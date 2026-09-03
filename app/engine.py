"""播放引擎。

基于 ``QAudioSink`` 的 **pull 模式**：自定义一个只读 :class:`PCMSource`，
QAudioSink 会按声卡需要的节奏不断回调其 ``readData`` 从内存 PCM 中取数。
相比自己用定时器 push，这种方式由 Qt 音频后端驱动，时序更稳、不会 underrun。

对外暴露与具体 Qt API 无关的简单状态机：Stopped / Playing / Paused，
以及播放位置、seek、音量、自动播完信号等。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QIODevice, QObject, QTimer, QVariantAnimation, Signal
from PySide6.QtMultimedia import QAudio, QAudioFormat, QAudioSink, QMediaDevices

from .decoder import DecodedAudio
from .audio_convert import DevicePCM, convert_to_device, pick_output_format

# 暂停缓出 / 恢复淡入时长（毫秒）。缓出让声音自然收束而非硬切，淡入避免恢复瞬间的爆音。
_FADE_OUT_MS = 220
_FADE_IN_MS = 170


def _enum(obj, *names):
    """兼容不同 PySide6 版本的枚举访问路径。"""
    for name in names:
        cur = obj
        ok = True
        for part in name.split("."):
            cur = getattr(cur, part, None)
            if cur is None:
                ok = False
                break
        if ok:
            return cur
    raise AttributeError(f"找不到枚举: {names}")


# 跨版本枚举常量
_INT16 = _enum(QAudioFormat, "SampleFormat.Int16", "Int16")
_ACTIVE = _enum(QAudio, "State.ActiveState", "ActiveState")
_SUSPENDED = _enum(QAudio, "State.SuspendedState", "SuspendedState")
_IDLE = _enum(QAudio, "State.IdleState", "IdleState")
_STOPPED = _enum(QAudio, "State.StoppedState", "StoppedState")


class PCMSource(QIODevice):
    """把内存中的整段 PCM 字节包装成 QAudioSink 可拉取的只读设备。"""

    def __init__(self, pcm: bytes, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._pcm = pcm
        self._pos = 0
        # pull 模式要求以只读方式打开。
        self.open(QIODevice.OpenModeFlag.ReadOnly)

    # --- 位置控制（seek 直接改这里，下一次 readData 立即生效） ---
    def set_position(self, byte_pos: int) -> None:
        self._pos = max(0, min(byte_pos, len(self._pcm)))

    def position(self) -> int:
        return self._pos

    def is_at_end(self) -> bool:
        return self._pos >= len(self._pcm)

    # pull 模式下 QAudioSink 依靠下面三个方法判断是否还有数据可读，必须重写。
    def bytesAvailable(self) -> int:  # noqa: N802 - Qt 命名
        return len(self._pcm) - self._pos

    def size(self) -> int:  # noqa: N802 - Qt 命名
        return len(self._pcm)

    def isSequential(self) -> bool:  # noqa: N802 - Qt 命名
        return False

    # --- QIODevice 必须实现的两个虚函数 ---
    def readData(self, maxlen: int) -> bytes:
        if maxlen <= 0 or self._pos >= len(self._pcm):
            return b""
        end = min(self._pos + maxlen, len(self._pcm))
        chunk = self._pcm[self._pos:end]
        self._pos = end
        return chunk

    def writeData(self, data) -> int:  # noqa: N802 - Qt 命名
        return 0

    def atEnd(self) -> bool:  # noqa: N802 - Qt 命名
        return self.is_at_end()


class PlaybackEngine(QObject):
    """内存 PCM 播放引擎。"""

    # 状态："stopped" / "playing" / "paused"
    stateChanged = Signal(str)
    # 当前播放位置（毫秒）
    positionChanged = Signal(int)
    # 当前曲目自然播放结束
    ended = Signal()

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._sink: Optional[QAudioSink] = None
        self._source: Optional[PCMSource] = None
        self._state = "stopped"
        self._bps = 0  # bytes per second
        self._total_ms = 0
        self._volume = 1.0
        # 播放增益（用于暂停缓出/恢复淡入），实际输出音量 = _volume * _gain。
        self._gain = 1.0
        self._fade: Optional[QVariantAnimation] = None
        self._pausing = False  # 正在缓出、缓出结束后再真正 suspend
        # 输出设备原生格式（主线程选取一次）；解码线程据此预转换，避免驱动转换出噪声。
        self._out_fmt = pick_output_format()

        # 以固定频率刷新播放位置并检测自然结束。
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._poll)

    # ----------------------------- 加载 -----------------------------
    @property
    def output_format(self) -> "QAudioFormat":
        """输出设备的原生格式，供后台解码线程预转换。"""
        return self._out_fmt

    def load(self, audio: DecodedAudio, device_pcm: Optional[DevicePCM] = None) -> None:
        """载入已解码音频；device_pcm 为后台线程按设备格式预转换的结果。

        若未提供（如测试直接调用），在主线程兜底转换一次。
        """
        self._teardown()

        if device_pcm is None:
            device_pcm = convert_to_device(audio.pcm, audio.sample_rate, self._out_fmt)

        # 新曲目以满增益开始（切歌不做淡入，避免开头被压弱）。
        self._pausing = False
        self._gain = 1.0
        device = QMediaDevices.defaultAudioOutput()
        self._sink = QAudioSink(device, device_pcm.fmt, self)
        self._apply_volume()
        self._sink.stateChanged.connect(self._on_sink_state)

        self._source = PCMSource(device_pcm.data, self)
        self._bps = device_pcm.bytes_per_second
        self._total_ms = audio.duration_ms
        self._set_state("stopped")
        self.positionChanged.emit(0)

    # --------------------------- 播放控制 ---------------------------
    def play(self) -> None:
        if self._sink is None or self._source is None:
            return
        sink_state = self._sink.state()
        fade_in = False
        if sink_state == _SUSPENDED:
            # 从暂停恢复：先 resume，再把增益从当前值（缓出后≈0）淡入回 1。
            self._sink.resume()
            fade_in = True
        elif sink_state != _ACTIVE:
            # 若已到末尾再按播放，则从头开始；新起播直接满增益，不做淡入。
            if self._source.is_at_end():
                self._source.set_position(0)
            self._gain = 1.0
            self._apply_volume()
            self._sink.start(self._source)
        elif self._pausing:
            # 缓出尚未结束就再次按播放：撤销挂起的暂停，反向淡入回满。
            self._pausing = False
            fade_in = True
        self._timer.start()
        if fade_in:
            self._start_fade(1.0, _FADE_IN_MS)

    def pause(self) -> None:
        if self._sink is None:
            return
        if self._pausing:
            return  # 已在缓出中，忽略重复点击
        if self._sink.state() == _ACTIVE:
            # 先让声音在 ~220ms 内线性收束到 0，再真正挂起设备，避免硬切的“咔”声。
            self._pausing = True
            self._start_fade(0.0, _FADE_OUT_MS, self._finish_pause)
        else:
            self._finish_pause()

    def _finish_pause(self) -> None:
        """缓出结束回调：真正挂起音频并进入 paused。"""
        self._pausing = False
        if self._sink is not None and self._sink.state() == _ACTIVE:
            self._sink.suspend()
        self._timer.stop()
        self._set_state("paused")

    def _start_fade(self, target: float, duration: int, on_finish=None) -> None:
        """把播放增益从当前值插值到 target（线性），结束后回调 on_finish。"""
        if self._fade is not None:
            try:
                self._fade.stop()
            except Exception:  # noqa: BLE001
                pass
            self._fade.deleteLater()
            self._fade = None
        anim = QVariantAnimation(self)
        anim.setStartValue(float(self._gain))
        anim.setEndValue(float(target))
        anim.setDuration(max(1, int(duration)))
        anim.valueChanged.connect(self._on_fade_value)
        if on_finish is not None:
            anim.finished.connect(on_finish)
        anim.start()
        self._fade = anim

    def _on_fade_value(self, value) -> None:
        self._set_gain(float(value))

    def _set_gain(self, gain: float) -> None:
        self._gain = max(0.0, min(1.0, gain))
        self._apply_volume()

    def _apply_volume(self) -> None:
        if self._sink is not None:
            self._sink.setVolume(max(0.0, min(1.0, self._volume * self._gain)))

    def toggle(self) -> None:
        if self._state == "playing" and not self._pausing:
            self.pause()
        else:
            self.play()

    def stop(self) -> None:
        self._cancel_fade()
        self._pausing = False
        self._gain = 1.0
        if self._sink is not None:
            self._sink.stop()
            self._apply_volume()
        if self._source is not None:
            self._source.set_position(0)
        self._timer.stop()
        self._set_state("stopped")
        self.positionChanged.emit(0)

    def seek_ms(self, ms: int) -> None:
        """跳转到指定毫秒位置（播放/暂停状态下均可）。"""
        if self._source is None or self._bps <= 0:
            return
        ms = max(0, min(ms, self._total_ms))
        self._source.set_position(ms * self._bps // 1000)
        self.positionChanged.emit(ms)

    def set_volume(self, volume: float) -> None:
        self._volume = max(0.0, min(1.0, volume))
        self._apply_volume()

    def _cancel_fade(self) -> None:
        if self._fade is not None:
            try:
                self._fade.stop()
            except Exception:  # noqa: BLE001
                pass
            self._fade.deleteLater()
            self._fade = None

    # ----------------------------- 查询 -----------------------------
    @property
    def state(self) -> str:
        return self._state

    @property
    def total_ms(self) -> int:
        return self._total_ms

    def current_ms(self) -> int:
        if self._source is None or self._bps <= 0:
            return 0
        return round(self._source.position() * 1000 / self._bps)

    # ----------------------------- 内部 -----------------------------
    def _on_sink_state(self, state) -> None:
        if state == _ACTIVE:
            self._set_state("playing")
            self._timer.start()
        elif state == _SUSPENDED:
            self._set_state("paused")
        elif state in (_IDLE, _STOPPED):
            # Idle：数据耗尽（自然播完）；Stopped：主动 stop。
            if state == _IDLE and self._source is not None and self._source.is_at_end():
                self._timer.stop()
                self._set_state("stopped")
                self.positionChanged.emit(self._total_ms)
                self.ended.emit()

    def _poll(self) -> None:
        if self._source is None:
            return
        self.positionChanged.emit(self.current_ms())
        # 双保险：部分后端不总会进入 Idle，这里主动判定结束。
        if self._source.is_at_end() and self._sink is not None:
            if self._sink.state() != _ACTIVE:
                self._timer.stop()
                if self._state != "stopped":
                    self._set_state("stopped")
                    self.positionChanged.emit(self._total_ms)
                    self.ended.emit()

    def _set_state(self, state: str) -> None:
        if self._state != state:
            self._state = state
            self.stateChanged.emit(state)

    def _teardown(self) -> None:
        self._cancel_fade()
        self._pausing = False
        self._gain = 1.0
        self._timer.stop()
        if self._sink is not None:
            try:
                self._sink.stop()
            except Exception:  # noqa: BLE001
                pass
            self._sink.deleteLater()
            self._sink = None
        if self._source is not None:
            self._source.deleteLater()
            self._source = None
