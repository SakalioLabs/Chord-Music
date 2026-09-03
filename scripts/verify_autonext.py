"""验证一首播完后自动切换到下一首（真实声卡，不显示窗口）。"""

import sys
import time
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import store as _store  # noqa: E402
import tempfile as _tf, pathlib as _pl  # noqa: E402
_store.app_data_dir = lambda: _pl.Path(_tf.mkdtemp(prefix="chord_test_"))
from app.main_window import MainWindow  # noqa: E402

app = QApplication(sys.argv)
win = MainWindow()
samples = sorted(str(p) for p in (ROOT / "samples").iterdir()
                 if p.suffix.lower() in (".wav", ".flac"))
win._ingest(samples)

win.play_path(win.local_order[0], win.local_order)
# 等首曲在后台线程解码完成并回投主线程
_deadline = time.time() + 5
while time.time() < _deadline and win.current_path is None:
    app.processEvents()
    time.sleep(0.01)
first = win.current_path
print("起始播放:", Path(first).name)

state = {"ok": False}


def check():
    second = win.current_path
    moved = second != first
    state["ok"] = moved and win.engine.state == "playing"
    print(f"2.8s 后: 当前={Path(second).name}  已切歌={moved}  状态={win.engine.state}")
    print("自动切歌:", "通过 ✅" if state["ok"] else "失败 ❌")
    app.quit()


QTimer.singleShot(2800, check)
QTimer.singleShot(4500, app.quit)  # 兜底
app.exec()
sys.exit(0 if state["ok"] else 1)
