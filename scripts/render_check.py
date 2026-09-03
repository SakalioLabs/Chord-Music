"""真实平台渲染 + 声卡拉流验证：短暂启动、播放、截图、自动退出。"""

import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import theme  # noqa: E402
from app import store as _store  # noqa: E402
import tempfile as _tf, pathlib as _pl  # noqa: E402
_store.app_data_dir = lambda: _pl.Path(_tf.mkdtemp(prefix="chord_test_"))
from app.main_window import MainWindow  # noqa: E402

theme.configure_high_dpi()  # 必须在 QApplication 之前
app = QApplication(sys.argv)
theme.load_application_fonts()
app.setFont(theme.application_font(10))
app.setStyleSheet((ROOT / "app" / "style.qss").read_text(encoding="utf-8"))
win = MainWindow()
win.resize(1000, 640)
win.show()

shots = ROOT / "scripts"


def load_and_play():
    samples = sorted(str(p) for p in (ROOT / "samples").iterdir()
                     if p.suffix.lower() in (".wav", ".flac"))
    win._ingest(samples)
    win.toggle_liked(win.local_order[0])
    win.switch_page(2)
    win.play_path(next(p for p in samples if p.endswith(".flac")), win.local_order)


def shot(name):
    pix = win.grab()
    pix.save(str(shots / name))
    print(f"saved {name}  state={win.engine.state}  pos={win.engine.current_ms()}ms  "
          f"sink={None if win.engine._sink is None else win.engine._sink.state()}")


QTimer.singleShot(380, lambda: (win.switch_page(0), shot("shot_home.png")))
QTimer.singleShot(550, load_and_play)
QTimer.singleShot(1000, lambda: shot("shot_local.png"))
QTimer.singleShot(1350, app.quit)

sys.exit(app.exec())
