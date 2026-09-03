"""验证播放模式（列表循环/单曲循环/随机）、音量静音与快捷键注册。"""

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtGui import QShortcut  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import store as _store  # noqa: E402
import tempfile as _tf, pathlib as _pl  # noqa: E402
_store.app_data_dir = lambda: _pl.Path(_tf.mkdtemp(prefix="chord_test_"))
from app.main_window import MainWindow  # noqa: E402

app = QApplication(sys.argv)
win = MainWindow()
win.show()

samples = sorted(str(p) for p in (ROOT / "samples").glob("*") if p.suffix.lower() in (".wav", ".flac"))
win._ingest(samples)

results = []


def check(name, cond):
    results.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")


def wait_loaded(target=None, timeout=5.0):
    """等后台解码回投完成（_pending_path 清空），可选等到指定曲目成为当前曲。"""
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)
        if win._pending_path is None and (target is None or win.current_path == target):
            return True
    return False


# ---- 播放模式三态循环 ----
check("初始为列表循环", win.play_mode == "loop")
win.cycle_play_mode()
check("切换到单曲循环", win.play_mode == "single" and "单曲" in win.btn_mode.toolTip())
win.cycle_play_mode()
check("切换到随机播放", win.play_mode == "shuffle" and "随机" in win.btn_mode.toolTip())
win.cycle_play_mode()
check("回到列表循环", win.play_mode == "loop")

# ---- 音量 / 静音 ----
win.volume.setValue(50)
check("音量写入引擎不报错", win.volume.value() == 50)
win.volume.setValue(0)
check("0 音量显示静音图标", win.btn_mute._icon_name == "mute")
win.toggle_mute()
check("取消静音恢复上次音量", win.volume.value() == 50 and win.btn_mute._icon_name == "volume")

# ---- 自然播完的模式行为 ----
first = win.local_order[0]
win.play_path(first, win.local_order)
wait_loaded(first)
win.play_mode = "single"
win._on_ended()
wait_loaded(first)
check("单曲循环重播本曲", win.current_path == first)

win.play_mode = "shuffle"
seen = set()
for _ in range(12):
    win._on_ended()
    wait_loaded()
    seen.add(win.current_path)
check("随机播放始终落在队列内", all(p in win.queue for p in seen))

win.play_mode = "loop"
win.current_path = win.queue[-1]
win._on_ended()
wait_loaded(win.queue[0])
check("列表循环末尾回到首曲", win.current_path == win.queue[0])

# ---- 快捷键 ----
check("已注册不少于 5 个快捷键", len(win.findChildren(QShortcut)) >= 5)

ok = all(results)
print("\n总体结果:", "全部通过 ✅" if ok else f"存在失败 ❌ ({results.count(False)})")
sys.exit(0 if ok else 1)
