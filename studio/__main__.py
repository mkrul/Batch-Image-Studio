from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

from .config import SUPPORT
from .ui import MainWindow, apply_theme


def _publish_pid() -> None:
    raw = os.environ.get("BATCH_STUDIO_PIDFILE", "").strip()
    if not raw:
        return
    path = Path(raw)
    SUPPORT.mkdir(parents=True, exist_ok=True)
    if path.resolve().parent != SUPPORT.resolve() or not path.name.startswith("gui-") or path.suffix != ".pid":
        return
    path.write_text(str(os.getpid()), encoding="ascii")

ICON_PATH = Path(__file__).resolve().parent / "icon.png"


def main() -> None:
    _publish_pid()
    app = QApplication(sys.argv)
    app.setApplicationName("Batch Image Studio")
    app.setApplicationDisplayName("Batch Image Studio")
    app.setStyle("Fusion")
    apply_theme("light")
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
