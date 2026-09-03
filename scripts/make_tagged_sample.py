"""生成带内嵌标签 / 同步歌词 / 专辑封面的测试 FLAC（samples/tone_tagged.flac）。"""
import os
import shutil
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QBuffer, QIODevice, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)


def make_cover_png() -> bytes:
    img = QImage(160, 160, QImage.Format.Format_RGB32)
    grad = QLinearGradient(QPointF(0, 0), QPointF(160, 160))
    grad.setColorAt(0.0, QColor("#3569E0"))
    grad.setColorAt(1.0, QColor("#8B5CF6"))
    p = QPainter(img)
    p.fillRect(img.rect(), grad)
    # 抽象唱片图案（不依赖字体）：白色半透明外环 + 中心圆
    from PySide6.QtGui import QPen
    pen = QPen(QColor(255, 255, 255, 230))
    pen.setWidth(6)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(34, 34, 92, 92)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 255, 255, 235))
    p.drawEllipse(70, 70, 20, 20)
    p.end()
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def main() -> None:
    src = ROOT / "samples" / "tone.flac"
    dst = ROOT / "samples" / "tone_tagged.flac"
    shutil.copyfile(src, dst)

    from mutagen.flac import FLAC, Picture

    audio = FLAC(str(dst))
    audio["TITLE"] = "弦乐测试曲"
    audio["ARTIST"] = "测试艺术家"
    audio["ALBUM"] = "元数据自测专辑"
    audio["LYRICS"] = (
        "[00:00.00]第一行 · 前奏响起\n"
        "[00:00.50]第二行 · 主歌进入\n"
        "[00:01.00]第三行 · 副歌展开\n"
        "[00:01.50]第四行 · 缓缓收尾"
    )
    audio.clear_pictures()
    pic = Picture()
    pic.type = 3  # Front Cover
    pic.mime = "image/png"
    pic.desc = "Front Cover"
    pic.data = make_cover_png()
    audio.add_picture(pic)
    audio.save()
    print(f"已生成带元数据样本: {dst}  ({dst.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
