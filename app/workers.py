"""后台线程：把耗时的目录扫描 / 元数据读取 / 音频解码移出 UI 线程。

主线程（GUI 线程）负责事件循环与 QAudioSink 的状态管理；一旦在主线程同步做大量
文件 IO、元数据解析或整曲解码，事件循环被阻塞，就会出现“界面卡一下、声音也卡”。
这里用两类后台任务：

* :class:`ImportWorker`：放在 :class:`QThread` 中运行，批量扫描并解析整个导入列表，
  通过进度信号汇报，结束后一次性把结果回传主线程。
* :class:`DecodeTask`：放到 :class:`QThreadPool`，单曲解码，完成后经信号桥回主线程，
  并用 token 丢弃过期结果，避免快速切歌时“串歌”。
"""

from __future__ import annotations

import os
from typing import Callable, Iterable, List, Optional, Tuple

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from .decoder import SUPPORTED_EXTS, decode, probe_duration_ms
from .audio_convert import convert_to_device
from .metadata import TrackMeta, read_metadata

# 一条导入结果：(绝对路径, 显示标题, 扩展名, 时长ms, 元数据)
Record = Tuple[str, str, str, int, TrackMeta]


def scan_folder(folder: str) -> List[str]:
    """递归收集文件夹下所有受支持的音频（本身为纯 IO，也在后台线程调用）。"""
    found: List[str] = []
    for root, _dirs, names in os.walk(folder):
        for name in names:
            if os.path.splitext(name)[1].lower() in SUPPORTED_EXTS:
                found.append(os.path.join(root, name))
    found.sort()
    return found


def build_records(paths: Iterable[str], known: set,
                  on_progress: Optional[Callable[[int, int], None]] = None,
                  should_continue: Optional[Callable[[], bool]] = None) -> List[Record]:
    """纯函数：解析路径列表为导入记录（耗时部分），可在任意线程运行。

    should_continue 返回 False 时提前中止（用于窗口关闭时中断大批量导入）。
    """
    paths = list(paths)
    total = len(paths)
    records: List[Record] = []
    for i, raw in enumerate(paths):
        if should_continue is not None and not should_continue():
            break
        path = os.path.normpath(raw)
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        if ext in ("wav", "flac") and path not in known:
            meta = read_metadata(path)
            fallback = os.path.splitext(os.path.basename(path))[0]
            records.append((path, meta.title or fallback, ext,
                            probe_duration_ms(path), meta))
        if on_progress is not None:
            on_progress(i + 1, total)
    return records


class ImportWorker(QObject):
    """在 QThread 中运行的批量导入任务，支持直接给目录或文件列表。"""

    progress = Signal(int, int)   # 已处理数, 总数
    finished = Signal(list)      # List[Record]

    def __init__(self, known: set, paths: Optional[List[str]] = None,
                 folder: Optional[str] = None, parent=None):
        super().__init__(parent)
        self._paths = paths
        self._folder = folder
        self._known = known

    @Slot()
    def run(self) -> None:
        try:
            if self._folder is not None:
                self._paths = scan_folder(self._folder)
            thread = self.thread()  # moveToThread 后即承载它的工作线程
            records = build_records(
                self._paths or [], self._known,
                lambda d, t: self.progress.emit(d, t),
                should_continue=lambda: not (thread is not None
                                             and thread.isInterruptionRequested()))
            self.finished.emit(records)
        except Exception as exc:  # noqa: BLE001  后台异常也要回传，避免线程静默死掉
            self.finished.emit([])
            print("[ImportWorker] 导入失败:", exc)


class DecodeSignalBridge(QObject):
    """解码结果跨线程回主线程的信号桥（主线程持有）。"""

    decoded = Signal(str, object, int)   # 路径, DecodedAudio, token
    failed = Signal(str, str, int)       # 路径, 错误信息, token


class DecodeTask(QRunnable):
    """线程池任务：解码单个文件，不触碰任何 UI / QObject 控件。"""

    def __init__(self, path: str, token: int, bridge: DecodeSignalBridge, out_fmt=None):
        super().__init__()
        self._path = path
        self._token = token
        self._bridge = bridge
        self._out_fmt = out_fmt
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            audio = decode(self._path)
            # 在后台线程一并完成到设备原生格式的重采样/转换，避免阻塞 UI。
            device_pcm = (convert_to_device(audio.pcm, audio.sample_rate, self._out_fmt)
                          if self._out_fmt is not None else None)
            self._bridge.decoded.emit(self._path, (audio, device_pcm), self._token)
        except Exception as exc:  # noqa: BLE001
            self._bridge.failed.emit(self._path, str(exc), self._token)
