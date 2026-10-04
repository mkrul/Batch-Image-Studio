from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from datetime import datetime
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QByteArray, QEvent, QFileSystemWatcher, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFontMetrics, QIcon, QImage, QImageReader, QKeySequence, QPalette, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .config import (
    BACKGROUNDS,
    CUTOUT_DIR,
    DEFAULT_CRITERIA,
    IMAGE_MODELS,
    MODE_HELP,
    MODES,
    SCREEN_MODELS,
    atomic_write,
    clear_api_key,
    default_output,
    load_api_key,
    load_settings,
    qualities_for,
    save_api_key,
    save_settings,
    sizes_for,
)
from .engine import Engine, ProductSpec, RunContext
from .imaging import checker_preview, is_cutout, list_images, load_rgba, locked_output_size, safe_slug
from .pricing import estimate_image_call, estimate_screen_call, money

def _stylesheet(colors: dict[str, str]) -> str:
    return f"""
QWidget {{ color: {colors["text"]}; font-size: 13px; background: transparent; }}
QMainWindow, QWidget#root, QWidget#settings {{ background: {colors["page"]}; }}
QWidget#work, QListWidget#gallery, QPlainTextEdit#log {{
    background: {colors["panel"]};
    border: 1px solid {colors["panel_border"]};
    border-radius: 12px;
}}
QLabel#title {{ color: {colors["text"]}; font-size: 20px; font-weight: 600; }}
QLabel#section {{ color: {colors["text"]}; font-size: 15px; font-weight: 600; padding-top: 10px; }}
QLabel#activity, QLabel#status {{ color: {colors["text"]}; font-weight: 600; }}
QLabel#muted {{ color: {colors["text"]}; }}
QLabel#warning {{ color: {colors["warning"]}; font-weight: 600; }}
QFrame#card {{
    background: {colors["panel"]};
    border: 1px solid {colors["card_border"]};
    border-radius: 10px;
}}
QFrame#drop {{
    background: {colors["drop"]};
    border: 1px dashed {colors["drop_border"]};
    border-radius: 10px;
}}
QFrame#drop QLabel {{ color: {colors["text"]}; }}
QFrame#drop[active="true"] {{
    border: 2px solid {colors["accent"]};
    background: {colors["panel"]};
}}
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {colors["field"]};
    color: {colors["text"]};
    border: 1px solid {colors["field_border"]};
    border-radius: 6px;
    padding: 6px 8px;
    min-height: 22px;
    selection-background-color: {colors["accent"]};
    selection-color: #ffffff;
}}
QPlainTextEdit#log {{ padding: 8px; }}
QComboBox QAbstractItemView {{
    background: {colors["field"]};
    color: {colors["text"]};
    selection-background-color: {colors["accent"]};
    selection-color: #ffffff;
}}
QCheckBox {{ color: {colors["text"]}; spacing: 8px; }}
QPushButton {{
    background: {colors["button"]};
    color: {colors["text"]};
    border: 1px solid {colors["button_border"]};
    border-radius: 6px;
    padding: 7px 12px;
    min-height: 22px;
}}
QPushButton:hover {{ background: {colors["button_hover"]}; }}
QPushButton:disabled {{
    background: {colors["disabled"]};
    color: {colors["disabled_text"]};
    border: 1px solid {colors["disabled_border"]};
}}
QPushButton#primary {{
    background: {colors["accent"]};
    color: #ffffff;
    border: 1px solid {colors["accent"]};
    font-weight: 600;
    padding: 8px 18px;
}}
QPushButton#primary:hover {{ background: {colors["accent_hover"]}; color: #ffffff; }}
QPushButton#primary:disabled, QPushButton#primary:disabled:hover {{
    background: {colors["disabled"]};
    color: {colors["disabled_text"]};
    border: 1px solid {colors["disabled_border"]};
}}
QPushButton#stop {{
    background: {colors["stop"]};
    color: #ffffff;
    border: 1px solid {colors["stop"]};
}}
QPushButton#stop:hover {{ background: {colors["stop_hover"]}; color: #ffffff; }}
QPushButton#stop:disabled, QPushButton#stop:disabled:hover {{
    background: {colors["disabled"]};
    color: {colors["disabled_text"]};
    border: 1px solid {colors["disabled_border"]};
}}
QTabWidget::pane {{ border: none; background: transparent; }}
QTabBar::tab {{
    background: transparent;
    color: {colors["text"]};
    padding: 8px 16px;
    border: none;
    border-bottom: 2px solid transparent;
    font-weight: 600;
}}
QTabBar::tab:selected {{ color: {colors["accent"]}; border-bottom: 2px solid {colors["accent"]}; }}
QListWidget#gallery::item {{ color: {colors["text"]}; padding: 4px; }}
QListWidget#gallery::item:selected {{ background: {colors["accent"]}; color: #ffffff; }}
QScrollArea {{ border: none; background: transparent; }}
QSplitter::handle {{ background: {colors["split"]}; }}
QSplitter::handle:horizontal {{ width: 8px; }}
QScrollBar:vertical, QScrollBar:horizontal {{ background: {colors["page"]}; }}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: {colors["scroll"]};
    border-radius: 4px;
    min-height: 24px;
    min-width: 24px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0px; width: 0px; }}
"""


LIGHT_COLORS = {
    "text": "#1c1c1e",
    "page": "#f2f2f7",
    "panel": "#ffffff",
    "panel_border": "#d1d1d6",
    "warning": "#9f1239",
    "card_border": "#8e8e93",
    "drop": "#f2f2f7",
    "drop_border": "#636366",
    "accent": "#0a64d8",
    "accent_hover": "#0854b8",
    "field": "#ffffff",
    "field_border": "#636366",
    "button": "#ffffff",
    "button_border": "#1c1c1e",
    "button_hover": "#f2f2f7",
    "disabled": "#f2f2f7",
    "disabled_text": "#3a3a3c",
    "disabled_border": "#aeaeb2",
    "stop": "#b42318",
    "stop_hover": "#912018",
    "split": "#d1d1d6",
    "scroll": "#aeaeb2",
}

DARK_COLORS = {
    "text": "#f5f5f7",
    "page": "#1c1c1e",
    "panel": "#2c2c2e",
    "panel_border": "#636366",
    "warning": "#ff8fa3",
    "card_border": "#aeaeb2",
    "drop": "#1c1c1e",
    "drop_border": "#aeaeb2",
    "accent": "#0a64d8",
    "accent_hover": "#0854b8",
    "field": "#2c2c2e",
    "field_border": "#aeaeb2",
    "button": "#2c2c2e",
    "button_border": "#f5f5f7",
    "button_hover": "#3a3a3c",
    "disabled": "#3a3a3c",
    "disabled_text": "#d1d1d6",
    "disabled_border": "#636366",
    "stop": "#b42318",
    "stop_hover": "#912018",
    "split": "#636366",
    "scroll": "#636366",
}

LIGHT_STYLESHEET = _stylesheet(LIGHT_COLORS)
DARK_STYLESHEET = _stylesheet(DARK_COLORS)
STYLESHEET = LIGHT_STYLESHEET
_current_theme = "light"

STATUS_TITLES = {
    "generating": "Generating",
    "reviewing": "Reviewing",
    "passed": "Screener passed",
    "failed": "Screener rejected",
    "unreviewed": "Needs your review",
    "approved": "Approved",
    "rejected": "Rejected",
    "error": "Error",
    "cap": "Spend cap reached",
    "cancelled": "Cancelled",
}

STATUS_COLORS_LIGHT = {
    "approved": "#1f7a4d",
    "passed": "#1f6f78",
    "failed": "#8c3a32",
    "rejected": "#8c3a32",
    "error": "#8c3a32",
    "cap": "#8a5a12",
    "unreviewed": "#1c1c1e",
    "generating": "#3a3a3c",
    "reviewing": "#0a64d8",
    "cancelled": "#3a3a3c",
}

STATUS_COLORS_DARK = {
    "approved": "#3dd68c",
    "passed": "#5ee0e6",
    "failed": "#ff8a80",
    "rejected": "#ff8a80",
    "error": "#ff8a80",
    "cap": "#ffc14d",
    "unreviewed": "#f5f5f7",
    "generating": "#d1d1d6",
    "reviewing": "#64b5ff",
    "cancelled": "#d1d1d6",
}


def status_color(status: str) -> str:
    table = STATUS_COLORS_DARK if _current_theme == "dark" else STATUS_COLORS_LIGHT
    return table.get(status, table["unreviewed"])


def _palette(colors: dict[str, str]) -> QPalette:
    palette = QPalette()
    text = QColor(colors["text"])
    page = QColor(colors["page"])
    field = QColor(colors["field"])
    button = QColor(colors["button"])
    accent = QColor(colors["accent"])
    disabled = QColor(colors["disabled_text"])
    palette.setColor(QPalette.ColorRole.Window, page)
    palette.setColor(QPalette.ColorRole.WindowText, text)
    palette.setColor(QPalette.ColorRole.Base, field)
    palette.setColor(QPalette.ColorRole.AlternateBase, page)
    palette.setColor(QPalette.ColorRole.Text, text)
    palette.setColor(QPalette.ColorRole.Button, button)
    palette.setColor(QPalette.ColorRole.ButtonText, text)
    palette.setColor(QPalette.ColorRole.Highlight, accent)
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(colors["drop_border"]))
    palette.setColor(QPalette.ColorRole.ToolTipBase, field)
    palette.setColor(QPalette.ColorRole.ToolTipText, text)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, disabled)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, disabled)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, disabled)
    return palette


def apply_theme(theme: str) -> None:
    global _current_theme
    _current_theme = "dark" if theme == "dark" else "light"
    app = QApplication.instance()
    if app is None:
        return
    colors = DARK_COLORS if _current_theme == "dark" else LIGHT_COLORS
    app.setPalette(_palette(colors))
    app.setStyleSheet(DARK_STYLESHEET if _current_theme == "dark" else LIGHT_STYLESHEET)


def pil_to_pixmap(image: Image.Image) -> QPixmap:
    rgba = image.convert("RGBA")
    data = rgba.tobytes("raw", "RGBA")
    qimage = QImage(data, rgba.width, rgba.height, rgba.width * 4, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(qimage.copy())


def thumbnail_pixmap(path: str, edge: int) -> QPixmap:
    if not path or not Path(path).is_file():
        return QPixmap()
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isValid() and max(size.width(), size.height()) > edge:
        scale = edge / max(size.width(), size.height())
        reader.setScaledSize(QSize(max(1, int(size.width() * scale)), max(1, int(size.height() * scale))))
    image = reader.read()
    if not image.isNull():
        return QPixmap.fromImage(image)
    try:
        pil = load_rgba(Path(path))
        pil.thumbnail((edge, edge), Image.Resampling.LANCZOS)
        return pil_to_pixmap(pil)
    except Exception:
        return QPixmap()


def muted(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("muted")
    label.setWordWrap(True)
    return label


def section(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("section")
    return label


def _keyboard_selected(widget: QWidget) -> bool:
    focus = QApplication.focusWidget()
    return focus is not None and (focus is widget or widget.isAncestorOf(focus))


class ComboBox(QComboBox):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event) -> None:
        if self.view().isVisible():
            super().wheelEvent(event)
            return
        event.ignore()


class SpinBox(QSpinBox):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event) -> None:
        if _keyboard_selected(self):
            super().wheelEvent(event)
            return
        event.ignore()


class DoubleSpinBox(QDoubleSpinBox):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event) -> None:
        if _keyboard_selected(self):
            super().wheelEvent(event)
            return
        event.ignore()


class PlainText(QPlainTextEdit):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.viewport().installEventFilter(self)

    def _release_wheel(self, event) -> bool:
        if not _keyboard_selected(self):
            return True
        bar = self.verticalScrollBar()
        delta = event.angleDelta().y() or event.pixelDelta().y()
        if bar.maximum() <= bar.minimum():
            return True
        if delta > 0 and bar.value() <= bar.minimum():
            return True
        if delta < 0 and bar.value() >= bar.maximum():
            return True
        return False

    def eventFilter(self, watched, event) -> bool:
        if watched is self.viewport() and event.type() == QEvent.Type.Wheel and self._release_wheel(event):
            event.ignore()
            return True
        return super().eventFilter(watched, event)

    def wheelEvent(self, event) -> None:
        if self._release_wheel(event):
            event.ignore()
            return
        super().wheelEvent(event)


def narrow_combo(combo: QComboBox) -> None:
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(14)


def fill_combo(combo: QComboBox, pairs: list[tuple[str, str]], current: str) -> None:
    narrow_combo(combo)
    combo.blockSignals(True)
    combo.clear()
    for value, label in pairs:
        combo.addItem(label, value)
    index = combo.findData(current)
    if index < 0:
        index = combo.findData("high")
    if index < 0:
        index = combo.findData("1024x1024")
    combo.setCurrentIndex(index if index >= 0 else 0)
    combo.blockSignals(False)


class FuncThread(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, fn) -> None:
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        try:
            self.succeeded.emit(self._fn())
        except Exception as exc:
            self.failed.emit(str(exc))


class Thumb(QFrame):
    remove = Signal(str)
    make_first = Signal(str)

    def __init__(self, path: str, index: int) -> None:
        super().__init__()
        self.path = path
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        image = QLabel()
        image.setPixmap(thumbnail_pixmap(path, 88))
        image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(image)
        name = QLabel()
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        metrics = QFontMetrics(name.font())
        name.setText(metrics.elidedText(Path(path).name, Qt.TextElideMode.ElideMiddle, 96))
        layout.addWidget(name)
        row = QHBoxLayout()
        remove = QPushButton("Remove")
        remove.setObjectName("secondary")
        remove.clicked.connect(lambda: self.remove.emit(self.path))
        row.addWidget(remove)
        if index > 0:
            first = QPushButton("First")
            first.setObjectName("secondary")
            first.clicked.connect(lambda: self.make_first.emit(self.path))
            row.addWidget(first)
        layout.addLayout(row)


class ImageDrop(QFrame):
    changed = Signal()

    def __init__(self, empty_text: str) -> None:
        super().__init__()
        self.setObjectName("drop")
        self.setAcceptDrops(True)
        self.paths: list[str] = []
        self.empty_text = empty_text
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        self.empty = QLabel(empty_text)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        outer.addWidget(self.empty)
        self.row_host = QWidget()
        self.row = QHBoxLayout(self.row_host)
        self.row.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.row_host)
        scroll.setMinimumHeight(104)
        self.scroll = scroll
        outer.addWidget(scroll)
        self.setMinimumHeight(118)
        self._rebuild()

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._active(True)
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        self._active(False)
        self.add_paths(self._urls(event))
        event.acceptProposedAction()

    def _active(self, active: bool) -> None:
        self.setProperty("active", active)
        self.style().unpolish(self)
        self.style().polish(self)

    def _urls(self, event) -> list[str]:
        found = []
        for url in event.mimeData().urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.is_dir():
                found.extend(str(item) for item in list_images(path))
            elif path.is_file() and path.suffix.lower() in {
                ".png", ".jpg", ".jpeg", ".webp", ".gif", ".tif", ".tiff", ".heic", ".heif", ".bmp"
            }:
                found.append(str(path))
        return found

    def add_paths(self, paths: list[str]) -> None:
        changed = False
        for path in paths:
            if path not in self.paths:
                self.paths.append(path)
                changed = True
        if changed:
            self._rebuild()
            self.changed.emit()

    def set_paths(self, paths: list[str]) -> None:
        self.paths = [path for path in paths if Path(path).is_file()]
        self._rebuild()

    def clear_paths(self) -> None:
        self.paths = []
        self._rebuild()
        self.changed.emit()

    def _remove(self, path: str) -> None:
        self.paths = [item for item in self.paths if item != path]
        self._rebuild()
        self.changed.emit()

    def _make_first(self, path: str) -> None:
        if path not in self.paths:
            return
        self.paths = [path, *[item for item in self.paths if item != path]]
        self._rebuild()
        self.changed.emit()

    def _rebuild(self) -> None:
        while self.row.count():
            item = self.row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.empty.setVisible(not self.paths)
        self.scroll.setVisible(bool(self.paths))
        for index, path in enumerate(self.paths):
            thumb = Thumb(path, index)
            thumb.remove.connect(self._remove)
            thumb.make_first.connect(self._make_first)
            self.row.addWidget(thumb)
        self.row.addStretch(1)


class ProductCard(QFrame):
    changed = Signal()
    remove_requested = Signal(object)
    cutout_requested = Signal(object, str)

    def __init__(self, spec_id: str | None = None) -> None:
        super().__init__()
        self.spec_id = spec_id or uuid.uuid4().hex[:8]
        self.mask_path: str | None = None
        self.cutout_path: str | None = None
        self.setObjectName("card")
        self.setAcceptDrops(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)
        header = QHBoxLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("Product name")
        header.addWidget(self.name, 1)
        remove = QPushButton("Remove product")
        remove.setObjectName("secondary")
        remove.clicked.connect(lambda: self.remove_requested.emit(self))
        header.addWidget(remove)
        layout.addLayout(header)
        self.notes = PlainText()
        self.notes.setPlaceholderText("Optional instructions for this product only")
        self.notes.setFixedHeight(52)
        layout.addWidget(self.notes)
        mode_row = QHBoxLayout()
        self.mode = ComboBox()
        for value, label in MODES:
            self.mode.addItem(label, value)
        narrow_combo(self.mode)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        mode_row.addWidget(self.mode, 1)
        self.scale = DoubleSpinBox()
        self.scale.setRange(0.2, 0.9)
        self.scale.setSingleStep(0.05)
        self.scale.setValue(0.62)
        self.scale.setPrefix("Height ")
        self.scale.setToolTip("How tall the pasted product is, compared with the picture. 0.62 means 62 percent of the height.")
        mode_row.addWidget(self.scale)
        layout.addLayout(mode_row)
        self.mode_help = muted(MODE_HELP["reference"])
        layout.addWidget(self.mode_help)
        self.images = ImageDrop("Drop product photographs here. The first image is the frame that stays in place when pixels are locked.")
        self.images.changed.connect(self.changed.emit)
        layout.addWidget(self.images)
        buttons = QHBoxLayout()
        self.plain_button = QPushButton("Cut out plain background")
        self.plain_button.setObjectName("secondary")
        self.plain_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.plain_button.clicked.connect(lambda: self.cutout_requested.emit(self, "plain"))
        self.rembg_button = QPushButton("Cut out complex background")
        self.rembg_button.setObjectName("secondary")
        self.rembg_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.rembg_button.clicked.connect(lambda: self.cutout_requested.emit(self, "rembg"))
        buttons.addWidget(self.plain_button)
        buttons.addWidget(self.rembg_button)
        layout.addLayout(buttons)
        tolerance_row = QHBoxLayout()
        tolerance_row.addWidget(QLabel("Background tolerance"))
        self.tolerance = SpinBox()
        self.tolerance.setRange(8, 80)
        self.tolerance.setValue(34)
        tolerance_row.addWidget(self.tolerance)
        tolerance_row.addStretch(1)
        layout.addLayout(tolerance_row)
        extra = QGridLayout()
        extra.setHorizontalSpacing(8)
        extra.setVerticalSpacing(8)
        mask_button = QPushButton("Choose mask")
        mask_button.setObjectName("secondary")
        mask_button.clicked.connect(self._choose_mask)
        clear_mask = QPushButton("Clear mask")
        clear_mask.setObjectName("secondary")
        clear_mask.clicked.connect(self._clear_mask)
        clear_cutout = QPushButton("Clear cutout")
        clear_cutout.setObjectName("secondary")
        clear_cutout.clicked.connect(self._clear_cutout)
        clear_images = QPushButton("Clear images")
        clear_images.setObjectName("secondary")
        clear_images.clicked.connect(self.images.clear_paths)
        for button in (mask_button, clear_mask, clear_cutout, clear_images):
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        extra.addWidget(mask_button, 0, 0)
        extra.addWidget(clear_mask, 0, 1)
        extra.addWidget(clear_cutout, 1, 0)
        extra.addWidget(clear_images, 1, 1)
        layout.addLayout(extra)
        self.preview = QLabel("No cutout yet.")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.preview)
        self.state = muted("No cutout or mask yet.")
        layout.addWidget(self.state)
        self.plain_button.setToolTip("For a product on a plain backdrop. Only the backdrop becomes transparent. The product pixels stay as they are. Check the preview.")
        self.rembg_button.setToolTip("Uses a local model and does not send the photograph to OpenAI. Install it with .venv/bin/python -m pip install rembg")
        mask_button.setToolTip("PNG mask. Transparent areas are regenerated and opaque areas are kept. If the file has no transparency, white is kept and black is regenerated.")
        self.name.textChanged.connect(lambda _text: self.changed.emit())
        self.notes.textChanged.connect(self.changed.emit)
        self.scale.valueChanged.connect(lambda _value: self.changed.emit())
        self.tolerance.valueChanged.connect(lambda _value: self.changed.emit())
        self._mode_changed()

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        self.images.dropEvent(event)

    def _mode_changed(self, _index: int = 0) -> None:
        mode = self.mode.currentData()
        self.mode_help.setText(MODE_HELP.get(mode, ""))
        self.scale.setVisible(mode in {"composite", "inset", "stage"})
        if mode == "stage":
            self.scale.setToolTip("Size of the open center. 0.62 opens the middle 62 percent of the width and the height.")
        else:
            self.scale.setToolTip("How tall the pasted product is, as a fraction of the picture. 0.62 means 62 percent of the height.")
        self.changed.emit()

    def _choose_mask(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Mask", "", "Images (*.png *.jpg *.jpeg *.webp *.tif *.tiff)")
        if path:
            self.mask_path = path
            self._refresh_state()
            self.changed.emit()

    def _clear_mask(self) -> None:
        self.mask_path = None
        self._refresh_state()
        self.changed.emit()

    def _clear_cutout(self) -> None:
        self.cutout_path = None
        self.preview.setPixmap(QPixmap())
        self.preview.setText("No cutout yet.")
        self._refresh_state()
        self.changed.emit()

    def set_cutout_busy(self, busy: bool) -> None:
        self.plain_button.setEnabled(not busy)
        self.rembg_button.setEnabled(not busy)

    def set_cutout(self, path: str) -> None:
        self.cutout_path = path
        try:
            preview = checker_preview(load_rgba(Path(path)))
            self.preview.setText("")
            self.preview.setPixmap(pil_to_pixmap(preview))
        except Exception:
            self.preview.setText("Cutout saved, but the preview could not be shown.")
        self._refresh_state()
        self.changed.emit()

    def _refresh_state(self) -> None:
        parts = []
        if self.cutout_path and Path(self.cutout_path).is_file():
            parts.append("Cutout ready")
        if self.mask_path and Path(self.mask_path).is_file():
            parts.append(f"Mask: {Path(self.mask_path).name}")
        self.state.setText(". ".join(parts) if parts else "No cutout or mask yet.")

    def spec(self) -> ProductSpec:
        return ProductSpec(
            id=self.spec_id,
            name=self.name.text().strip() or "Product",
            notes=self.notes.toPlainText().strip(),
            mode=str(self.mode.currentData() or "reference"),
            scale=float(self.scale.value()),
            image_paths=tuple(self.images.paths),
            mask_path=self.mask_path if self.mask_path and Path(self.mask_path).is_file() else None,
            cutout_path=self.cutout_path if self.cutout_path and Path(self.cutout_path).is_file() else None,
        )

    def to_dict(self) -> dict:
        spec = self.spec()
        return {
            "id": spec.id,
            "name": spec.name,
            "notes": spec.notes,
            "mode": spec.mode,
            "scale": spec.scale,
            "tolerance": int(self.tolerance.value()),
            "images": list(spec.image_paths),
            "mask": spec.mask_path or "",
            "cutout": spec.cutout_path or "",
        }

    def load_dict(self, data: dict) -> None:
        self.spec_id = str(data.get("id") or self.spec_id)
        self.name.setText(str(data.get("name") or ""))
        self.notes.setPlainText(str(data.get("notes") or ""))
        index = self.mode.findData(data.get("mode") or "reference")
        self.mode.setCurrentIndex(index if index >= 0 else 0)
        self.scale.setValue(float(data.get("scale") or 0.62))
        self.tolerance.setValue(int(data.get("tolerance") or 34))
        self.images.set_paths([str(path) for path in data.get("images") or []])
        mask = str(data.get("mask") or "")
        cutout = str(data.get("cutout") or "")
        self.mask_path = mask if mask and Path(mask).is_file() else None
        self.cutout_path = cutout if cutout and Path(cutout).is_file() else None
        if self.cutout_path:
            self.set_cutout(self.cutout_path)
        else:
            self._refresh_state()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Batch Image Studio")
        self.resize(1280, 860)
        self.setMinimumSize(1020, 700)
        self.output_dir = default_output()
        self._loading = False
        self._threads: list[FuncThread] = []
        self.cards: list[ProductCard] = []
        self.product_serial = 1
        self.candidates: dict[str, dict] = {}
        self.order: list[str] = []
        self.items: dict[str, QListWidgetItem] = {}
        self.preview_pix = QPixmap()
        self.engine = Engine()
        self.engine.key_getter = load_api_key
        self.engine.log.connect(self.append_log)
        self.engine.candidate.connect(self.on_candidate)
        self.engine.spend.connect(self.on_spend)
        self.engine.busy.connect(self.on_busy)
        self.engine.idle.connect(self.on_idle)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self.save_session)
        self._build()
        self._loading = True
        self._apply_settings(load_settings())
        self._loading = False
        self.engine.set_cap(float(self.cap_spin.value()))
        self.discarded: set[str] = set()
        self.file_watcher = QFileSystemWatcher(self)
        self.file_watcher.directoryChanged.connect(self._schedule_file_check)
        self.file_watcher.fileChanged.connect(self._schedule_file_check)
        self._file_check = QTimer(self)
        self._file_check.setSingleShot(True)
        self._file_check.timeout.connect(self._forget_missing_files)
        self.load_manifest()
        self._key_status()
        shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        shortcut.activated.connect(self.on_generate)
        self.generate_shortcut = shortcut

    def _build(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(10)
        outer.addLayout(self._top_bar())
        self.tabs = QTabWidget()
        self.tabs.addTab(self._create_tab(), "Create")
        self.tabs.addTab(self._review_tab(), "Review")
        self.tabs.currentChanged.connect(self._on_tab)
        outer.addWidget(self.tabs, 1)
        activity = QLabel("Activity")
        activity.setObjectName("activity")
        outer.addWidget(activity)
        self.log_box = QPlainTextEdit()
        self.log_box.setObjectName("log")
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumBlockCount(400)
        self.log_box.setFixedHeight(88)
        outer.addWidget(self.log_box)

    def _top_bar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        title = QLabel("Batch Image Studio")
        title.setObjectName("title")
        row.addWidget(title)
        self.theme_box = QCheckBox("Dark theme")
        self.theme_box.setToolTip("Switch the window between the light and dark color schemes. The choice is saved on this Mac.")
        self.theme_box.toggled.connect(self._theme_toggled)
        row.addWidget(self.theme_box)
        row.addStretch(1)
        self.run_spend = QLabel("This run $0.0000 spent")
        self.run_spend.setObjectName("status")
        self.folder_total = QLabel("Folder total $0.0000")
        self.folder_total.setObjectName("status")
        self.key_label = QLabel("No key")
        self.key_label.setObjectName("warning")
        row.addWidget(self.run_spend)
        row.addWidget(self.folder_total)
        row.addWidget(self.key_label)
        self.run_spend.setToolTip(
            "The meter uses published GPT Image 2.5 token rates ($5 text input, $8 image input, $30 image output per million) "
            "and published GPT-5.4 review rates. The OpenAI invoice is the authority if prices change."
        )
        return row

    def _create_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._settings_panel())
        splitter.addWidget(self._work_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([440, 840])
        layout.addWidget(splitter, 1)
        bar = QHBoxLayout()
        self.generate_button = QPushButton("Generate")
        self.generate_button.clicked.connect(self.on_generate)
        self.generate_button.setToolTip("Command+Return")
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("stop")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.engine.stop)
        open_folder = QPushButton("Open output folder")
        open_folder.setObjectName("secondary")
        open_folder.clicked.connect(self.open_output)
        self.generate_button.setObjectName("primary")
        self.generate_button.setMinimumHeight(38)
        self.generate_button.setMinimumWidth(148)
        bar.addStretch(1)
        bar.addWidget(open_folder)
        bar.addWidget(self.stop_button)
        bar.addWidget(self.generate_button)
        layout.addLayout(bar)
        return page

    def _settings_panel(self) -> QScrollArea:
        content = QWidget()
        content.setObjectName("settings")
        form = QVBoxLayout(content)
        form.setContentsMargins(8, 4, 16, 8)
        form.setSpacing(8)
        form.addWidget(section("Account"))
        form.addWidget(QLabel("API key"))
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("Paste your OpenAI API key")
        form.addWidget(self.key_edit)
        key_buttons = QHBoxLayout()
        save_key = QPushButton("Save key")
        save_key.setObjectName("primary")
        save_key.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        save_key.clicked.connect(self.on_save_key)
        remove_key = QPushButton("Remove key")
        remove_key.setObjectName("secondary")
        remove_key.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        remove_key.clicked.connect(self.on_remove_key)
        key_buttons.addWidget(save_key)
        key_buttons.addWidget(remove_key)
        form.addLayout(key_buttons)
        form.addWidget(muted("The key is written to .env in this project folder. It is not shown again after you save it."))
        form.addWidget(section("Generation"))
        self.model = ComboBox()
        self.quality = ComboBox()
        self.size = ComboBox()
        self.background = ComboBox()
        for value, label in BACKGROUNDS:
            self.background.addItem(label, value)
        narrow_combo(self.background)
        self.model.currentIndexChanged.connect(self._model_changed)
        fill_combo(self.model, IMAGE_MODELS, IMAGE_MODELS[0][0])
        fields = QFormLayout()
        fields.setHorizontalSpacing(12)
        fields.setVerticalSpacing(10)
        fields.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        fields.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        fields.addRow("Model", self.model)
        fields.addRow("Quality", self.quality)
        fields.addRow("Size", self.size)
        fields.addRow("Background", self.background)
        self.candidates_spin = SpinBox()
        self.candidates_spin.setRange(1, 8)
        self.candidates_spin.setValue(4)
        self.parallel_spin = SpinBox()
        self.parallel_spin.setRange(1, 8)
        self.parallel_spin.setValue(2)
        self.retries_spin = SpinBox()
        self.retries_spin.setRange(0, 3)
        self.retries_spin.setValue(1)
        self.cap_spin = SpinBox()
        self.cap_spin.setRange(1, 500)
        self.cap_spin.setValue(10)
        self.cap_spin.setPrefix("$")
        fields.addRow("Candidates per product", self.candidates_spin)
        fields.addRow("Parallel requests", self.parallel_spin)
        fields.addRow("Retries after a rejection", self.retries_spin)
        fields.addRow("Spend cap", self.cap_spin)
        form.addLayout(fields)
        form.addWidget(section("Review"))
        self.screen_box = QCheckBox("Review each image before a retry")
        self.screen_box.setChecked(True)
        form.addWidget(self.screen_box)
        self.send_criteria = QCheckBox("Send the criteria to the image model")
        self.send_criteria.setChecked(True)
        form.addWidget(self.send_criteria)
        self.screen_model = ComboBox()
        fill_combo(self.screen_model, SCREEN_MODELS, SCREEN_MODELS[0][0])
        form.addWidget(QLabel("Review model"))
        form.addWidget(self.screen_model)
        form.addWidget(muted("The image model does not see this text unless the checkbox above is on. Put the scene description in the prompt."))
        self.criteria = PlainText()
        self.criteria.setPlainText(DEFAULT_CRITERIA.strip())
        self.criteria.setFixedHeight(120)
        form.addWidget(self.criteria)
        form.addWidget(section("Output"))
        self.folder_button = QPushButton("Choose output folder")
        self.folder_button.setObjectName("secondary")
        self.folder_button.clicked.connect(self.choose_folder)
        self.folder_label = muted(self.output_dir)
        form.addWidget(self.folder_button)
        form.addWidget(self.folder_label)
        form.addStretch(1)
        for widget in (
            self.model,
            self.quality,
            self.size,
            self.background,
            self.screen_model,
            self.candidates_spin,
            self.parallel_spin,
            self.retries_spin,
            self.cap_spin,
        ):
            widget.currentIndexChanged.connect(self.schedule_save) if isinstance(widget, QComboBox) else widget.valueChanged.connect(self.schedule_save)
        self.screen_box.toggled.connect(self.schedule_save)
        self.send_criteria.toggled.connect(self.schedule_save)
        self.criteria.textChanged.connect(self.schedule_save)
        self.cap_spin.valueChanged.connect(self._cap_changed)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        self._model_changed()
        return scroll

    def _work_panel(self) -> QScrollArea:
        content = QWidget()
        content.setObjectName("work")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(8)
        layout.addWidget(section("Prompt"))
        self.prompt = PlainText()
        self.prompt.setPlaceholderText("Describe the scene, the product, the lighting, and the framing.")
        self.prompt.setMinimumHeight(100)
        self.prompt.textChanged.connect(self.schedule_save)
        layout.addWidget(self.prompt)
        clear_prompt = QPushButton("Clear prompt")
        clear_prompt.setObjectName("secondary")
        clear_prompt.clicked.connect(self.prompt.clear)
        layout.addWidget(clear_prompt, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(section("Reference images"))
        layout.addWidget(muted(
            "Drop one approved example here. Every product uses it for lighting, framing, and background. "
            "For Use my scene, paste product, the first image here is the scene you already have. It is not redrawn. "
            "For Generate product in my scene, that first image is the scene that stays, and the photographs on the product card are what the model draws from. "
            "Leave the product list empty to generate from this prompt and these images only."
        ))
        self.references = ImageDrop("Drop the approved example, or any shared reference images.")
        self.references.changed.connect(self.schedule_save)
        layout.addWidget(self.references)
        clear_refs = QPushButton("Clear references")
        clear_refs.setObjectName("secondary")
        clear_refs.clicked.connect(self.references.clear_paths)
        layout.addWidget(clear_refs, 0, Qt.AlignmentFlag.AlignLeft)
        header = QHBoxLayout()
        header.addWidget(section("Products"))
        header.addStretch(1)
        add = QPushButton("Add product")
        add.clicked.connect(lambda: self.add_product())
        import_folder = QPushButton("Import folder")
        import_folder.setObjectName("secondary")
        import_folder.clicked.connect(self.import_folder)
        import_folder.setToolTip("If the folder contains subfolders, each subfolder becomes a product. Otherwise each image becomes a product.")
        remove_all = QPushButton("Remove all products")
        remove_all.setObjectName("secondary")
        remove_all.clicked.connect(self.remove_all_products)
        header.addWidget(add)
        header.addWidget(import_folder)
        header.addWidget(remove_all)
        layout.addLayout(header)
        self.product_host = QWidget()
        self.product_layout = QVBoxLayout(self.product_host)
        self.product_layout.setContentsMargins(0, 0, 0, 0)
        self.product_layout.addStretch(1)
        layout.addWidget(self.product_host)
        layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        return scroll

    def _review_tab(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        left = QVBoxLayout()
        self.filter = ComboBox()
        narrow_combo(self.filter)
        self.filter.addItem("All", "all")
        self.filter.addItem("In progress", "progress")
        self.filter.addItem("Needs a decision", "decision")
        self.filter.addItem("Approved", "approved")
        self.filter.addItem("Rejected", "rejected")
        self.filter.addItem("Errors", "errors")
        self.filter.currentIndexChanged.connect(self.apply_filter)
        left.addWidget(self.filter)
        self.gallery = QListWidget()
        self.gallery.setObjectName("gallery")
        self.gallery.setViewMode(QListWidget.ViewMode.IconMode)
        self.gallery.setIconSize(QSize(180, 180))
        self.gallery.setGridSize(QSize(210, 260))
        self.gallery.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.gallery.setMovement(QListWidget.Movement.Static)
        self.gallery.setSpacing(8)
        self.gallery.setWordWrap(True)
        self.gallery.setUniformItemSizes(True)
        self.gallery.itemSelectionChanged.connect(self.on_selection)
        self.gallery.itemDoubleClicked.connect(lambda _item: self.open_selected())
        left.addWidget(self.gallery, 1)
        layout.addLayout(left, 3)
        side_host = QWidget()
        side_host.setObjectName("work")
        side = QVBoxLayout(side_host)
        side.setContentsMargins(16, 12, 12, 12)
        side.setSpacing(8)
        self.preview = QLabel("Generated images will appear in the list.")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.preview.setWordWrap(True)
        self.preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        side.addWidget(self.preview)
        self.detail = muted("Select an image.")
        side.addWidget(self.detail)
        self.issues = PlainText()
        self.issues.setFixedHeight(80)
        self.issues.setPlaceholderText("Review notes")
        self.issues.textChanged.connect(self._review_notes_edited)
        side.addWidget(self.issues)
        self._notes_timer = QTimer(self)
        self._notes_timer.setSingleShot(True)
        self._notes_timer.timeout.connect(self.save_manifest)
        self.sent_prompt = PlainText()
        self.sent_prompt.setReadOnly(True)
        self.sent_prompt.setFixedHeight(90)
        self.sent_prompt.setPlaceholderText("The prompt sent for this image")
        side.addWidget(self.sent_prompt)
        actions = QHBoxLayout()
        self.approve_button = QPushButton("Approve")
        self.approve_button.setObjectName("primary")
        self.approve_button.clicked.connect(self.approve_selected)
        self.reject_button = QPushButton("Reject")
        self.reject_button.setObjectName("secondary")
        self.reject_button.clicked.connect(self.reject_selected)
        another = QPushButton("Generate another from the original brief")
        another.setObjectName("secondary")
        another.clicked.connect(self.another_selected)
        actions.addWidget(self.approve_button)
        actions.addWidget(self.reject_button)
        side.addLayout(actions)
        side.addWidget(another)
        side.addWidget(muted(
            "This asks for a new image from the original brief. The selected image is attached only as the look to stay close to. "
            "It is not a chat, and the rejected pictures are still left out."
        ))
        self.change_text = PlainText()
        self.change_text.setFixedHeight(70)
        self.change_text.setPlaceholderText("Describe one change to the selected image. The original brief stays in the request.")
        side.addWidget(self.change_text)
        apply_change = QPushButton("Apply a change to this image")
        apply_change.setObjectName("secondary")
        apply_change.clicked.connect(self.change_selected)
        side.addWidget(apply_change)
        file_row = QHBoxLayout()
        open_image = QPushButton("Open image")
        open_image.setObjectName("secondary")
        open_image.clicked.connect(self.open_selected)
        reveal = QPushButton("Show in Finder")
        reveal.setObjectName("secondary")
        reveal.clicked.connect(self.reveal_selected)
        remove_image = QPushButton("Remove image")
        remove_image.setObjectName("secondary")
        remove_image.clicked.connect(self.remove_selected)
        file_row.addWidget(open_image)
        file_row.addWidget(reveal)
        file_row.addWidget(remove_image)
        side.addLayout(file_row)
        side.addWidget(muted(
            "Approving copies the file into the approved folder. Rejecting leaves the file where it is. "
            "Remove image deletes the file from the output folder and from this list. "
            "The reviewer can miss errors. Your decision is the one that counts."
        ))
        self.review_scroll = QScrollArea()
        self.review_scroll.setWidgetResizable(True)
        self.review_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.review_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.review_scroll.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.review_scroll.setWidget(side_host)
        layout.addWidget(self.review_scroll, 2)
        return page

    def _on_tab(self, index: int) -> None:
        if self.tabs.widget(index) is self.review_scroll.parentWidget():
            self._scale_preview()
            self.review_scroll.verticalScrollBar().setValue(0)

    def _cap_changed(self, value: int) -> None:
        self.engine.set_cap(float(value))
        self.on_spend(self.engine.spent, self.engine.reserved)

    def _model_changed(self, _index: int = 0) -> None:
        if not hasattr(self, "quality"):
            return
        model = str(self.model.currentData() or IMAGE_MODELS[0][0])
        fill_combo(self.quality, [(item, item) for item in qualities_for(model)], str(self.quality.currentData() or "high"))
        fill_combo(self.size, [(item, item) for item in sizes_for(model)], str(self.size.currentData() or "1024x1024"))
        self.schedule_save()

    def _theme_toggled(self, checked: bool) -> None:
        self._apply_theme("dark" if checked else "light")
        self.schedule_save()

    def _apply_theme(self, theme: str) -> None:
        apply_theme(theme)
        for record in self.candidates.values():
            self._render_item(record)

    def schedule_save(self, *_args) -> None:
        if self._loading:
            return
        self._save_timer.start(800)

    def _apply_settings(self, settings: dict) -> None:
        self.prompt.setPlainText(str(settings.get("prompt") or ""))
        self.criteria.setPlainText(str(settings.get("criteria") or DEFAULT_CRITERIA.strip()))
        self.send_criteria.setChecked(bool(settings.get("send_criteria", True)))
        self.screen_box.setChecked(bool(settings.get("screen", True)))
        fill_combo(self.model, IMAGE_MODELS, str(settings.get("model") or IMAGE_MODELS[0][0]))
        self._model_changed()
        fill_combo(self.quality, [(item, item) for item in qualities_for(str(self.model.currentData()))], str(settings.get("quality") or "high"))
        fill_combo(self.size, [(item, item) for item in sizes_for(str(self.model.currentData()))], str(settings.get("size") or "1024x1024"))
        index = self.background.findData(settings.get("background") or "opaque")
        self.background.setCurrentIndex(index if index >= 0 else 0)
        fill_combo(self.screen_model, SCREEN_MODELS, str(settings.get("screen_model") or SCREEN_MODELS[0][0]))
        self.candidates_spin.setValue(int(settings.get("candidates") or 4))
        self.parallel_spin.setValue(int(settings.get("parallel") or 2))
        self.retries_spin.setValue(int(settings.get("retries") or 1))
        self.cap_spin.setValue(int(float(settings.get("spend_cap") or 10)))
        self.output_dir = str(settings.get("output_dir") or default_output())
        self.folder_label.setText(self.output_dir)
        self.references.set_paths([str(path) for path in settings.get("references") or []])
        for data in settings.get("products") or []:
            if isinstance(data, dict):
                self.add_product(data=data, notify=False)
        self.product_serial = len(self.cards) + 1
        theme = str(settings.get("theme") or "light")
        if theme not in {"light", "dark"}:
            theme = "light"
        self.theme_box.blockSignals(True)
        self.theme_box.setChecked(theme == "dark")
        self.theme_box.blockSignals(False)
        self._apply_theme(theme)
        geometry = str(settings.get("geometry") or "")
        if geometry:
            self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii")))

    def save_session(self) -> None:
        if self._loading:
            return
        settings = load_settings()
        settings.update(
            {
                "prompt": self.prompt.toPlainText(),
                "criteria": self.criteria.toPlainText(),
                "send_criteria": self.send_criteria.isChecked(),
                "screen": self.screen_box.isChecked(),
                "references": list(self.references.paths),
                "products": [card.to_dict() for card in self.cards],
                "model": self.model.currentData(),
                "quality": self.quality.currentData(),
                "size": self.size.currentData(),
                "background": self.background.currentData(),
                "candidates": self.candidates_spin.value(),
                "parallel": self.parallel_spin.value(),
                "retries": self.retries_spin.value(),
                "spend_cap": self.cap_spin.value(),
                "screen_model": self.screen_model.currentData(),
                "output_dir": self.output_dir,
                "geometry": bytes(self.saveGeometry().toBase64()).decode("ascii"),
                "theme": "dark" if self.theme_box.isChecked() else "light",
            }
        )
        try:
            save_settings(settings)
        except OSError as exc:
            self.append_log(f"Could not save settings. {exc}")

    def add_product(self, name: str = "", images: list[str] | None = None, data: dict | None = None, notify: bool = True) -> ProductCard:
        card = ProductCard()
        card.changed.connect(self.schedule_save)
        card.remove_requested.connect(self.remove_product)
        card.cutout_requested.connect(self.run_cutout)
        if data:
            card.load_dict(data)
        else:
            card.name.setText(name or f"Product {self.product_serial}")
            self.product_serial += 1
            if images:
                card.images.set_paths(images)
        self.cards.append(card)
        self.product_layout.insertWidget(self.product_layout.count() - 1, card)
        if notify:
            self.schedule_save()
        return card

    def remove_product(self, card: ProductCard) -> None:
        if card in self.cards:
            self.cards.remove(card)
        card.setParent(None)
        card.deleteLater()
        self.schedule_save()

    def remove_all_products(self) -> None:
        if not self.cards:
            return
        answer = QMessageBox.question(
            self,
            "Remove products",
            "Remove every product from this window? The image files stay where they are.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        for card in list(self.cards):
            self.remove_product(card)

    def import_folder(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Import products", str(Path.home()))
        if not directory:
            return
        root = Path(directory)
        subs = [path for path in sorted(root.iterdir()) if path.is_dir() and not path.name.startswith(".") and path.name != "_workspace"]
        loose = list_images(root)
        made = 0
        if subs:
            for sub in subs:
                images = list_images(sub)
                if images:
                    self.add_product(name=sub.name, images=[str(path) for path in images], notify=False)
                    made += 1
            if loose:
                self.append_log(f"Ignored {len(loose)} image(s) sitting directly in that folder. Subfolders were used as products.")
        elif loose:
            for image in loose:
                self.add_product(name=image.stem, images=[str(image)], notify=False)
                made += 1
        self.schedule_save()
        if made == 0:
            QMessageBox.information(self, "Import folder", "No images were found in that folder.")
        else:
            self.append_log(f"Imported {made} product(s).")

    def run_cutout(self, card: ProductCard, kind: str) -> None:
        if not card.images.paths:
            QMessageBox.information(self, "Cutout", "Drop a product photograph on this product first.")
            return
        source = Path(card.images.paths[0])
        dest = CUTOUT_DIR / f"{card.spec_id}.png"
        tolerance = int(card.tolerance.value())

        def work() -> str:
            image = load_rgba(source)
            cut = checker_source(image, kind, tolerance)
            CUTOUT_DIR.mkdir(parents=True, exist_ok=True)
            cut.save(dest, "PNG")
            return str(dest)

        if kind == "rembg":
            self.append_log("Cutting out the background locally. The first time can take several minutes while the model downloads.")
        thread = FuncThread(work)
        thread.succeeded.connect(lambda path, cid=card.spec_id: self.finish_cutout(cid, path))
        thread.failed.connect(lambda message, cid=card.spec_id: self.fail_cutout(cid, message))
        thread.finished.connect(lambda thread=thread: self._drop_thread(thread))
        self._threads.append(thread)
        card.set_cutout_busy(True)
        thread.start()

    def _drop_thread(self, thread: FuncThread) -> None:
        if thread in self._threads:
            self._threads.remove(thread)

    def _card(self, spec_id: str) -> ProductCard | None:
        for card in self.cards:
            if card.spec_id == spec_id:
                return card
        return None

    def finish_cutout(self, spec_id: str, path: str) -> None:
        card = self._card(spec_id)
        if card is None:
            return
        card.set_cutout_busy(False)
        card.set_cutout(path)
        self.append_log(f"Cutout ready for {card.name.text().strip() or 'the product'}. Check the preview before generating.")

    def fail_cutout(self, spec_id: str, message: str) -> None:
        card = self._card(spec_id)
        if card is not None:
            card.set_cutout_busy(False)
        QMessageBox.warning(self, "Cutout", message)

    def choose_folder(self) -> None:
        if self.engine.is_busy():
            return
        path = QFileDialog.getExistingDirectory(self, "Output folder", self.output_dir)
        if not path:
            return
        self.output_dir = path
        self.folder_label.setText(path)
        self.load_manifest()
        self.schedule_save()

    def open_output(self) -> None:
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.output_dir))

    def on_save_key(self) -> None:
        try:
            save_api_key(self.key_edit.text())
        except ValueError as exc:
            QMessageBox.warning(self, "API key", str(exc))
            return
        except OSError as exc:
            QMessageBox.warning(self, "API key", f"Could not write .env. {exc}")
            return
        self.key_edit.clear()
        self.engine.drop_client()
        self._key_status()
        self.append_log("API key saved in .env.")

    def on_remove_key(self) -> None:
        answer = QMessageBox.question(self, "Remove key", "Remove the API key from .env?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            clear_api_key()
        except OSError as exc:
            QMessageBox.warning(self, "API key", str(exc))
            return
        self.engine.drop_client()
        self._key_status()
        self.append_log("API key removed.")

    def _key_status(self) -> None:
        if load_api_key():
            self.key_label.setText("Key saved")
            self.key_label.setObjectName("muted")
        else:
            self.key_label.setText("No key")
            self.key_label.setObjectName("warning")
            self.append_log("Put your OpenAI API key in the field and click Save key. It is stored in .env in this folder.")
        self.key_label.style().unpolish(self.key_label)
        self.key_label.style().polish(self.key_label)

    def append_log(self, line: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_box.appendPlainText(f"{stamp}  {line}")
        folder = Path(self.output_dir)
        if folder.exists():
            try:
                with (folder / "log.txt").open("a", encoding="utf-8") as handle:
                    handle.write(f"{stamp}  {line}\n")
            except OSError:
                pass

    def on_spend(self, spent: float, reserved: float) -> None:
        cap = float(self.cap_spin.value()) if hasattr(self, "cap_spin") else 0
        self.run_spend.setText(f"This run {money(spent)} spent, {money(reserved)} in flight, cap {money(cap)}")

    def on_busy(self, busy: bool) -> None:
        self.generate_button.setEnabled(not busy)
        self.stop_button.setEnabled(busy)
        self.folder_button.setEnabled(not busy)

    def on_idle(self) -> None:
        was_running = not self.generate_button.isEnabled()
        self.on_busy(False)
        if was_running:
            self.append_log("Ready.")

    def on_generate(self) -> None:
        if self.engine.is_busy():
            return
        brief = self.prompt.toPlainText().strip()
        products = [card.spec() for card in self.cards]
        if not products:
            products = [
                ProductSpec(
                    id="batch",
                    name="Batch",
                    notes="",
                    mode="reference",
                    scale=0.62,
                    image_paths=(),
                    mask_path=None,
                    cutout_path=None,
                )
            ]
        problems = self._prepare_products(products)
        if problems:
            QMessageBox.warning(self, "Generate", problems)
            return
        review_on = self.screen_box.isChecked() and bool(self.criteria.toPlainText().strip())
        generates = any(product.mode != "inset" for product in products)
        if (generates or review_on) and not load_api_key():
            QMessageBox.warning(self, "API key", "Save an API key first. It is stored in .env in this project folder.")
            return
        if generates and not brief:
            QMessageBox.warning(self, "Prompt", "Write a prompt first.")
            return
        context = self._context(brief)
        cap = float(self.cap_spin.value())
        n_approved = len(context.approved_paths)
        wave = 0.0
        ceiling = 0.0
        largest = 0.0
        job_count = 0
        for product in products:
            incoming = self._input_count(product, n_approved)
            size = context.size
            copies = 1 if product.mode == "inset" else context.candidates
            job_count += copies
            if product.mode == "lock" and product.image_paths:
                try:
                    size = locked_output_size(Path(product.image_paths[0]))
                except Exception:
                    size = context.size
            elif product.mode == "stage" and self.references.paths:
                try:
                    size = locked_output_size(Path(self.references.paths[0]))
                except Exception:
                    size = context.size
            image_cost = 0.0 if product.mode == "inset" else estimate_image_call(context.quality, size, incoming)
            screen_images = 1 + min(4, len(product.image_paths)) + min(2, n_approved)
            review_cost = estimate_screen_call(context.screen_model, screen_images) if context.screen and context.criteria.strip() else 0
            largest = max(largest, image_cost + review_cost)
            wave += copies * (image_cost + review_cost)
            extra = 0 if product.mode == "inset" else context.retries
            ceiling += copies * (image_cost + review_cost) * (1 + extra)
        if largest > cap:
            QMessageBox.warning(
                self,
                "Spend cap",
                f"The allowance held for one image is {money(largest)}. The cap is {money(cap)}. "
                "Raise the cap, or choose a lower quality or a smaller size.",
            )
            return
        screen_line = "Review is on." if context.screen and context.criteria.strip() else "Review is off, or the criteria box is empty. Nothing will be rejected automatically."
        paste_line = ""
        if any(product.mode == "inset" for product in products):
            paste_line = (
                "Use my scene, paste product does not send an image request. "
                "One picture is saved for each of those products, using the first reference image as the scene.\n"
            )
        if any(product.mode == "stage" for product in products):
            paste_line += (
                "Generate product in my scene keeps the first reference image outside the center "
                "and asks the model to draw a new product there.\n"
            )
        answer = QMessageBox.question(
            self,
            "Generate images",
            (
                f"This can create up to {job_count} images before retries.\n"
                f"Allowance held before retries and review settle: about {money(wave)}.\n"
                f"If every generated image uses every retry, the allowance could reach about {money(ceiling)}.\n"
                f"The cap is {money(cap)}. The run stops when the cap is reached.\n"
                f"{paste_line}"
                f"{screen_line}\n"
                "The reviewer can miss errors. Approve the images yourself.\n"
                "The OpenAI invoice is the authority. API use is billed separately from ChatGPT."
            ),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.engine.parallel = context.parallel
        try:
            self.engine.start_batch(context, products)
        except RuntimeError as exc:
            QMessageBox.warning(self, "Generate", str(exc))

    def _prepare_products(self, products: list[ProductSpec]) -> str:
        problems: list[str] = []
        seen: set[str] = set()
        for product in products:
            if product.id in seen:
                problems.append(f"{product.name}: this product is listed twice. Remove it and add it again.")
            seen.add(product.id)
            if product.mode == "lock":
                problems.extend(self._lock_problems(product))
            elif product.mode == "composite":
                problems.extend(self._composite_problems(product))
            elif product.mode == "inset":
                problems.extend(self._inset_problems(product))
            elif product.mode == "stage":
                problems.extend(self._stage_problems(product))
        if len(problems) > 12:
            hidden = len(problems) - 12
            problems = problems[:12]
            problems.append(f"{hidden} more.")
        return "\n".join(problems)

    def _lock_problems(self, product: ProductSpec) -> list[str]:
        if not product.image_paths:
            return [f"{product.name}: add the product photograph before locking pixels."]
        if product.mask_path:
            return []
        if product.cutout_path:
            try:
                cutout = load_rgba(Path(product.cutout_path))
            except Exception as exc:
                return [f"{product.name}: could not read the cutout. {exc}"]
            if not is_cutout(cutout):
                return [f"{product.name}: the cutout has no transparent backdrop."]
            return []
        try:
            image = load_rgba(Path(product.image_paths[0]))
        except Exception as exc:
            return [f"{product.name}: could not read the photograph. {exc}"]
        if is_cutout(image):
            return [f"{product.name}: this file is a cutout. Use New scene, paste product."]
        return [f"{product.name}: create a cutout or choose a mask before locking pixels."]

    def _composite_problems(self, product: ProductSpec) -> list[str]:
        path = product.cutout_path or (product.image_paths[0] if product.image_paths else "")
        if not path:
            return [f"{product.name}: add a product photograph, then create a cutout."]
        try:
            image = load_rgba(Path(path))
        except Exception as exc:
            return [f"{product.name}: could not read the photograph. {exc}"]
        if not is_cutout(image):
            return [
                f"{product.name}: create a cutout, or use a PNG that already has transparency, before pasting the product."
            ]
        return []

    def _context(self, brief: str) -> RunContext:
        criteria = self.criteria.toPlainText().strip()
        return RunContext(
            brief=brief,
            requirements=criteria if self.send_criteria.isChecked() else "",
            criteria=criteria,
            approved_paths=tuple(self.references.paths),
            model=str(self.model.currentData()),
            quality=str(self.quality.currentData()),
            size=str(self.size.currentData()),
            background=str(self.background.currentData()),
            output_dir=self.output_dir,
            screen=self.screen_box.isChecked(),
            screen_model=str(self.screen_model.currentData()),
            retries=int(self.retries_spin.value()),
            parallel=int(self.parallel_spin.value()),
            candidates=int(self.candidates_spin.value()),
            run_id=uuid.uuid4().hex[:8],
        )

    def _inset_problems(self, product: ProductSpec) -> list[str]:
        problems: list[str] = []
        if not self.references.paths:
            problems.append(
                f"{product.name}: drop the scene you already have into Reference images. The first image is the one that is kept."
            )
        problems.extend(self._composite_problems(product))
        return problems

    def _stage_problems(self, product: ProductSpec) -> list[str]:
        problems: list[str] = []
        if not self.references.paths:
            problems.append(
                f"{product.name}: drop the scene you already have into Reference images. The first image is the one that stays."
            )
        if not product.image_paths:
            problems.append(f"{product.name}: add photographs of the product the model should draw.")
        return problems

    def _input_count(self, product: ProductSpec, n_approved: int) -> int:
        if product.mode == "inset":
            return 0
        if product.mode == "composite":
            return min(16, n_approved)
        if product.mode == "stage":
            return min(16, (1 if n_approved else 0) + len(product.image_paths))
        return min(16, len(product.image_paths) + n_approved)

    def on_candidate(self, record: dict) -> None:
        cid = str(record.get("id") or "")
        if not cid:
            return
        if cid in self.discarded:
            self._delete_saved_files(record)
            return
        path = str(record.get("path") or "")
        if path and not Path(path).is_file():
            self.discarded.add(cid)
            self._drop_record(cid)
            self.save_manifest()
            self._sync_file_watches()
            return
        existing = self.candidates.get(cid)
        if existing and existing.get("status") in {"approved", "rejected"}:
            record["status"] = existing["status"]
            if existing.get("approved_path"):
                record["approved_path"] = existing["approved_path"]
        if existing and "review_notes" in existing:
            record["review_notes"] = existing["review_notes"]
        self.candidates[cid] = record
        if cid not in self.order:
            self.order.append(cid)
        self._render_item(record)
        self.save_manifest()
        self.refresh_totals()
        self._review_title()
        if self._selected_id() == cid:
            self.show_record(record)
        self._sync_file_watches()

    def _render_item(self, record: dict) -> None:
        cid = str(record["id"])
        item = self.items.get(cid)
        if item is None:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, cid)
            item.setSizeHint(QSize(200, 250))
            self.gallery.addItem(item)
            self.items[cid] = item
        kind = str(record.get("kind") or "fresh")
        if kind == "fresh":
            title = f"Candidate {int(record.get('index') or 0) + 1}"
        elif kind == "retry":
            title = f"Retry {int(record.get('attempt') or 1)}"
        elif kind == "change":
            title = "Change"
        else:
            title = "New from brief"
        status = str(record.get("status") or "generating")
        item.setText(f"{record.get('product_name') or 'Image'}\n{title}\n{STATUS_TITLES.get(status, status)}")
        item.setForeground(QColor(status_color(status)))
        icon = thumbnail_pixmap(str(record.get("path") or ""), 180)
        if not icon.isNull():
            item.setIcon(QIcon(icon))
        self.apply_filter_to(item, status)

    def apply_filter(self, _index: int = 0) -> None:
        for cid, item in self.items.items():
            status = str(self.candidates.get(cid, {}).get("status") or "")
            self.apply_filter_to(item, status)

    def apply_filter_to(self, item: QListWidgetItem, status: str) -> None:
        choice = str(self.filter.currentData() or "all")
        groups = {
            "all": None,
            "progress": {"generating", "reviewing"},
            "decision": {"passed", "failed", "unreviewed"},
            "approved": {"approved"},
            "rejected": {"rejected"},
            "errors": {"error", "cap"},
        }
        allowed = groups.get(choice)
        item.setHidden(allowed is not None and status not in allowed)

    def _review_title(self) -> None:
        waiting = sum(1 for record in self.candidates.values() if record.get("status") in {"passed", "failed", "unreviewed"})
        self.tabs.setTabText(1, f"Review ({waiting})" if waiting else "Review")

    def load_manifest(self) -> None:
        self.gallery.clear()
        self.items.clear()
        self.candidates.clear()
        self.order.clear()
        path = Path(self.output_dir) / "manifest.json"
        if path.is_file():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self.append_log("Could not read manifest.json in the output folder.")
                payload = {}
            removed = 0
            for record in payload.get("candidates") or []:
                if not isinstance(record, dict) or not record.get("id"):
                    continue
                normalized = self._normalize_loaded(record)
                image_path = str(normalized.get("path") or "")
                if image_path and not Path(image_path).is_file():
                    removed += 1
                    continue
                cid = str(normalized["id"])
                self.order.append(cid)
                self.candidates[cid] = normalized
                self._render_item(normalized)
            if removed:
                self.save_manifest()
                if removed == 1:
                    self.append_log("Removed an image that is no longer in the output folder.")
                else:
                    self.append_log(f"Removed {removed} images that are no longer in the output folder.")
        self.refresh_totals()
        self._review_title()
        self.show_record(None)
        self._sync_file_watches()

    def _normalize_loaded(self, record: dict) -> dict:
        status = str(record.get("status") or "")
        path = str(record.get("path") or "")
        if status in {"generating", "reviewing"} and not path:
            record["status"] = "error"
            record["error"] = "This run was interrupted before the image was saved."
        elif status == "reviewing" and path:
            record["status"] = "unreviewed"
        record["issues"] = list(record.get("issues") or [])
        return record

    def save_manifest(self) -> None:
        folder = Path(self.output_dir)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            payload = {"version": 1, "candidates": [self.candidates[cid] for cid in self.order if cid in self.candidates]}
            atomic_write(folder / "manifest.json", json.dumps(payload, indent=2))
        except OSError as exc:
            self.append_log(f"Could not write manifest.json. {exc}")

    def refresh_totals(self) -> None:
        total = sum(float(record.get("cost") or 0) for record in self.candidates.values())
        self.folder_total.setText(f"Folder total {money(total)}")

    def _selected_id(self) -> str:
        items = self.gallery.selectedItems()
        if not items:
            return ""
        return str(items[0].data(Qt.ItemDataRole.UserRole) or "")

    def current(self) -> dict | None:
        cid = self._selected_id()
        if not cid:
            return None
        return self.candidates.get(cid)

    def on_selection(self) -> None:
        self.show_record(self.current())

    def show_record(self, record: dict | None) -> None:
        self.preview_pix = QPixmap()
        if not record:
            self._shown_id = ""
            self.preview.setPixmap(QPixmap())
            self.preview.setText("Generated images will appear in the list.")
            self.preview.setFixedHeight(72)
            self.detail.setText("Select an image.")
            self._set_review_notes("")
            self.sent_prompt.clear()
            return
        cid = str(record.get("id") or "")
        same = bool(cid) and cid == getattr(self, "_shown_id", "")
        path = str(record.get("path") or "")
        if path and Path(path).is_file():
            self.preview_pix = QPixmap(path)
            self.preview.setText("")
            self._scale_preview()
            if not same:
                self.review_scroll.verticalScrollBar().setValue(0)
        else:
            self.preview.setPixmap(QPixmap())
            self.preview.setText(str(record.get("error") or "No image file yet."))
            self.preview.setFixedHeight(72)
        status = STATUS_TITLES.get(str(record.get("status") or ""), str(record.get("status") or ""))
        bits = [
            str(record.get("product_name") or ""),
            status,
            str(record.get("model") or ""),
            money(float(record.get("cost") or 0)),
        ]
        if record.get("approved_path"):
            bits.append("Copied to the approved folder")
        if record.get("notes"):
            bits.append(str(record["notes"]))
        if record.get("error") and record.get("status") not in {"generating", "reviewing"}:
            bits.append(str(record["error"]))
        issues = [str(item).strip() for item in record.get("issues") or [] if str(item).strip()]
        if issues:
            bits.append("Reviewer found " + "; ".join(issues))
        instruction = str(record.get("instruction") or "")
        if instruction and instruction not in " ".join(bits) and (
            record.get("kind") in {"retry", "change"} or str(record.get("status")) == "failed"
        ):
            bits.append(instruction)
        self.detail.setText("\n".join(bit for bit in bits if bit))
        if not same or not _keyboard_selected(self.issues):
            self._set_review_notes(str(record.get("review_notes") or ""))
        self._shown_id = cid
        prompt_text = str(record.get("prompt") or "")
        if (not same or not _keyboard_selected(self.sent_prompt)) and self.sent_prompt.toPlainText() != prompt_text:
            self.sent_prompt.setPlainText(prompt_text)
        mode = str(record.get("mode") or "")
        if mode == "lock":
            self.change_text.setPlaceholderText("This can change the scene. The product pixels stay in place.")
        elif mode == "composite":
            self.change_text.setPlaceholderText("This changes the scene. The product is pasted again and is not redrawn. Scale stays as it was.")
        elif mode == "inset":
            self.change_text.setPlaceholderText("This picture is a paste. Change Height and generate again. A text change cannot move the product.")
        elif mode == "stage":
            self.change_text.setPlaceholderText("This can change the product in the center. The scene outside that center stays.")
        else:
            self.change_text.setPlaceholderText("Describe one change. The original brief stays in the request.")

    def _set_review_notes(self, text: str) -> None:
        self.issues.blockSignals(True)
        self.issues.setPlainText(text)
        self.issues.blockSignals(False)

    def _review_notes_edited(self) -> None:
        record = self.current()
        if not record:
            return
        record["review_notes"] = self.issues.toPlainText()
        self._notes_timer.start(400)

    def _scale_preview(self) -> None:
        if self.preview_pix.isNull() or not hasattr(self, "review_scroll"):
            return
        margins = self.review_scroll.widget().layout().contentsMargins()
        width = self.review_scroll.viewport().width() - margins.left() - margins.right()
        if width < 80:
            return
        scaled = self.preview_pix.scaledToWidth(width, Qt.TransformationMode.SmoothTransformation)
        self.preview.setPixmap(scaled)
        self.preview.setFixedHeight(scaled.height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._scale_preview()

    def _schedule_file_check(self, _path: str = "") -> None:
        self._file_check.start(200)

    def _sync_file_watches(self) -> None:
        if not hasattr(self, "file_watcher"):
            return
        directories: set[str] = set()
        files: set[str] = set()
        root = Path(self.output_dir)
        if root.is_dir():
            directories.add(str(root))
        for record in self.candidates.values():
            for key in ("path", "approved_path"):
                raw = str(record.get(key) or "")
                if not raw:
                    continue
                file = Path(raw)
                if file.is_file():
                    files.add(str(file))
                if file.parent.is_dir():
                    directories.add(str(file.parent))
        self._apply_watches(set(self.file_watcher.directories()), directories)
        self._apply_watches(set(self.file_watcher.files()), files)

    def _apply_watches(self, current: set[str], wanted: set[str]) -> None:
        extra = [path for path in current if path not in wanted]
        missing = [path for path in wanted if path not in current]
        if extra:
            self.file_watcher.removePaths(extra)
        if missing:
            self.file_watcher.addPaths(missing)

    def _forget_missing_files(self) -> None:
        gone = [
            cid
            for cid, record in self.candidates.items()
            if str(record.get("path") or "") and not Path(str(record.get("path") or "")).is_file()
        ]
        if not gone:
            self._sync_file_watches()
            return
        for cid in gone:
            self.discarded.add(cid)
            self._drop_record(cid)
        self.save_manifest()
        self._sync_file_watches()
        if len(gone) == 1:
            self.append_log("Removed an image that is no longer in the output folder.")
        else:
            self.append_log(f"Removed {len(gone)} images that are no longer in the output folder.")

    def _drop_record(self, cid: str) -> None:
        self.candidates.pop(cid, None)
        if cid in self.order:
            self.order.remove(cid)
        item = self.items.pop(cid, None)
        if item is not None:
            row = self.gallery.row(item)
            if row >= 0:
                self.gallery.takeItem(row)
        if self._selected_id() == cid or getattr(self, "_shown_id", "") == cid:
            self.show_record(None)
        self.refresh_totals()
        self._review_title()

    def _delete_saved_files(self, record: dict) -> str:
        problems: list[str] = []
        for key in ("path", "approved_path"):
            raw = str(record.get(key) or "")
            if not raw:
                continue
            file = Path(raw)
            if not file.is_file():
                continue
            try:
                file.unlink()
            except OSError as exc:
                problems.append(f"Could not delete {file.name}. {exc}")
        return " ".join(problems)

    def remove_selected(self) -> None:
        record = self.current()
        if not record:
            return
        raw = str(record.get("path") or "")
        name = Path(raw).name if raw else str(record.get("product_name") or "this image")
        approved = str(record.get("approved_path") or "")
        extra = ""
        if approved and Path(approved).is_file():
            extra = " The copy in the approved folder is deleted too."
        answer = QMessageBox.question(
            self,
            "Remove image",
            f"Delete {name} from the output folder and remove it from this list?{extra} This cannot be undone.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        cid = str(record.get("id") or "")
        self.discarded.add(cid)
        self.engine.note_decision(cid)
        error = self._delete_saved_files(record)
        self._drop_record(cid)
        self.save_manifest()
        self._sync_file_watches()
        if error:
            self.append_log(error)
            QMessageBox.warning(self, "Remove image", error)
            return
        self.append_log(f"Removed {name}.")

    def approve_selected(self) -> None:
        record = self.current()
        if not record:
            return
        path = str(record.get("path") or "")
        if not path or not Path(path).is_file():
            QMessageBox.information(self, "Approve", "This image has no file yet.")
            return
        dest_dir = Path(str(record.get("output_dir") or self.output_dir)) / "approved" / safe_slug(str(record.get("product_name") or "image"))
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / Path(path).name
            shutil.copy2(path, dest)
        except OSError as exc:
            QMessageBox.warning(self, "Approve", str(exc))
            return
        record["status"] = "approved"
        record["approved_path"] = str(dest)
        self.engine.note_decision(str(record.get("id") or ""))
        self.on_candidate(record)
        self.append_log(f"Approved {Path(path).name}.")

    def reject_selected(self) -> None:
        record = self.current()
        if not record:
            return
        record["status"] = "rejected"
        self.engine.note_decision(str(record.get("id") or ""))
        self.on_candidate(record)

    def another_selected(self) -> None:
        record = self.current()
        if not record:
            return
        self.engine.request_another(record)

    def change_selected(self) -> None:
        record = self.current()
        if not record:
            return
        self.engine.request_change(record, self.change_text.toPlainText())

    def open_selected(self) -> None:
        record = self.current()
        path = str(record.get("path") or "") if record else ""
        if path and Path(path).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def reveal_selected(self) -> None:
        record = self.current()
        path = str(record.get("path") or "") if record else ""
        if path and Path(path).is_file():
            subprocess.run(["open", "-R", path], check=False)

    def closeEvent(self, event) -> None:
        self.save_session()
        if self.engine.is_busy():
            answer = QMessageBox.question(
                self,
                "Stop the run",
                "Images are still being generated. Stop them and close?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.engine.blockSignals(True)
            self.engine.shutdown(wait=False)
            event.accept()
            QTimer.singleShot(50, lambda: os._exit(0))
            return
        self.engine.shutdown(wait=True)
        event.accept()


def checker_source(image: Image.Image, kind: str, tolerance: int) -> Image.Image:
    from .imaging import flood_cutout, rembg_cutout

    if kind == "plain":
        return flood_cutout(image, tolerance)
    return rembg_cutout(image)
