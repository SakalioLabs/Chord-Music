"""音频解码层。

把 WAV（基础 PCM）与 FLAC（无损压缩）统一解码成 16-bit、小端、交错排列
(interleaved) 的原始 PCM 字节流，供 QAudioSink 直接播放。

设计说明
========
* WAV 优先使用 Python 标准库 ``wave`` 解析 RIFF 容器，手工处理 8/16/24/32-bit
  的位深归一化，体现"基础 PCM 解码"；遇到标准库不支持的格式（如 32-bit float
  WAV）时自动回退到 soundfile/libsndfile。
* FLAC 为无损压缩格式（Rice 熵编码 + 线性预测），不适合手写解压器，因此通过
  ``soundfile``（捆绑 libsndfile）解码，这是工程上稳健的做法。
* 无论输入位深/格式如何，输出统一为有符号 16-bit PCM（CD 规格），从而让播放
  引擎只需处理一种 QAudioFormat。
"""

from __future__ import annotations

import os
import wave
from dataclasses import dataclass

import numpy as np

from . import ffmpeg_decoder

# soundfile 为延迟导入，仅在解码 FLAC / WAV 回退路径时才需要，
# 这样即使缺少该依赖，纯 16-bit WAV 仍可通过标准库正常播放。
try:  # pragma: no cover - 取决于运行环境
    import soundfile as _sf
except Exception:  # noqa: BLE001
    _sf = None


# 基础 WAV/FLAC，加上 FFmpeg(PyAV) 通道可稳健覆盖的常见压缩格式。
SUPPORTED_EXTS = (
    ".wav", ".flac",
    ".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus",
    ".ape", ".wma", ".tta",
)


@dataclass
class DecodedAudio:
    """解码后的原始音频。

    Attributes
    ----------
    pcm:
        16-bit、小端、交错排列的 PCM 字节。
    sample_rate:
        采样率，单位 Hz。
    channels:
        声道数（1 = 单声道，2 = 立体声）。
    n_frames:
        每声道的采样帧数。
    source_format:
        来源格式描述，用于界面展示。
    """

    pcm: bytes
    sample_rate: int
    channels: int
    n_frames: int
    source_format: str = ""

    @property
    def sample_width(self) -> int:
        """输出位宽固定为 2 字节（16-bit）。"""
        return 2

    @property
    def duration_ms(self) -> int:
        """总时长，单位毫秒。"""
        if self.sample_rate <= 0:
            return 0
        return round(self.n_frames * 1000 / self.sample_rate)

    @property
    def bytes_per_second(self) -> int:
        """每秒 PCM 占用字节数，用于进度/seek 换算。"""
        return self.sample_rate * self.channels * self.sample_width


def _pcm_to_int16(raw: bytes, sampwidth: int, channels: int) -> np.ndarray:
    """把任意位深的 PCM 字节归一化为 int16 的一维 numpy 数组。

    ``raw`` 为交错排列的小端 PCM；返回形状 (frames*channels,) 的 int16 数组。
    """
    if sampwidth == 2:
        # 16-bit signed little-endian：x86 原生格式，直接解释即可。
        return np.frombuffer(raw, dtype="<i2").astype(np.int16, copy=False)

    if sampwidth == 1:
        # 8-bit WAV 规范为无符号，中点 128；放大到 16-bit 动态范围。
        u8 = np.frombuffer(raw, dtype=np.uint8).astype(np.int32)
        return ((u8 - 128) << 8).astype(np.int16)

    if sampwidth == 4:
        # 32-bit signed：右移 16 位降到 16-bit。
        i32 = np.frombuffer(raw, dtype="<i4").astype(np.int32)
        return (i32 >> 16).astype(np.int16)

    if sampwidth == 3:
        # 24-bit signed little-endian：手工还原为整数后右移 8 位。
        b = np.frombuffer(raw, dtype=np.uint8)
        if b.size % 3 != 0:
            raise ValueError("24-bit PCM 数据长度不是 3 的整数倍")
        b = b.reshape(-1, 3).astype(np.int32)
        i24 = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        # 处理符号位（第 23 位）。
        i24 = np.where(i24 >= 1 << 23, i24 - (1 << 24), i24)
        return (i24 >> 8).astype(np.int16)

    raise ValueError(f"暂不支持的 PCM 位深: {sampwidth * 8}-bit")


# 常见声道布局名称（WAV / FLAC 的声道顺序遵循 SMPTE/WAVEFORMATEXTENSIBLE 约定）。
_CHANNEL_LABEL = {1: "单声道", 2: "立体声", 6: "5.1环绕", 8: "7.1环绕"}
_SQRT2_HALF = 0.7071  # -3dB，中央 / 环绕声道下混到立体声的标准增益


def channel_label(n: int) -> str:
    return _CHANNEL_LABEL.get(n, f"{n}声道")


def _downmix_to_stereo(data: np.ndarray) -> np.ndarray:
    """把 ``(frames, n)`` 的 int16 采样统一为 ``(frames*2,)`` 交错立体声 int16。

    * 1 声道（单声道）：复制到左右声道；
    * 2 声道（立体声）：原样返回；
    * 6 声道（5.1：L/R/C/LFE/BL/BR）与 8 声道（7.1）：按标准系数下混，保留中央与环绕；
    * 其它声道数：前两声道为主，其余声道作为环境声平均后等量并入两侧。
    全程在 float32 域求和并限幅回 int16，避免多声道叠加时整数溢出爆音。
    """
    n = data.shape[1]
    if n == 2:
        return np.ascontiguousarray(data, dtype=np.int16).reshape(-1)

    f = data.astype(np.float32)
    if n == 1:
        left = f[:, 0]
        right = f[:, 0]
    elif n == 6:  # L, R, C, LFE, BL, BR
        left = f[:, 0] + _SQRT2_HALF * f[:, 2] + _SQRT2_HALF * f[:, 4] + 0.5 * f[:, 3]
        right = f[:, 1] + _SQRT2_HALF * f[:, 2] + _SQRT2_HALF * f[:, 5] + 0.5 * f[:, 3]
    elif n == 8:  # L, R, C, LFE, BL, BR, SL, SR
        left = (f[:, 0] + _SQRT2_HALF * f[:, 2]
                + 0.5 * (f[:, 4] + f[:, 6]) + 0.5 * f[:, 3])
        right = (f[:, 1] + _SQRT2_HALF * f[:, 2]
                 + 0.5 * (f[:, 5] + f[:, 7]) + 0.5 * f[:, 3])
    else:  # 通用回退：前两声道为主，其余作为环境声
        left = f[:, 0].copy()
        right = f[:, 1].copy() if n >= 2 else f[:, 0].copy()
        if n > 2:
            ambient = f[:, 2:].mean(axis=1) * _SQRT2_HALF
            left += ambient
            right += ambient

    out = np.stack((left, right), axis=1)
    np.clip(out, -32768, 32767, out=out)
    return out.astype(np.int16).reshape(-1)


def _decode_wav(path: str) -> DecodedAudio:
    """使用标准库 wave 解码 PCM WAV。"""
    with wave.open(path, "rb") as wf:
        nchannels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        nframes = wf.getnframes()
        comp = wf.getcomptype()
        if comp != "NONE":
            raise wave.Error(f"仅支持未压缩 PCM WAV，当前压缩类型: {comp}")
        raw = wf.readframes(nframes)

    samples = _pcm_to_int16(raw, sampwidth, nchannels)
    frames = samples.reshape(-1, nchannels)
    pcm = _downmix_to_stereo(frames).tobytes()
    # 常规立体声不额外标注（默认输出就是立体声），仅多声道下混时注明来源布局。
    note = f" {channel_label(nchannels)}→立体声" if nchannels != 2 else ""
    return DecodedAudio(
        pcm=pcm,
        sample_rate=framerate,
        channels=2,  # 输出统一为立体声，保证任意声卡都能播放
        n_frames=frames.shape[0],
        source_format=(f"WAV {sampwidth * 8}-bit "
                       f"{framerate // 1000}.{framerate % 1000:03d}kHz{note}"),
    )


def _decode_with_soundfile(path: str, tag: str) -> DecodedAudio:
    """使用 soundfile/libsndfile 解码（FLAC 主路径，WAV 回退路径）。"""
    if _sf is None:
        raise RuntimeError(
            "解码该文件需要 soundfile 依赖（含 libsndfile）。"
            "请先执行: pip install soundfile numpy"
        )
    # 直接请求 int16，得到形状 (frames, channels) 的交错数组。
    data, framerate = _sf.read(path, dtype="int16", always_2d=True)
    nframes, nchannels = data.shape
    pcm = _downmix_to_stereo(np.ascontiguousarray(data, dtype=np.int16)).tobytes()
    note = f" {channel_label(nchannels)}→立体声" if nchannels != 2 else ""
    subtype = f"{tag}{note}"
    try:
        info = _sf.info(path)
        subtype = f"{tag} {info.subtype}{note}"
    except Exception:  # noqa: BLE001
        pass
    return DecodedAudio(
        pcm=pcm,
        sample_rate=framerate,
        channels=2,  # 统一输出立体声
        n_frames=nframes,
        source_format=subtype,
    )


def decode(path: str) -> DecodedAudio:
    """根据扩展名分派解码器，返回统一的 :class:`DecodedAudio`。

    Raises
    ------
    FileNotFoundError
        文件不存在。
    ValueError
        不支持的扩展名。
    RuntimeError / wave.Error
        文件损坏或缺少对应解码后端。
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"音频文件不存在: {path}")

    ext = os.path.splitext(path)[1].lower()
    if ext not in SUPPORTED_EXTS:
        raise ValueError(
            f"不支持的格式: {ext}；当前支持 {', '.join(sorted(set(SUPPORTED_EXTS))).upper()}"
        )

    # 先用 FFmpeg 探测**真实编码**：WAV 文件头可能伪装成 PCM，里面却是 DTS 等压缩码流
    # （DTS-WAV）。这类文件以及 MP3/AAC 等压缩格式必须走真正的解码器，不能当 PCM 直读。
    info = ffmpeg_decoder.probe_codec(path) if ffmpeg_decoder.available() else None
    if info is not None and not info.is_plain_pcm and info.codec != "flac":
        return ffmpeg_decoder.decode_with_ffmpeg(path, info)

    if ext == ".wav":
        try:
            return _decode_wav(path)
        except (wave.Error, ValueError, EOFError):
            # 标准库无法处理（如 32-bit float WAV）时回退到 libsndfile。
            try:
                return _decode_with_soundfile(path, "WAV")
            except Exception:  # noqa: BLE001
                # soundfile 也失败（典型：伪装成 WAV 的压缩码流）时，最后交给 FFmpeg。
                if ffmpeg_decoder.available():
                    return ffmpeg_decoder.decode_with_ffmpeg(path, info)
                raise

    # FLAC 及其它无损/压缩格式优先 soundfile，失败再回退 FFmpeg。
    try:
        return _decode_with_soundfile(path, ext.lstrip(".").upper())
    except Exception:  # noqa: BLE001
        if ffmpeg_decoder.available():
            return ffmpeg_decoder.decode_with_ffmpeg(path, info)
        raise


def probe_duration_ms(path: str) -> int:
    """只读取时长（不解码全部 PCM），用于构建列表，速度快。"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".wav":
        try:
            with wave.open(path, "rb") as wf:
                sr, n = wf.getframerate(), wf.getnframes()
                return round(n * 1000 / sr) if sr else 0
        except Exception:  # noqa: BLE001
            pass
    if _sf is not None:
        try:
            info = _sf.info(path)
            return round(info.frames * 1000 / info.samplerate)
        except Exception:  # noqa: BLE001
            pass
    # 压缩格式 / 伪装 WAV：用容器元数据给时长。
    if ffmpeg_decoder.available():
        ms = ffmpeg_decoder.probe_duration_ms(path)
        if ms:
            return ms
    return 0
