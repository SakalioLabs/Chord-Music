"""应用自绘风格的轻量对话框，替代系统默认 QInputDialog / QMessageBox 的原生外观。

设计令牌与主程序 style.qss 保持一致：白色圆角卡片、品牌蓝主按钮、中文按钮、
无边框窗口（圆角外透明），标题区可拖拽。
"""
from typing import Optional, Tuple

from PySide6.QtCore import Qt, QPoint
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QVBoxLayout, QWidget,
)

BRAND = "#3569E0"
BRAND_HOVER = "#2B5BD0"
BRAND_PRESSED = "#234DB8"
TEXT_1 = "#1B1E26"
TEXT_2 = "#5E6573"
BORDER = "#E4E8EF"
CARD = "#FFFFFF"

_DIALOG_QSS = f"""
QFrame#AppDialog {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 14px;
}}
QLabel#DlgTitle {{ color: {TEXT_1}; font-size: 15px; font-weight: 700; background: transparent; }}
QLabel#DlgField {{ color: {TEXT_2}; font-size: 12px; background: transparent; }}
QLineEdit#DlgInput {{
    background: #F5F7FA;
    border: 1px solid #DDE2EA;
    border-radius: 8px;
    padding: 8px 10px;
    font-size: 13px;
    color: {TEXT_1};
    selection-background-color: {BRAND};
    selection-color: #FFFFFF;
}}
QLineEdit#DlgInput:focus {{ border: 1px solid {BRAND}; background: #FFFFFF; }}
QPushButton#DlgGhost {{
    background: #FFFFFF; border: 1px solid #DDE2EA; border-radius: 8px;
    color: {TEXT_1}; font-size: 13px; padding: 7px 18px; min-width: 56px;
}}
QPushButton#DlgGhost:hover {{ background: #F2F4F8; }}
QPushButton#DlgGhost:pressed {{ background: #E9ECF2; }}
QPushButton#DlgPrimary {{
    background: {BRAND}; border: none; border-radius: 8px;
    color: #FFFFFF; font-size: 13px; font-weight: 600; padding: 7px 18px; min-width: 56px;
}}
QPushButton#DlgPrimary:hover {{ background: {BRAND_HOVER}; }}
QPushButton#DlgPrimary:pressed {{ background: {BRAND_PRESSED}; }}
QPushButton#DlgPrimary:disabled {{ background: #A9C0F2; }}
"""


class _DraggableDialog(QDialog):
    """无边框对话框，按住标题/空白处可拖动；回车确定、Esc 取消。"""

    def __init__(self, parent: Optional[QWidget], title: str):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet(_DIALOG_QSS)
        self._drag_offset: Optional[QPoint] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.card = QFrame()
        self.card.setObjectName("AppDialog")
        self.card.setFixedWidth(340)
        outer.addWidget(self.card)
        self.box = QVBoxLayout(self.card)
        self.box.setContentsMargins(20, 18, 20, 18)
        self.box.setSpacing(12)

        self.title_lbl = QLabel(title)
        self.title_lbl.setObjectName("DlgTitle")
        self.box.addWidget(self.title_lbl)

    # 标题区/空白处拖拽
    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        self._drag_offset = None
        super().mouseReleaseEvent(event)


class InputDialog(_DraggableDialog):
    """单行文本输入对话框。get_text() 返回 (文本, 是否确定)。"""

    def __init__(self, parent: Optional[QWidget], title: str,
                 field_label: str, text: str = "", placeholder: str = ""):
        super().__init__(parent, title)

        if field_label:
            lbl = QLabel(field_label)
            lbl.setObjectName("DlgField")
            self.box.addWidget(lbl)

        self.edit = QLineEdit(text)
        self.edit.setObjectName("DlgInput")
        if placeholder:
            self.edit.setPlaceholderText(placeholder)
        self.edit.selectAll()
        self.edit.returnPressed.connect(self.accept)
        self.box.addWidget(self.edit)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 4, 0, 0)
        btn_row.addStretch(1)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setObjectName("DlgGhost")
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_ok = QPushButton("确定")
        self.btn_ok.setObjectName("DlgPrimary")
        self.btn_ok.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_row.addWidget(self.btn_cancel)
        btn_row.addSpacing(10)
        btn_row.addWidget(self.btn_ok)
        self.box.addLayout(btn_row)

        self.btn_cancel.clicked.connect(self.reject)
        self.btn_ok.clicked.connect(self.accept)

    def value(self) -> str:
        return self.edit.text().strip()

    @classmethod
    def get_text(cls, parent: Optional[QWidget], title: str, field_label: str,
                 text: str = "", placeholder: str = "") -> Tuple[str, bool]:
        dlg = cls(parent, title, field_label, text, placeholder)
        dlg.edit.setFocus()
        ok = dlg.exec() == QDialog.DialogCode.Accepted
        return (dlg.value(), ok)


class ConfirmDialog(_DraggableDialog):
    """确认对话框，替代 QMessageBox.question；get_confirm 返回是否确认。"""

    def __init__(self, parent: Optional[QWidget], title: str, message: str,
                 ok_text: str = "确定", cancel_text: str = "取消",
                 danger: bool = False):
        super().__init__(parent, title)
        msg = QLabel(message)
        msg.setWordWrap(True)
        msg.setStyleSheet(f"color:{TEXT_1}; font-size:13px; background:transparent;")
        self.box.addWidget(msg)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 4, 0, 0)
        btn_row.addStretch(1)
        self.btn_cancel = QPushButton(cancel_text)
        self.btn_cancel.setObjectName("DlgGhost")
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_ok = QPushButton(ok_text)
        self.btn_ok.setObjectName("DlgPrimary")
        self.btn_ok.setCursor(Qt.CursorShape.PointingHandCursor)
        if danger:
            self.btn_ok.setStyleSheet(
                "background:#E5484D;border:none;border-radius:8px;color:#FFF;"
                "font-size:13px;font-weight:600;padding:7px 18px;min-width:56px;")
        btn_row.addWidget(self.btn_cancel)
        btn_row.addSpacing(10)
        btn_row.addWidget(self.btn_ok)
        self.box.addLayout(btn_row)

        self.btn_cancel.clicked.connect(self.reject)
        self.btn_ok.clicked.connect(self.accept)

    @classmethod
    def get_confirm(cls, parent, title, message, ok_text="确定",
                    cancel_text="取消", danger=False) -> bool:
        dlg = cls(parent, title, message, ok_text, cancel_text, danger)
        return dlg.exec() == QDialog.DialogCode.Accepted


class NoticeDialog(_DraggableDialog):
    """单按钮信息/警示框，替代 QMessageBox.information/warning/critical。"""

    def __init__(self, parent, title: str, message: str,
                 ok_text: str = "我知道了", danger: bool = False):
        super().__init__(parent, title)
        msg = QLabel(message)
        msg.setWordWrap(True)
        msg.setStyleSheet(
            f"color:{'#D13438' if danger else TEXT_1}; font-size:13px; background:transparent;")
        self.box.addWidget(msg)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 4, 0, 0)
        btn_row.addStretch(1)
        self.btn_ok = QPushButton(ok_text)
        self.btn_ok.setObjectName("DlgPrimary")
        self.btn_ok.setCursor(Qt.CursorShape.PointingHandCursor)
        if danger:
            self.btn_ok.setStyleSheet(
                "background:#E5484D;border:none;border-radius:8px;color:#FFF;"
                "font-size:13px;font-weight:600;padding:7px 18px;min-width:72px;")
        btn_row.addWidget(self.btn_ok)
        self.box.addLayout(btn_row)
        self.btn_ok.clicked.connect(self.accept)

    @classmethod
    def show(cls, parent, title, message, ok_text="我知道了", danger=False) -> None:
        dlg = cls(parent, title, message, ok_text, danger)
        dlg.exec()
