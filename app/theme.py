"""主题资源：HarmonyOS Sans 字体加载 + 矢量图标(SVG)渲染 + 官方头像。

所有图标都来自 assets/icons 下的 SVG 资源文件（遵循 HarmonyOS 24 网格图标规范），
运行时按需着色、按设备像素比高清渲染，避免用字符或 paintEvent 自绘控件图标。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QFont,
    QFontDatabase,
    QGuiApplication,
    QIcon,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

ASSETS = Path(__file__).resolve().parent.parent / "assets"
ICON_DIR = ASSETS / "icons"
FONT_DIR = ASSETS / "fonts"
IMAGE_DIR = ASSETS / "images"
AVATAR_SVG = IMAGE_DIR / "元服务静默登录默认头像.svg"

# QSS 字体族：简中优先 HarmonyOS Sans SC，西文/数字回退 HarmonyOS Sans
FONT_STACK = '"HarmonyOS Sans SC", "HarmonyOS Sans", "Microsoft YaHei UI", sans-serif'

_text_cache: dict[str, str] = {}
_pix_cache: dict[tuple, QPixmap] = {}


def configure_high_dpi() -> None:
    """在创建 QApplication **之前**调用，完成高 DPI 自适应。

    采用 PassThrough：在 125%/150% 等非整数缩放下仍按真实缩放因子渲染矢量文字与
    SVG（而不是先按 100% 位图再拉伸），这是 Windows 非整数缩放下避免字体发虚的关键。
    Qt6 默认虽为 PassThrough，这里显式固定，保证不同机器行为一致。
    """
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )


def application_font(point_size: int = 10) -> QFont:
    """全局应用字体：抗锯齿 + 无 hinting，忠实字形原始轮廓，边缘柔和不生硬。

    不使用 PreferFullHinting：全字网格对齐在高 DPI / 非整数缩放下会把笔画
    强制扭曲到像素边界，视觉上边缘尖锐、字形僵硬。现代屏幕与设计系统
    （macOS / Win11 / Figma / Flutter）均采用无 hinting 抗锯齿渲染。
    """
    font = QFont("HarmonyOS Sans SC", point_size)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return font


def install_message_filter() -> None:
    """过滤一条已知的无害 Qt 警告，其余消息照常输出到标准错误。

    QSS 使用 px 字号后，派生子控件的字体 pointSize 为 -1，Qt 内部个别路径会以 -1
    调用 QFont::setPointSize 并打印 “Point size <= 0” 警告；它不影响任何渲染，这里
    仅屏蔽这一条，避免污染控制台，其它警告/关键信息一律保留。
    """
    import sys

    from PySide6.QtCore import QtMsgType, qInstallMessageHandler

    def handler(mode, _context, message):
        if "QFont::setPointSize" in message and "Point size <= 0" in message:
            return
        stream = sys.stderr if mode in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg) else sys.stdout
        stream.write(message + "\n")

    qInstallMessageHandler(handler)


def load_application_fonts() -> list[str]:
    """加载 fonts 目录下全部 HarmonyOS Sans 字体，返回可用族名。

    自动扫描目录（而非硬编码文件名），后续补充 Medium / SemiBold / Bold
    等字重文件后无需改代码。仅有 Regular 字重时，QSS 的粗体会走算法合成
    （synthetic bold），边缘偏粗糙，建议补齐多字重字体文件。
    """
    families: list[str] = []
    if not FONT_DIR.is_dir():
        return families
    for path in sorted(FONT_DIR.iterdir()):
        if path.suffix.lower() not in (".ttf", ".otf", ".ttc"):
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id >= 0:
            families.extend(QFontDatabase.applicationFontFamilies(font_id))
    return families


def _svg_text(name: str) -> str:
    if name not in _text_cache:
        _text_cache[name] = (ICON_DIR / f"{name}.svg").read_text(encoding="utf-8")
    return _text_cache[name]


def _dpr() -> float:
    app = QApplication.instance()
    if app is not None:
        screen = app.primaryScreen()
        if screen is not None:
            return screen.devicePixelRatio()
    return 1.0


def render_pixmap(name: str, size: int, color: str = "#FFFFFF") -> QPixmap:
    """把指定 SVG 图标渲染成 size×size（逻辑像素）的高清 QPixmap。"""
    key = (name, size, color)
    cached = _pix_cache.get(key)
    if cached is not None:
        return cached

    svg = _svg_text(name).replace("#__C__", color)
    renderer = QSvgRenderer(svg.encode("utf-8"))
    dpr = _dpr()
    phys = max(1, round(size * dpr))
    pm = QPixmap(phys, phys)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    renderer.render(painter, QRectF(0, 0, phys, phys))
    painter.end()
    pm.setDevicePixelRatio(dpr)
    _pix_cache[key] = pm
    return pm


def icon(name: str, color: str = "#FFFFFF", size: int = 24) -> QIcon:
    """单色图标。"""
    return QIcon(render_pixmap(name, size, color))


def icon_states(name: str, normal: str, active: str, size: int = 24) -> QIcon:
    """带常规/悬停两态颜色的图标，QPushButton 悬停时自动切换。"""
    ic = QIcon()
    ic.addPixmap(render_pixmap(name, size, normal), QIcon.Mode.Normal)
    ic.addPixmap(render_pixmap(name, size, active), QIcon.Mode.Active)
    ic.addPixmap(render_pixmap(name, size, active), QIcon.Mode.Selected)
    ic.addPixmap(render_pixmap(name, size, normal), QIcon.Mode.Disabled)
    return ic


@lru_cache(maxsize=8)
def avatar_pixmap(size: int) -> QPixmap:
    """渲染官方默认头像（自带圆形渐变与白色人形）。"""
    dpr = _dpr()
    phys = max(1, round(size * dpr))
    pm = QPixmap(phys, phys)
    pm.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(str(AVATAR_SVG))
    painter = QPainter(pm)
    renderer.render(painter, QRectF(0, 0, phys, phys))
    painter.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def cover_pixmap(data: bytes, size: int, radius: int = 10):
    """把内嵌封面字节渲染成 size×size（逻辑像素）的正方形圆角高清 QPixmap。

    保持比例铺满并居中裁剪；数据无法解码时返回 None，由调用方回退默认图标。
    """
    if not data:
        return None
    source = QPixmap()
    if not source.loadFromData(bytes(data)):
        return None
    dpr = _dpr()
    phys = max(1, round(size * dpr))
    scaled = source.scaled(
        phys, phys,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    out = QPixmap(phys, phys)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    path = QPainterPath()
    rr = radius * dpr
    path.addRoundedRect(QRectF(0, 0, phys, phys), rr, rr)
    painter.setClipPath(path)
    painter.drawPixmap((phys - scaled.width()) // 2, (phys - scaled.height()) // 2, scaled)
    painter.end()
    out.setDevicePixelRatio(dpr)
    return out


def clear_icon_cache() -> None:
    """设备像素比变化（窗口拖到不同缩放的屏幕）时清空缓存，按新 dpr 重渲染。"""
    _pix_cache.clear()
    avatar_pixmap.cache_clear()
