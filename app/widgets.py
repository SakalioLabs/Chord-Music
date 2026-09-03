"""自定义控件。

* :class:`ElidedLabel`：超长自动省略号的文本标签。
* :class:`TrackRow`：歌曲列表单行（序号/正在播放指示、标题、格式、时长、收藏），
  所有图标均来自 assets/icons 的 SVG 矢量资源。
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from . import theme

LIKED_RED = "#E04F5F"
HEART_GRAY = "#A6ADBB"
BRAND = "#3569E0"
TEXT_TERTIARY = "#9AA1AF"


class ElidedLabel(QLabel):
    """文本超出可用宽度时以右侧省略号截断。"""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full_text = text
        self._elide()

    def setText(self, text):  # noqa: N802
        self._full_text = text
        self._elide()

    def fullText(self) -> str:
        return self._full_text

    def resizeEvent(self, event):  # noqa: N802
        self._elide()
        super().resizeEvent(event)

    def _elide(self) -> None:
        elided = self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideRight, max(0, self.width())
        )
        super().setText(elided)


class TrackRow(QWidget):
    """列表中的一行歌曲。"""

    likedClicked = Signal(str)
    doubleActivated = Signal(str)
    addRequested = Signal(str, QPoint)

    def __init__(self, path, title, fmt, duration_text, liked,
                 index: int = 0, playing: bool = False, parent=None):
        super().__init__(parent)
        self._path = path
        self._liked = liked
        self._playing = playing

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 4, 10, 4)
        layout.setSpacing(10)

        # 序号 / 正在播放均衡条（同一位置两页切换）
        self.index_label = QLabel(str(index))
        self.index_label.setObjectName("trackIndex")
        self.index_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.eq_label = QLabel()
        self.eq_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.eq_label.setPixmap(theme.render_pixmap("eq", 16, BRAND))
        self.lead = QWidget()
        self.lead.setFixedWidth(22)
        self.lead_stack = QStackedLayout(self.lead)
        self.lead_stack.setContentsMargins(0, 0, 0, 0)
        self.lead_stack.addWidget(self.index_label)
        self.lead_stack.addWidget(self.eq_label)
        layout.addWidget(self.lead)

        self.title = ElidedLabel(title)
        self.title.setObjectName("trackTitle")
        self.title.setMinimumWidth(40)
        layout.addWidget(self.title, 1)

        self.fmt = QLabel(fmt.upper())
        self.fmt.setObjectName("trackFormat")
        self.fmt.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.fmt.setFixedWidth(50)

        self.duration = QLabel(duration_text)
        self.duration.setObjectName("trackDuration")
        self.duration.setFixedWidth(50)
        self.duration.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.heart = QPushButton()
        self.heart.setObjectName("heartBtn")
        self.heart.setFixedSize(30, 30)
        self.heart.setIconSize(QSize(19, 19))
        self.heart.setCursor(Qt.CursorShape.PointingHandCursor)
        self.heart.setFlat(True)
        self.heart.setToolTip("收藏 / 取消收藏")
        self.heart.clicked.connect(lambda: self.likedClicked.emit(self._path))
        self._refresh_heart_icon()

        # 加入歌单：点击后由主窗口在按钮旁弹出原生菜单
        self.add_list_btn = QPushButton()
        self.add_list_btn.setObjectName("addListBtn")
        self.add_list_btn.setFixedSize(30, 30)
        self.add_list_btn.setIconSize(QSize(18, 18))
        self.add_list_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_list_btn.setFlat(True)
        self.add_list_btn.setToolTip("添加到歌单")
        self.add_list_btn.setIcon(theme.icon_states("add_list", HEART_GRAY, BRAND, 18))
        self.add_list_btn.clicked.connect(
            lambda: self.addRequested.emit(self._path,
                                           self.add_list_btn.mapToGlobal(QPoint(0, self.add_list_btn.height())))
        )

        layout.addWidget(self.fmt)
        layout.addWidget(self.duration)
        layout.addWidget(self.add_list_btn)
        layout.addWidget(self.heart)

        self.set_playing(playing)

    def _refresh_heart_icon(self) -> None:
        if self._liked:
            self.heart.setIcon(theme.icon("heart_filled", LIKED_RED, 19))
        else:
            self.heart.setIcon(theme.icon_states("heart", HEART_GRAY, LIKED_RED, 19))

    def set_liked(self, liked: bool) -> None:
        self._liked = liked
        self._refresh_heart_icon()

    def set_playing(self, playing: bool) -> None:
        self._playing = playing
        self.lead_stack.setCurrentIndex(1 if playing else 0)
        self.title.setStyleSheet(f"color: {BRAND}; font-weight: 600;" if playing else "")

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        self.doubleActivated.emit(self._path)
        super().mouseDoubleClickEvent(event)


class VolumePopup(QFrame):
    """点击喇叭图标浮出的竖向音量面板（Qt.Popup：点击外部自动收起）。"""

    valueChanged = Signal(int)
    muteRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("VolumePopup")
        self.setFixedWidth(52)
        # 圆角外透明、圆角内由 QSS 纯白实填，避免背后列表文字透出（“背景不干净”）
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 12, 8, 10)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        self.slider = QSlider(Qt.Orientation.Vertical)
        self.slider.setObjectName("VolumeSlider")
        self.slider.setRange(0, 100)
        self.slider.setFixedSize(22, 104)
        self.slider.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.mute_btn = QPushButton()
        self.mute_btn.setObjectName("VolMuteBtn")
        self.mute_btn.setFixedSize(26, 24)
        self.mute_btn.setIconSize(QSize(16, 16))
        self.mute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mute_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mute_btn.clicked.connect(self.muteRequested.emit)

        for w in (self.slider, self.mute_btn):
            layout.addWidget(w, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.slider.valueChanged.connect(self._slider_changed)

    def _slider_changed(self, value: int) -> None:
        self.valueChanged.emit(value)

    def sync(self, value: int, icon_name: str, color: str) -> None:
        """外部（快捷键/静音）改变音量后同步浮层，且不再反向发信号。"""
        self.slider.blockSignals(True)
        self.slider.setValue(value)
        self.slider.blockSignals(False)
        self.mute_btn.setIcon(theme.icon(icon_name, color, 16))

    def popup_at(self, anchor: QWidget) -> None:
        """定位到锚点按钮正上方并弹出。"""
        # show 之前布局尚未激活，顶层窗口高度是默认大尺寸（数百 px），直接用 self.height()
        # 计算向上偏移会把浮层抛到窗口顶部之外（表现为“音量条乱飞”）。先激活布局并按内容
        # 收缩到真实尺寸，再取宽高定位。
        self.ensurePolished()
        if self.layout() is not None:
            self.layout().activate()
        self.adjustSize()
        self.slider.setFocus()
        local = QPoint(anchor.width() // 2 - self.width() // 2, -self.height() - 8)
        self.move(anchor.mapToGlobal(local))
        self.show()


class ClickableFrame(QFrame):
    """可点击的容器：整块区域（如封面+歌名）点击后发出 clicked，用于进入详情。"""

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)
