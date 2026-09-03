"""弦乐音乐播放器 —— 程序入口。

运行：
    python main.py
"""

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from app import theme
from app.main_window import MainWindow


def load_stylesheet(app: QApplication) -> None:
    qss_path = Path(__file__).resolve().parent / "app" / "style.qss"
    if qss_path.is_file():
        app.setStyleSheet(qss_path.read_text(encoding="utf-8"))


def main() -> int:
    # 必须在 QApplication 创建之前设定高 DPI 舍入策略（非整数缩放防发虚）。
    theme.configure_high_dpi()

    app = QApplication(sys.argv)
    app.setApplicationName("弦乐音乐播放器")

    # 加载随应用分发的 HarmonyOS Sans（简中 + 西文），并设为全局清晰字体
    theme.load_application_fonts()
    app.setFont(theme.application_font(10))
    theme.install_message_filter()

    load_stylesheet(app)

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
