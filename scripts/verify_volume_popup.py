"""回归：音量浮层必须稳定锚定在底部喇叭按钮正上方，不得飞到窗口顶部/右上角。"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import store as _store  # noqa: E402
import tempfile as _tf, pathlib as _pl  # noqa: E402
_store.app_data_dir = lambda: _pl.Path(_tf.mkdtemp(prefix="chord_test_"))
from app.main_window import MainWindow  # noqa: E402

app = QApplication([])
fail = []


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        fail.append(name)


def pump(ms=80):
    import time
    end = time.perf_counter() + ms / 1000
    while time.perf_counter() < end:
        app.processEvents()


win = MainWindow()
win.resize(1280, 800)
win.show()
pump()

anchor = win.btn_mute
pop = win.volume_popup


def open_and_check(tag):
    win._open_volume_popup()
    pump(40)
    a_top_left = anchor.mapToGlobal(QPoint(0, 0))
    anchor_cx = a_top_left.x() + anchor.width() / 2
    anchor_top = a_top_left.y()
    px, py, pw, ph = pop.x(), pop.y(), pop.width(), pop.height()
    pop_cx = px + pw / 2
    gap = anchor_top - (py + ph)
    print(f"[{tag}] anchor=({a_top_left.x()},{anchor_top}) popup=({px},{py},{pw}x{ph}) "
          f"中心偏差={pop_cx - anchor_cx:.1f}px 上间距={gap}px")
    check(f"{tag} 水平与喇叭居中(偏差<=2)", abs(pop_cx - anchor_cx) <= 2)
    check(f"{tag} 底边在喇叭上方 8px", gap == 8)
    check(f"{tag} 高度为内容高度(<220，非默认大窗)", ph < 220)
    # 本次 bug 是浮层飞到窗口上方远处：断言顶部不越出屏幕上沿、且确实位于锚点上方。
    check(f"{tag} 不飞出屏幕上沿", py >= 0)
    check(f"{tag} 位于喇叭按钮上方", py + ph <= anchor_top)
    return px, py


p1 = open_and_check("首次")
pop.hide()
pump(20)
p2 = open_and_check("再次")
check("两次弹出位置一致", p1 == p2)

print("\n结果:", "全部通过" if not fail else f"{len(fail)} 项失败 {fail}")
raise SystemExit(1 if fail else 0)
