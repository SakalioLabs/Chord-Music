"""回归：按压动画对象被 Qt 回收后不再崩溃，且图标回弹到各自原始尺寸；fade_in 可安全重入。"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEvent, QPointF, QSize, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton, QWidget  # noqa: E402

from app import animation  # noqa: E402

app = QApplication(sys.argv)
results = []


def check(name, cond):
    results.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")


def pump(seconds=0.4):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents(); time.sleep(0.01)


def mouse(t):
    return QMouseEvent(t, QPointF(2, 2), QPointF(2, 2), Qt.MouseButton.LeftButton,
                       Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)


crashed = False
pf = animation.PressScaleFilter()
b22 = QPushButton(); b22.setIconSize(QSize(22, 22)); b22.installEventFilter(pf)
b19 = QPushButton(); b19.setIconSize(QSize(19, 19)); b19.installEventFilter(pf)

try:
    # 高频反复按压（动画会在中途被新动画打断、并被 DeleteWhenStopped 回收）
    for _ in range(25):
        for b in (b22, b19):
            b.event(mouse(QEvent.Type.MouseButtonPress)); app.processEvents()
            b.event(mouse(QEvent.Type.MouseButtonRelease)); app.processEvents()
except RuntimeError as exc:  # noqa: BLE001
    crashed = True
    print("崩溃:", exc)

check("高频按压不再抛 already-deleted", not crashed)
pump(0.5)
check("22px 按钮回弹到 22", b22.iconSize().width() == 22)
check("19px 按钮回弹到 19（不被统一尺寸带偏）", b19.iconSize().width() == 19)

# Leave 事件也不应崩
try:
    b22.event(mouse(QEvent.Type.MouseButtonPress)); app.processEvents()
    b22.event(mouse(QEvent.Type.Leave)); pump(0.4)
    leave_ok = True
except RuntimeError:  # noqa: BLE001
    leave_ok = False
check("按下后离开(Leave)不崩溃并回弹", leave_ok and b22.iconSize().width() == 22)

# fade_in 对同一 widget 快速重入不崩，且结束后移除透明效果
w = QWidget()
try:
    for _ in range(10):
        animation.fade_in(w); app.processEvents()
    fade_ok = True
except RuntimeError:  # noqa: BLE001
    fade_ok = False
pump(0.5)
check("fade_in 可安全重入", fade_ok)
check("淡入结束后移除透明效果（文字回归原生渲染）", w.graphicsEffect() is None)

ok = all(results)
print("\n总体结果:", "全部通过 ✅" if ok else f"存在失败 ❌ ({results.count(False)})")
sys.exit(0 if ok else 1)
