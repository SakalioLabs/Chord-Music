"""回归：任务栏内嵌歌词——主窗口对 TaskbarLyrics 的驱动、随进度换行、
无同步歌词退化为曲名、关闭隐藏与退出回收。用 Fake 替身避免离屏下触碰真实任务栏。"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

import app.main_window as mw  # noqa: E402
from app import store as _store  # noqa: E402
import tempfile as _tf, pathlib as _pl  # noqa: E402
_store.app_data_dir = lambda: _pl.Path(_tf.mkdtemp(prefix="chord_test_"))
from app.main_window import MainWindow  # noqa: E402


class FakeTaskbarLyrics:
    """记录主窗口对任务栏歌词窗的全部调用，替代真实 Win32 嵌入窗。"""

    def __init__(self):
        self.shown = False
        self.closed = False
        self.texts = []
        self.last = ""

    def is_supported(self):
        return True

    def show(self):
        self.shown = True
        return True

    def hide(self):
        self.shown = False

    def set_text(self, text):
        self.texts.append(text)
        self.last = text

    def reassert(self):
        pass

    def close(self):
        self.closed = True
        self.shown = False


mw.TaskbarLyrics = FakeTaskbarLyrics  # 注入替身

app = QApplication(sys.argv)
win = MainWindow()
results = []


def check(name, cond):
    results.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")


def pump_until(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)
        if pred():
            return True
    return pred()


tagged = str(ROOT / "samples" / "tone_tagged.flac")
win._ingest([tagged])
win.engine.play = lambda: None
win.play_path(tagged, win.local_order)
pump_until(lambda: win.current_path == tagged)

# 默认关闭、尚未创建嵌入窗
check("默认任务栏歌词关闭", not win._task_on and win.taskbar_lyrics is None)

# 开启：创建并显示嵌入窗、载入时间轴、force 刷出首行
win.btn_task_lyric.setChecked(True)
win.toggle_taskbar_lyrics()
tb = win.taskbar_lyrics
check("开启后创建并显示嵌入窗", win._task_on and isinstance(tb, FakeTaskbarLyrics) and tb.shown)
check("开启后载入 4 行时间轴", len(win._task_times) == 4 and len(win._task_lines) == 4)
check("开启即显示首行歌词", tb.last == win._task_lines[0])

# 随进度换行
win._update_taskbar_line(0)
check("0ms 选首行", tb.last == win._task_lines[0])
win._update_taskbar_line(win._task_times[-1] + 500)
check("末尾时间选末行", tb.last == win._task_lines[-1])
win._on_position(win._task_times[2])
check("_on_position 同步换行", tb.last == win._task_lines[2])

# 未换行不重绘（记录次数不增加）
before = len(tb.texts)
win._on_position(win._task_times[2] + 10)
check("同一行不重复重绘", len(tb.texts) == before)

# 关闭：隐藏嵌入窗但保留实例，便于再次开启
win.btn_task_lyric.setChecked(False)
win.toggle_taskbar_lyrics()
check("关闭后隐藏且状态复位", not win._task_on and not tb.shown)

# 再次开启：复用窗口并重新绑定当前曲
win.btn_task_lyric.setChecked(True)
win.toggle_taskbar_lyrics()
check("重新开启复用窗口并显示首行", win._task_on and tb.shown and tb.last == win._task_lines[0])

# 无同步歌词时退化为显示曲名
win._task_times, win._task_lines, win._task_idx = [], [], -2
win._task_placeholder = "示例曲名"
win._update_taskbar_line(12000, force=True)
check("无同步歌词退化为曲名", tb.last == "示例曲名")

# 平台不支持时 _ensure_taskbar_window 返回 False（不弹窗路径）
saved, mw.TaskbarLyrics = mw.TaskbarLyrics, None
win.taskbar_lyrics = None
check("平台不支持时优雅返回 False", win._ensure_taskbar_window() is False)
mw.TaskbarLyrics = saved
win.taskbar_lyrics = tb

# 退出主窗口时回收嵌入窗
win.close()
check("关闭主窗口时销毁嵌入窗", tb.closed)

ok = all(results)
print("\n总体结果:", "全部通过" if ok else f"存在失败 ({results.count(False)})")
sys.exit(0 if ok else 1)
