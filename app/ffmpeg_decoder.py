"""基于 PyAV（捆绑 FFmpeg/libav）的通用音频解码通道。

为什么需要它
============
标准库 :mod:`wave` 与 ``soundfile/libsndfile`` 都**完全相信 WAV 文件头**。但存在
一类 “DTS-WAV”（DTS-CD）文件：扩展名是 ``.wav``、文件头也写成 44.1kHz/16bit/双声道
PCM，data 块里装的却是 DTS/DTS-ES 6.1 声道的**压缩码流**（常见 14-bit 封装，样本值
恰好落在 ±8192）。若按 PCM 直接喂给声卡，等于把压缩码流当采样值播放，结果就是连续
“滋滋滋”噪声、没有人声。

FFmpeg 会扫描码流同步头、识别真实编码（``dca`` / ``mp3`` / ``aac`` …），这里用 PyAV
完成：真实编码探测 → 解码 → 由 libswresample 专业下混为立体声 → 输出统一的 16-bit
交错 PCM，与 :class:`~app.decoder.DecodedAudio` 对齐。

PyAV 为延迟导入：未安装时 :func:`available` 返回 False，解码层自动回退到原有路径，
不影响纯 PCM WAV / FLAC 的播放。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

try:  # pragma: no cover - 取决于运行环境
    import av as _av
except Exception:  # noqa: BLE001
    _av = None


# 编码名 -> 界面友好名
_CODEC_NAMES = {
    "dca": "DTS",
    "dts": "DTS",
    "mp3float": "MP3",
    "mp3": "MP3",
    "mp3adufloat": "MP3",
    "aac": "AAC",
    "ac3": "AC-3",
    "eac3": "E-AC-3",
    "vorbis": "OGG Vorbis",
    "opus": "Opus",
    "ape": "APE",
    "wmapro": "WMA",
    "wmav2": "WMA",
    "wmav1": "WMA",
    "flac": "FLAC",
    "alac": "ALAC",
    "tta": "TTA",
    "shorten": "Shorten",
    "midi": "MIDI",
}


def available() -> bool:
    """PyAV 是否可用。"""
    return _av is not None


@dataclass
class CodecInfo:
    """探测到的真实音频流信息。"""

    codec: str
    sample_rate: int
    channels: int
    layout: str

    @property
    def is_plain_pcm(self) -> bool:
        """是否为未压缩 PCM（这类交给标准库/soundfile 路径处理即可）。"""
        return self.codec.startswith("pcm_")


def probe_codec(path: str) -> Optional[CodecInfo]:
    """只读取容器头、识别音频流的真实编码（不做整曲解码）。

    无法识别或缺少 PyAV 时返回 None。
    """
    if _av is None:
        return None
    container = None
    try:
        # 部分中文标签是 GBK，强制宽松解码，避免 av.open 在元数据阶段抛 UnicodeDecodeError。
        container = _av.open(path, metadata_encoding="gb18030", metadata_errors="replace")
        astream = next((s for s in container.streams if s.type == "audio"), None)
        if astream is None:
            return None
        ctx = astream.codec_context
        return CodecInfo(
            codec=(ctx.name or "").lower(),
            sample_rate=int(ctx.rate or 0),
            channels=int(ctx.channels or 0),
            layout=_layout_name(ctx),
        )
    except Exception:  # noqa: BLE001
        return None
    finally:
        if container is not None:
            try:
                container.close()
            except Exception:  # noqa: BLE001
                pass


def _display_name(codec: str) -> str:
    return _CODEC_NAMES.get(codec, codec.upper())


def _layout_name(ctx) -> str:
    """取干净的声道布局名（如 '6.1'/'stereo'），避免 str() 带出 <av.AudioLayout ...>。"""
    lay = getattr(ctx, "layout", None)
    name = getattr(lay, "name", None)
    if name:
        return name
    ch = getattr(ctx, "channels", 0) or 0
    return f"{ch}声道" if ch else ""


def probe_duration_ms(path: str) -> Optional[int]:
    """从容器/流元数据读取时长（毫秒），不做整曲解码；失败返回 None。"""
    if _av is None:
        return None
    container = None
    try:
        container = _av.open(path, metadata_encoding="gb18030", metadata_errors="replace")
        astream = next((s for s in container.streams if s.type == "audio"), None)
        us = None
        if astream is not None and astream.duration is not None and astream.time_base:
            us = int(astream.duration * astream.time_base * 1_000_000)
        elif container.duration is not None:
            us = int(container.duration)  # 容器 duration 单位即微秒
        if us is None or us <= 0:
            return None
        return round(us / 1000)
    except Exception:  # noqa: BLE001
        return None
    finally:
        if container is not None:
            try:
                container.close()
            except Exception:  # noqa: BLE001
                pass


def decode_with_ffmpeg(path: str, info: Optional[CodecInfo] = None):
    """用 FFmpeg 解码任意压缩/伪装编码，输出 16-bit 立体声交错 PCM。

    返回 :class:`~app.decoder.DecodedAudio`（延迟导入以避免循环依赖）。
    """
    if _av is None:
        raise RuntimeError("解码该文件需要 PyAV（捆绑 FFmpeg），请先执行: pip install av")

    from .decoder import DecodedAudio  # 延迟导入避免循环依赖

    if info is None:
        info = probe_codec(path)

    container = _av.open(path, metadata_encoding="gb18030", metadata_errors="replace")
    try:
        astream = next(s for s in container.streams if s.type == "audio")
        ctx = astream.codec_context
        src_rate = int(ctx.rate)
        src_ch = int(ctx.channels)
        layout = _layout_name(ctx)
        codec = (ctx.name or "").lower()

        # 保持源采样率，仅做 格式->s16 planar、布局->stereo 的专业下混。
        resampler = _av.AudioResampler(format="s16p", layout="stereo", rate=src_rate)
        chunks = []
        for packet in container.demux(astream):
            for frame in packet.decode():
                for rf in resampler.resample(frame):
                    chunks.append(np.asarray(rf.to_ndarray()).T)  # (frames, 2) int16
        # 冲刷重采样器内部残留样本。
        try:
            for rf in resampler.resample(None):
                chunks.append(np.asarray(rf.to_ndarray()).T)
        except Exception:  # noqa: BLE001
            pass
    finally:
        container.close()

    if not chunks:
        raise RuntimeError("FFmpeg 未解码出任何音频帧")

    stereo = np.ascontiguousarray(np.concatenate(chunks), dtype=np.int16)
    pcm = stereo.reshape(-1).tobytes()

    name = _display_name(codec)
    khz = f"{src_rate // 1000}.{src_rate % 1000:03d}kHz"
    # 多声道才标注下混；立体声直通不额外说明。
    note = f" {layout or (str(src_ch) + '声道')}→立体声" if src_ch != 2 else ""
    return DecodedAudio(
        pcm=pcm,
        sample_rate=src_rate,
        channels=2,
        n_frames=stereo.shape[0],
        source_format=f"{name} {khz}{note}",
    )
