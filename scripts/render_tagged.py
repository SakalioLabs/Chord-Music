"""真实平台渲染：带封面/歌词曲目的列表态、音量浮层、正在播放详情。"""
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

theme.configure_high_dpi()
app = QApplication(sys.argv)
theme.load_application_fonts()
app.setFont(theme.application_font(10))
app.setStyleSheet((ROOT / "app" / "style.qss").read_text(encoding="utf-8"))
win = MainWindow()
win.resize(1040, 660)
win.show()
shots = ROOT / "scripts"
tagged = str(ROOT / "samples" / "tone_tagged.flac")


def save(widget, name):
    widget.grab().save(str(shots / name))
    print("saved", name)


QTimer.singleShot(350, lambda: win._ingest([tagged]))
QTimer.singleShot(600, lambda: win.play_path(tagged, win.local_order))
QTimer.singleShot(1000, lambda: save(win, "shot_tagged_list.png"))
QTimer.singleShot(1200, lambda: (win._open_volume_popup(), ))
QTimer.singleShot(1450, lambda: save(win.volume_popup, "shot_volume_popup.png"))
QTimer.singleShot(1600, lambda: (win.volume_popup.hide(), win.open_now_playing()))
QTimer.singleShot(1850, lambda: win.engine.seek_ms(1100))
QTimer.singleShot(2050, lambda: save(win, "shot_nowplaying.png"))
QTimer.singleShot(2400, app.quit)
sys.exit(app.exec())
