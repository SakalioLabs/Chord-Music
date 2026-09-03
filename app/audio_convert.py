"""把解码得到的 16-bit 立体声 PCM 转换为输出设备的**原生格式**。

为什么需要这一层
================
解码层统一输出 44.1/48k 等源采样率的 16-bit 立体声 PCM，但声卡（尤其廉价 USB
声卡）的原生混合格式常常是 48kHz / Float32。若直接把 Int16/源采样率交给
QAudioSink、依赖后端或驱动做转换，部分驱动会出现“只有滋滋噪声、没有歌声”的问题。
因此这里在内存里一次性完成：

1. 重采样到设备采样率（线性插值，向量化，保持总时长不变）；
2. 声道适配（设备为单声道时下混）；
3. 样本格式转换（Float32 / Int32 / Int16）。

输出字节可直接喂给与 ``out_format`` 完全一致的 QAudioSink。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PySide6.QtMultimedia import QAudioFormat, QMediaDevices


def pick_output_format(device=None) -> QAudioFormat:
    """选取输出设备的原生（首选）格式；无效时回退到最通用的 48k/立体声/Float。"""
    dev = device or QMediaDevices.defaultAudioOutput()
    fmt = dev.preferredFormat()
    if not fmt.isValid() or fmt.sampleRate() <= 0:
        fmt = QAudioFormat()
        fmt.setSampleRate(48000)
        fmt.setChannelCount(2)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
    # 只处理 1/2 声道输出；异常声道数统一为立体声。
    if fmt.channelCount() not in (1, 2):
        fmt.setChannelCount(2)
    return fmt


def _resample_stereo(x: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    """线性插值重采样，``x`` 形状 (frames, channels)，float32，时长保持不变。"""
    if dst_sr <= 0 or src_sr == dst_sr:
        return x
    n = x.shape[0]
    dst_n = max(1, int(round(n * dst_sr / src_sr)))
    pos = np.arange(dst_n, dtype=np.float64) * src_sr / dst_sr
    i0 = np.floor(pos).astype(np.int64)
    i0 = np.clip(i0, 0, n - 1)
    i1 = np.minimum(i0 + 1, n - 1)
    frac = (pos - i0).astype(np.float32)[:, None]
    return x[i0] * (1.0 - frac) + x[i1] * frac


@dataclass
class DevicePCM:
    """转换后、与 :attr:`fmt` 匹配的 PCM 数据。"""

    data: bytes
    fmt: QAudioFormat
    n_frames: int

    @property
    def bytes_per_second(self) -> int:
        # bytesPerFrame 已包含声道数与每样本字节数。
        return self.fmt.sampleRate() * self.fmt.bytesPerFrame()

    @property
    def duration_ms(self) -> int:
        sr = self.fmt.sampleRate()
        return round(self.n_frames * 1000 / sr) if sr else 0


def convert_to_device(pcm_int16: bytes, src_sr: int,
                      out_fmt: QAudioFormat) -> DevicePCM:
    """把 16-bit 交错立体声 PCM 转成 ``out_fmt`` 指定的设备原生 PCM。"""
    # 解码层保证输出立体声（每帧 2 个 int16）。
    x = np.frombuffer(pcm_int16, dtype="<i2").reshape(-1, 2).astype(np.float32)

    dst_sr = out_fmt.sampleRate()
    y = _resample_stereo(x, src_sr, dst_sr)

    ch = out_fmt.channelCount()
    if ch == 1:
        y = y.mean(axis=1, keepdims=True)

    sample_fmt = out_fmt.sampleFormat()
    if sample_fmt == QAudioFormat.SampleFormat.Float:
        # 归一化到 [-1, 1] 的 32-bit float（小端）。
        out = (y / 32768.0).astype("<f4")
    elif sample_fmt == QAudioFormat.SampleFormat.Int32:
        # int16 放到 int32 高 16 位，保持满量程。
        clipped = np.clip(np.rint(y), -32768, 32767).astype(np.int32)
        out = (clipped << 16).astype("<i4")
    else:
        # 设备若偏好 Int16/UInt8 等，统一按 Int16 输出（并把格式固定为 Int16）。
        out_fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        out = np.clip(np.rint(y), -32768, 32767).astype("<i2")

    data = np.ascontiguousarray(out).tobytes()
    return DevicePCM(data=data, fmt=out_fmt, n_frames=y.shape[0])
