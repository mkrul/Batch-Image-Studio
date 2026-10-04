from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

from .ui import STYLESHEET, MainWindow

ICON_PATH = Path(__file__).resolve().parent / "icon.png"


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Batch Image Studio")
    app.setApplicationDisplayName("Batch Image Studio")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    icon = QIcon(str(ICON_PATH))
    if not icon.isNull():
        app.setWindowIcon(icon)
    font = QFont()
    font.setFamilies([".AppleSystemUIFont", "Helvetica Neue", "Arial"])
    font.setPointSize(13)
    app.setFont(font)
    window = MainWindow()
    if not icon.isNull():
        window.setWindowIcon(icon)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
