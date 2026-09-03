"""验证窗口只能通过顶部拖拽区（侧栏 Chord 头 / 右侧窗口控制手柄）拖动（内容区拖动无效），
以及窗口按钮悬停反馈。

关键：用 QApplication.sendEvent 走真实事件分发链（事件过滤器必须真的 installEventFilter 才会触发），
而不是直接调用 win.eventFilter——后者无法发现“拖拽容器漏装事件过滤器导致真实拖不动”的回归。
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import store as _store  # noqa: E402
import tempfile as _tf, pathlib as _pl  # noqa: E402
_store.app_data_dir = lambda: _pl.Path(_tf.mkdtemp(prefix="chord_test_"))
from app.main_window import MainWindow  # noqa: E402

app = QApplication(sys.argv)
win = MainWindow()
win.show()


def mouse(t, local=(5, 5), glob=(100, 100), button=Qt.MouseButton.LeftButton):
    return QMouseEvent(t, QPointF(*local), QPointF(*glob), button, button, Qt.KeyboardModifier.NoModifier)


def send(widget, ev):
    """走 Qt 真实事件分发：只有 widget 上安装过 self 事件过滤器才会进入 MainWindow.eventFilter。"""
    return app.sendEvent(widget, ev)


results = []
def check(name, cond):
    results.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")


# 1) 在内容区按下鼠标：不应进入拖动状态
win._drag_offset = None
send(win.content, mouse(QEvent.Type.MouseButtonPress, (50, 50), (200, 200)))
check("内容区按下不触发拖动", win._drag_offset is None)

# 1b) 在列表上同样不拖
send(win.local_list, mouse(QEvent.Type.MouseButtonPress, (5, 5), (200, 200)))
check("列表区按下不触发拖动", win._drag_offset is None)

# 2) 在侧栏 Chord 头部【真实派发】按下：必须真的安装了过滤器才会进入拖动
send(win.chord_head, mouse(QEvent.Type.MouseButtonPress, (30, 30), (200, 200)))
check("侧栏 Chord 头部按下进入拖动（过滤器已安装）", win._drag_offset is not None)
send(win.chord_head, mouse(QEvent.Type.MouseButtonRelease, (30, 30), (200, 200)))
check("松开后清除拖动状态", win._drag_offset is None)

# 2b) 右侧窗口控制手柄是另一个独立拖拽区，同样可拖
send(win.win_handle, mouse(QEvent.Type.MouseButtonPress, (10, 10), (200, 200)))
check("窗口控制手柄按下进入拖动（过滤器已安装）", win._drag_offset is not None)

# 3) 拖到全局 (240,210)：窗口应随之移动 +40/+10
x0, y0 = win.x(), win.y()
send(win.win_handle, mouse(QEvent.Type.MouseMove, (10, 10), (240, 210)))
check("顶部拖拽区拖动改变窗口位置", win.x() == x0 + 40 and win.y() == y0 + 10)

# 4) 松开后清除拖动状态
send(win.win_handle, mouse(QEvent.Type.MouseButtonRelease, (10, 10), (240, 210)))
check("松开后拖动状态已清空", win._drag_offset is None)

# 5) 主窗口类不再有全局鼠标拖动方法
check("已移除全局 mousePressEvent 拖动",
      "mousePressEvent" not in MainWindow.__dict__ and "mouseMoveEvent" not in MainWindow.__dict__)

# 6) 窗口线性图标按钮：始终显示图标，悬停/离开均非空（关闭键悬停反色、离开恢复）
check("窗口按钮常态即显示图标", not win.btn_close.icon().isNull())
send(win.btn_close, QEvent(QEvent.Type.Enter))
check("悬停窗口按钮保持图标", not win.btn_close.icon().isNull())
send(win.btn_close, QEvent(QEvent.Type.Leave))
check("离开窗口按钮恢复常态图标", not win.btn_close.icon().isNull())

# 7) 双击顶部拖拽区最大化/还原
geom_before = win.geometry()
send(win.win_handle, QMouseEvent(
    QEvent.Type.MouseButtonDblClick, QPointF(10, 10), QPointF(200, 200),
    Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
check("双击顶部拖拽区最大化", win._normal_geom is not None and win.geometry() != geom_before)

ok = all(results)
print("\n总体结果:", "全部通过 ✅" if ok else f"存在失败 ❌ ({results.count(False)})")
sys.exit(0 if ok else 1)
