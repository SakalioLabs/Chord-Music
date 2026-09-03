"""真实平台视觉冒烟（不可 offscreen）：验证新版任务栏歌词——
透明背景、固定宽度、超长行跑马灯、换段从下往上动画。运行后在 scripts/ 下生成截图供目视。"""
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.taskbar_lyrics import TaskbarLyrics, FIXED_W  # noqa: E402

app = QApplication(sys.argv)
screen = app.primaryScreen()
tb = TaskbarLyrics()
print("任务栏支持:", tb.is_supported())
print("show=", tb.show(), " 固定宽度=", FIXED_W)

NORMAL = "天之大 · 毛阿敏"
LONG = "这是一句特意写得很长很长很长很长很长的歌词用来验证超长时向左匀速滚动的跑马灯效果是否正常工作"
NEXT = "妈妈 月光之下 静静地 我想你了"


def grab(name):
    pm = screen.grabWindow(0)
    w, h = pm.width(), pm.height()
    # 歌词窗右对齐到托盘左侧：截任务栏右侧条带（物理像素）。
    band = pm.copy(max(0, w - 1180), h - 60, 1180, 60)
    out = ROOT / "scripts" / name
    band.save(str(out))
    print("saved", out, "全屏物理尺寸", w, h)


QTimer.singleShot(300, lambda: tb.set_text(NORMAL))
QTimer.singleShot(1000, lambda: grab("taskbar_embed_1.png"))      # 正常行：透明/固定宽/居中
QTimer.singleShot(1300, lambda: tb.set_text(LONG))                # 超长行
QTimer.singleShot(3400, lambda: grab("taskbar_embed_2.png"))      # 跑马灯滚动中段
QTimer.singleShot(3900, lambda: tb.set_text(NEXT))                # 触发换段垂直动画
QTimer.singleShot(3955, lambda: grab("taskbar_embed_3.png"))      # 换词后约 55ms：两行错位
QTimer.singleShot(4010, lambda: grab("taskbar_embed_3b.png"))     # 约 110ms：接近到位
QTimer.singleShot(4300, lambda: grab("taskbar_embed_4.png"))      # 换段完成、新行居中


def end():
    tb.close()
    app.quit()


QTimer.singleShot(4900, end)
sys.exit(app.exec())
