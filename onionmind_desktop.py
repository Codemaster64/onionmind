"""THESIS: Onionmind is one calm local-work loop, not a chat page ringed by dashboards.
OWN-WORLD: Matte warm graphite planes, fine charcoal seams, bone type, and aubergine selection; native controls stay compact and square-edged.
STORY: Choose a repository and Onionmind model, describe the work, watch an interruptible Chat or Agent run, then verify observed context, changes, and activity.
FIRST VIEWPORT: A 224px project/session rail, dominant open transcript with terminal drawer and composer, and a 292px three-tab inspector under a compact state toolbar.
FORM: Approved balanced workbench A; Operate mode; seed approved-onionmind-workbench-a.
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, and DESIGN.md
"""

from __future__ import annotations

import argparse
import base64
import copy
import dataclasses
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from PySide6.QtCore import (
    QByteArray,
    QObject,
    QPointF,
    QProcess,
    QRectF,
    QSettings,
    QSize,
    QStandardPaths,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
    QFontDatabase,
    QIcon,
    QKeyEvent,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QShortcut,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


APP_NAME = "Onionmind"
APP_ID = "OnionmindDesktop"
MODULE_DIR = Path(__file__).resolve().parent
ACCENT = "#8d6aa0"
ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
MAX_TEXT_FILE_BYTES = 64 * 1024
MAX_TEXT_TOTAL_BYTES = 256 * 1024
MAX_IMAGE_FILE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_TOTAL_BYTES = 24 * 1024 * 1024
MAX_IMAGE_COUNT = 4
SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", ".venv", "venv", "dist", "build", "__pycache__"}
_SCREENSHOT_PATH: Optional[str] = None


STYLE_SHEET = r"""
* {
    color: #eee8df;
}
QMainWindow, QDialog { background: #181715; }
QWidget#windowRoot, QWidget#centerPane, QWidget#transcriptViewport { background: #191816; }
QWidget#toolbar { background: #1c1b19; border-bottom: 1px solid #37342f; }
QWidget#leftRail { background: #1d1c1a; border-right: 1px solid #37342f; }
QWidget#inspector { background: #1c1b19; border-left: 1px solid #37342f; }
QFrame#terminalPane { background: #171615; border-top: 1px solid #403c36; }
QFrame#composerFrame { background: #211f1d; border-top: 1px solid #403c36; }
QFrame#card { background: #23211f; border: 1px solid #403c36; border-radius: 5px; }
QFrame#toolCard { background: #1e1d1b; border: 1px solid #423e38; border-radius: 5px; }
QFrame#modeSwitch { background: #191816; border: 1px solid #403c36; border-radius: 4px; }
QFrame#separator { background: #3b3833; min-width: 1px; max-width: 1px; }
QLabel { background: transparent; }
QLabel#brand { color: #f5efe7; font-size: 11pt; font-weight: 650; }
QLabel#sectionTitle { color: #aaa39a; font-size: 8.5pt; font-weight: 650; }
QLabel#muted, QLabel#meta, QLabel#disclosure { color: #aaa39a; }
QLabel#title { color: #f2ece4; font-weight: 650; }
QLabel#avatarUser { background: #74579a; color: #f9f5ef; border-radius: 16px; font-weight: 650; }
QLabel#avatarAssistant { background: #282327; color: #b791c9; border: 1px solid #624c6c; border-radius: 16px; font-weight: 700; }
QLabel#attachmentLabel { color: #cdbbd5; background: #2c252e; border: 1px solid #493d4e; border-radius: 3px; padding: 3px 7px; }
QLabel#success { color: #84c08f; }
QLabel#danger { color: #d88675; }
QLabel#accent { color: #c3a1d3; }
QPushButton, QToolButton, QComboBox, QLineEdit {
    background: #24221f;
    border: 1px solid #45413b;
    border-radius: 4px;
    padding: 5px 9px;
    min-height: 18px;
}
QPushButton:hover, QToolButton:hover, QComboBox:hover { background: #2c2926; border-color: #5a554d; }
QPushButton:pressed, QToolButton:pressed { background: #191816; }
QPushButton:disabled, QToolButton:disabled { color: #6f6961; background: #211f1d; border-color: #37342f; }
QPushButton:focus, QToolButton:focus, QComboBox:focus, QLineEdit:focus,
QTextEdit:focus, QPlainTextEdit:focus, QListWidget:focus, QTreeWidget:focus, QTabWidget:focus {
    border: 1px solid #a481b4;
}
QPushButton#primaryButton { background: #5c4566; border-color: #765984; color: #faf6f0; font-weight: 600; padding-left: 17px; padding-right: 17px; }
QPushButton#primaryButton:hover { background: #684e73; border-color: #8a6b98; }
QPushButton#primaryButton:disabled { background: #2b2729; border-color: #403a3f; color: #777078; }
QPushButton#modeButton { background: transparent; border: none; padding: 4px 16px; color: #b8b1a8; }
QPushButton#modeButton:checked { background: #47364f; color: #f5edf8; border: 1px solid #684f73; }
QPushButton#railAction { text-align: left; background: transparent; border-color: transparent; padding: 7px 9px; }
QPushButton#railAction:hover { background: #292724; border-color: #3f3b36; }
QToolButton#bareButton { background: transparent; border-color: transparent; padding: 4px; }
QToolButton#bareButton:hover { background: #2a2825; border-color: #403c37; }
QToolButton#bareButton:checked { background: #3a3040; border-color: #684f73; }
QToolButton#bareButton:disabled { background: transparent; border-color: transparent; }
QComboBox { padding-right: 25px; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: #262421; border: 1px solid #4a453f; selection-background-color: #46384b; selection-color: #f5efe7; outline: 0; }
QListWidget, QTreeWidget {
    background: transparent;
    border: none;
    outline: 0;
    alternate-background-color: #211f1d;
}
QListWidget::item, QTreeWidget::item { border-radius: 3px; padding: 5px; }
QListWidget::item:hover, QTreeWidget::item:hover { background: #292724; }
QListWidget::item:selected, QTreeWidget::item:selected { background: #3a3040; color: #f5edf8; }
QTreeWidget::branch { background: transparent; }
QTreeView::indicator { width: 13px; height: 13px; border: 1px solid #625b53; border-radius: 2px; background: #1a1917; }
QTreeView::indicator:hover { border-color: #a98abb; }
QTreeView::indicator:checked { border-color: #a98abb; background: #7b5b8a; }
QTextEdit, QPlainTextEdit {
    background: #191816;
    border: 1px solid #403c36;
    border-radius: 4px;
    selection-background-color: #624d6d;
    selection-color: #fffaf5;
    padding: 7px;
}
QTextEdit#composer { background: #24211f; border: 1px solid #4a453e; padding: 10px; }
QPlainTextEdit#terminalOutput, QPlainTextEdit#diffView {
    font-family: "Cascadia Mono", "Consolas", "DejaVu Sans Mono", monospace;
    font-size: 9pt;
    background: #171615;
    border: none;
    border-radius: 0;
    padding: 7px 10px;
}
QLineEdit#terminalInput { font-family: "Cascadia Mono", "Consolas", monospace; background: #1b1a18; border-color: #3f3b36; }
QTabWidget::pane { border: none; border-top: 1px solid #38352f; top: -1px; }
QTabBar::tab { background: transparent; color: #b7b0a7; padding: 12px 16px 10px 16px; border-bottom: 2px solid transparent; }
QTabBar::tab:hover { color: #eee8df; }
QTabBar::tab:selected { color: #f5efe8; border-bottom-color: #9570a7; }
QCheckBox { spacing: 7px; color: #c8c0b7; }
QCheckBox::indicator { width: 15px; height: 15px; border: 1px solid #5b554e; background: #1a1917; border-radius: 3px; }
QCheckBox::indicator:checked { background: #86659a; border-color: #a17bb6; }
QProgressBar { background: #191816; border: 1px solid #403c36; border-radius: 3px; text-align: center; min-height: 18px; }
QProgressBar::chunk { background: #80608f; }
QSplitter::handle { background: #37342f; }
QSplitter::handle:hover { background: #71557c; }
QStatusBar { background: #151412; border-top: 1px solid #35322d; color: #9f988f; }
QStatusBar::item { border: none; }
QScrollBar:vertical { background: #191816; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #49443e; min-height: 30px; border-radius: 4px; margin: 2px; }
QScrollBar::handle:vertical:hover { background: #5b554e; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: #191816; height: 10px; }
QScrollBar::handle:horizontal { background: #49443e; min-width: 30px; border-radius: 4px; margin: 2px; }
QToolTip { background: #2a2825; color: #f2ece4; border: 1px solid #514c45; padding: 5px; }
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _as_text(value: Any) -> str:
    return "" if value is None else str(value)


_BRAND_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bDeepSeek\s+Harness\b", re.IGNORECASE), "Onionmind Agent"),
    (re.compile(r"\bDeepSeek\b", re.IGNORECASE), "Onionmind"),
    (re.compile(r"\bHarness\b"), "Onionmind Agent"),
    (re.compile(r"\bDSH\b", re.IGNORECASE), "Onionmind Agent"),
    (re.compile(r"\bOllama\b", re.IGNORECASE), "Onionmind local engine"),
    (re.compile(r"\bllama(?:\.cpp|-server)?\b", re.IGNORECASE), "Onionmind local engine"),
    (re.compile(r"\bNode\.js\b", re.IGNORECASE), "Agent runtime"),
)


def _brand_runtime_text(value: Any) -> str:
    """Keep implementation brands behind Onionmind's product language."""
    text = _as_text(value)
    for pattern, replacement in _BRAND_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    return text


def _friendly_error(core: Any, exc: BaseException) -> str:
    text = _as_text(exc) or exc.__class__.__name__
    helper = getattr(core, "user_error", None)
    if callable(helper):
        try:
            text = _as_text(helper(exc)) or text
        except Exception:
            pass
    return _brand_runtime_text(text)


def _icon(name: str, size: int = 18) -> QIcon:
    """Render Onionmind's compact, platform-neutral monochrome icon language."""
    canvas = QPixmap(size, size)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(size / 18.0, size / 18.0)
    pen = QPen(QColor("#c9c1b7"))
    pen.setWidthF(1.45)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    def line(x1: float, y1: float, x2: float, y2: float) -> None:
        painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

    def box(x: float, y: float, width: float, height: float, radius: float = 1.2) -> None:
        painter.drawRoundedRect(QRectF(x, y, width, height), radius, radius)

    def file_shape() -> None:
        path = QPainterPath(QPointF(4.5, 2.5))
        path.lineTo(10.5, 2.5)
        path.lineTo(14, 6)
        path.lineTo(14, 15.5)
        path.lineTo(4.5, 15.5)
        path.closeSubpath()
        painter.drawPath(path)
        line(10.5, 2.8, 10.5, 6)
        line(10.5, 6, 13.7, 6)

    def folder_shape(opened: bool = False) -> None:
        path = QPainterPath(QPointF(2.5, 5.5))
        path.lineTo(7, 5.5)
        path.lineTo(8.5, 7)
        path.lineTo(15.5, 7)
        if opened:
            path.lineTo(13.8, 14.5)
            path.lineTo(3.8, 14.5)
            path.lineTo(2.5, 8)
        else:
            path.lineTo(15.5, 14.5)
            path.lineTo(2.5, 14.5)
        path.closeSubpath()
        painter.drawPath(path)

    if name in {"file", "new_task"}:
        file_shape()
        if name == "new_task":
            line(6.2, 10, 10.2, 10)
            line(8.2, 8, 8.2, 12)
    elif name in {"folder", "folder_open", "folder_plus"}:
        folder_shape(name == "folder_open")
        if name == "folder_plus":
            line(7, 10.8, 11, 10.8)
            line(9, 8.8, 9, 12.8)
    elif name == "archive":
        box(3, 5.5, 12, 9)
        box(2.5, 3, 13, 3, 0.8)
        line(7, 9, 11, 9)
    elif name == "export":
        box(3, 8, 12, 7)
        line(9, 11, 9, 2.5)
        line(6.5, 5, 9, 2.5)
        line(11.5, 5, 9, 2.5)
    elif name == "model":
        box(3, 3, 12, 12, 2)
        painter.drawEllipse(QRectF(6.2, 6.2, 5.6, 5.6))
        line(9, 1.5, 9, 3)
        line(9, 15, 9, 16.5)
        line(1.5, 9, 3, 9)
        line(15, 9, 16.5, 9)
    elif name == "settings":
        painter.drawEllipse(QRectF(5.7, 5.7, 6.6, 6.6))
        painter.drawEllipse(QRectF(8, 8, 2, 2))
        for x1, y1, x2, y2 in ((9, 2, 9, 5), (9, 13, 9, 16), (2, 9, 5, 9), (13, 9, 16, 9), (4, 4, 6, 6), (12, 12, 14, 14), (14, 4, 12, 6), (6, 12, 4, 14)):
            line(x1, y1, x2, y2)
    elif name == "stop":
        painter.setBrush(QColor("#c9c1b7"))
        painter.drawRoundedRect(QRectF(5, 5, 8, 8), 1.2, 1.2)
    elif name == "clear":
        box(5, 5.5, 8, 10, 1)
        line(3.8, 5.5, 14.2, 5.5)
        line(7, 3.2, 11, 3.2)
        line(7.5, 8, 7.5, 13)
        line(10.5, 8, 10.5, 13)
    elif name == "close":
        line(4.5, 4.5, 13.5, 13.5)
        line(13.5, 4.5, 4.5, 13.5)
    elif name == "refresh":
        painter.drawArc(QRectF(3, 3, 12, 12), 35 * 16, 280 * 16)
        line(12.8, 2.8, 15.2, 3.7)
        line(15.2, 3.7, 14.3, 6)
    elif name in {"rail", "terminal", "inspector"}:
        box(2.5, 3, 13, 12, 1.2)
        if name == "rail":
            line(6.5, 3.5, 6.5, 14.5)
        elif name == "inspector":
            line(11.5, 3.5, 11.5, 14.5)
        else:
            line(5, 7, 7.2, 9)
            line(7.2, 9, 5, 11)
            line(9.2, 11, 12.5, 11)
    elif name == "attach":
        path = QPainterPath(QPointF(6.2, 8.2))
        path.cubicTo(6.2, 4.2, 11.8, 4.2, 11.8, 8.2)
        path.lineTo(11.8, 12)
        path.cubicTo(11.8, 15.3, 6.2, 15.3, 6.2, 12)
        path.lineTo(6.2, 6.5)
        path.cubicTo(6.2, 3.2, 13.8, 3.2, 13.8, 7)
        path.lineTo(13.8, 11.5)
        painter.drawPath(path)
    else:
        box(4, 4, 10, 10)

    painter.end()
    return QIcon(canvas)


def _register_system_fonts(app: QApplication) -> None:
    """Register platform fonts and keep the platform's proportional UI size."""
    system_point_size = app.font().pointSizeF()
    candidates = [
        Path("C:/Windows/Fonts/segoeui.ttf"),
        Path("C:/Windows/Fonts/segoeuib.ttf"),
        Path("C:/Windows/Fonts/consola.ttf"),
        Path("C:/Windows/Fonts/consolab.ttf"),
        Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
    ]
    ui_family = ""
    for path in candidates:
        if not path.is_file():
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id < 0:
            continue
        for family in QFontDatabase.applicationFontFamilies(font_id):
            if not ui_family and not QFontDatabase.isFixedPitch(family):
                ui_family = family
    if ui_family:
        ui_font = app.font()
        ui_font.setFamily(ui_family)
        if system_point_size > 0:
            ui_font.setPointSizeF(system_point_size)
        app.setFont(ui_font)


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    text = Signal(str)
    event = Signal(object)
    progress = Signal(float, str)
    finished = Signal()


class SafeWorker:
    def __init__(self, fn: Callable[[WorkerSignals], Any], core: Any = None) -> None:
        self.fn = fn
        self.core = core
        self.signals = WorkerSignals()

    @staticmethod
    def _emit(signal: Any, *values: Any) -> None:
        try:
            signal.emit(*values)
        except RuntimeError:
            # The application may have closed while a daemon worker was
            # finishing a network or filesystem operation.
            pass

    def run(self) -> None:
        try:
            self._emit(self.signals.result, self.fn(self.signals))
        except BaseException as exc:  # core functions use SystemExit for user-facing failures
            if isinstance(exc, KeyboardInterrupt):
                self._emit(self.signals.error, "The operation was interrupted.")
            else:
                self._emit(self.signals.error, _friendly_error(self.core, exc))
        finally:
            self._emit(self.signals.finished)


# Tor Browser owns the SOCKS port on Windows/macOS; Linux usually has a tor daemon.
# Same candidate list the installer walks, so the button starts what the installer set up.
_TOR_BROWSER_PATHS = (
    "{home}/Desktop/Tor Browser/Browser/firefox.exe",
    "{home}/OneDrive/Desktop/Tor Browser/Browser/firefox.exe",
    "{local}/Tor Browser/Browser/firefox.exe",
    "{local}/Programs/Tor Browser/Browser/firefox.exe",
    "C:/Program Files/Tor Browser/Browser/firefox.exe",
    "C:/Program Files (x86)/Tor Browser/Browser/firefox.exe",
    "/Applications/Tor Browser.app/Contents/MacOS/firefox",
)


def _tor_launch_command() -> Optional[list[str]]:
    """The command that brings a SOCKS proxy up, or None if nothing is installed."""
    daemon = shutil.which("tor")
    if daemon and sys.platform not in ("win32", "darwin"):
        return [daemon]
    home = Path.home().as_posix()
    local = Path(os.environ.get("LOCALAPPDATA", home)).as_posix()
    for template in _TOR_BROWSER_PATHS:
        candidate = Path(template.format(home=home, local=local))
        if candidate.exists():
            return [str(candidate)]
    return [daemon] if daemon else None


class StatusDot(QWidget):
    def __init__(self, color: str = "#a39b91", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._color = QColor(color)
        self.setFixedSize(10, 10)
        self.setAccessibleName("Status indicator")

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def paintEvent(self, event: Any) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._color)
        painter.drawEllipse(2, 2, 6, 6)


class StatusPill(QFrame):
    clicked = Signal()

    COLORS = {
        "good": "#78b889",
        "warn": "#c9a36b",
        "bad": "#d47d6b",
        "idle": "#8e8880",
        "busy": "#a98abb",
    }

    def __init__(self, prefix: str, text: str, state: str = "idle", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.prefix = prefix
        self.setObjectName("statusPill")
        self.setStyleSheet("QFrame#statusPill { background:#211f1d; border:1px solid #403c36; border-radius:4px; }")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(5)
        prefix_label = QLabel(prefix)
        prefix_label.setObjectName("muted")
        self.dot = StatusDot(self.COLORS.get(state, self.COLORS["idle"]))
        self.label = QLabel(text)
        layout.addWidget(prefix_label)
        layout.addWidget(self.dot)
        layout.addWidget(self.label)
        self.setAccessibleName(f"{prefix} status: {text}")

    def set_status(self, text: str, state: str = "idle") -> None:
        self.label.setText(text)
        self.dot.set_color(self.COLORS.get(state, self.COLORS["idle"]))
        self.setAccessibleName(f"{self.prefix} status: {text}")

    def make_clickable(self) -> None:
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class ComposerEdit(QTextEdit):
    sendRequested = Signal()
    filesDropped = Signal(list)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("composer")
        self.setAcceptDrops(True)
        self.setAccessibleName("Task composer")
        self.setTabChangesFocus(True)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.sendRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def dragEnterEvent(self, event: Any) -> None:
        if event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event: Any) -> None:
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.filesDropped.emit(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)


class MessageBlock(QWidget):
    def __init__(self, role: str, text: str = "", name: Optional[str] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.role = role
        self._text = text
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 5, 0, 12)
        outer.setSpacing(12)
        self.avatar = QLabel("DE" if role == "user" else "O")
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar.setFixedSize(32, 32)
        self.avatar.setObjectName("avatarUser" if role == "user" else "avatarAssistant")
        self.avatar.setAccessibleName("Developer" if role == "user" else "Onionmind")
        if role != "user" and (MODULE_DIR / "onionmind.ico").exists():
            self.avatar.setText("")
            self.avatar.setPixmap(
                QPixmap(str(MODULE_DIR / "onionmind.ico")).scaled(
                    18,
                    18,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        outer.addWidget(self.avatar, 0, Qt.AlignmentFlag.AlignTop)

        column = QVBoxLayout()
        column.setSpacing(6)
        header = QHBoxLayout()
        header.setSpacing(7)
        who = QLabel(name or ("Developer" if role == "user" else "Onionmind"))
        who.setObjectName("title")
        timestamp = QLabel(datetime.now().strftime("%I:%M %p").lstrip("0"))
        timestamp.setObjectName("meta")
        header.addWidget(who)
        header.addWidget(timestamp)
        header.addStretch(1)
        column.addLayout(header)
        self.body = QLabel(text)
        self.body.setTextFormat(Qt.TextFormat.PlainText)
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.body.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.body.setMinimumHeight(18)
        self.body.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._target_body_width = max(520, self.body.fontMetrics().horizontalAdvance("0" * 74))
        self.set_reading_width(self._target_body_width)
        self.body.setAccessibleName(f"{who.text()} message")
        column.addWidget(self.body)
        outer.addLayout(column, 1)

    @property
    def text(self) -> str:
        return self._text

    def set_text(self, text: str) -> None:
        self._text = text
        self.body.setText(text)
        self._sync_body_height()

    def append_text(self, text: str) -> None:
        self._text += text
        self.body.setText(self._text)
        self._sync_body_height()

    def set_reading_width(self, available_width: int) -> None:
        body_width = max(240, min(self._target_body_width, available_width))
        self.body.setFixedWidth(body_width)
        self.setFixedWidth(body_width + 56)
        self._sync_body_height()

    def _sync_body_height(self) -> None:
        required_height = self.body.heightForWidth(max(1, self.body.width()))
        if required_height < 0:
            required_height = self.body.sizeHint().height()
        self.body.setFixedHeight(max(18, required_height))
        self.body.updateGeometry()
        own_layout = self.layout()
        if own_layout is not None:
            own_layout.invalidate()
        self.updateGeometry()
        parent = self.parentWidget()
        if parent is not None and parent.layout() is not None:
            parent.layout().invalidate()


class ToolActivityCard(QFrame):
    def __init__(self, title: str, rows: Iterable[tuple[str, str]], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        actual_rows = list(rows)
        self.setObjectName("toolCard")
        self._target_width = max(560, self.fontMetrics().horizontalAdvance("0" * 78))
        self.set_reading_width(self._target_width)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(11, 8, 11, 8)
        outer.setSpacing(5)
        header = QHBoxLayout()
        label = QLabel(title)
        label.setObjectName("title")
        count = QLabel(f"{len(actual_rows)} items")
        count.setObjectName("meta")
        header.addWidget(label)
        header.addStretch(1)
        header.addWidget(count)
        outer.addLayout(header)
        for path, state in actual_rows:
            line = QHBoxLayout()
            file_label = QLabel(path)
            file_label.setTextFormat(Qt.TextFormat.PlainText)
            file_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            state_label = QLabel(state)
            state_label.setObjectName("accent")
            line.addWidget(file_label, 1)
            line.addWidget(state_label)
            outer.addLayout(line)

    def set_reading_width(self, available_width: int) -> None:
        self.setFixedWidth(max(280, min(self._target_width, available_width)))


class TranscriptView(QScrollArea):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setAccessibleName("Conversation transcript")
        self.viewport().setObjectName("transcriptViewport")
        self.content = QWidget()
        self.content.setObjectName("transcriptViewport")
        self.layout = QVBoxLayout(self.content)
        self.layout.setContentsMargins(48, 20, 48, 24)
        self.layout.setSpacing(2)
        self.layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._message_blocks: list[MessageBlock] = []
        self._tool_cards: list[ToolActivityCard] = []
        self.setWidget(self.content)

    def clear(self) -> None:
        self._message_blocks.clear()
        self._tool_cards.clear()
        while self.layout.count():
            item = self.layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def add_message(self, role: str, text: str = "", name: Optional[str] = None) -> MessageBlock:
        block = MessageBlock(role, text, name)
        self._message_blocks.append(block)
        self.layout.addWidget(block, 0, Qt.AlignmentFlag.AlignLeft)
        self._apply_reading_widths()
        self._scroll_later()
        return block

    def add_tool_card(self, title: str, rows: list[tuple[str, str]]) -> ToolActivityCard:
        holder = QWidget()
        holder_layout = QHBoxLayout(holder)
        holder_layout.setContentsMargins(44, 0, 0, 12)
        card = ToolActivityCard(title, rows)
        self._tool_cards.append(card)
        holder_layout.addWidget(card, 0, Qt.AlignmentFlag.AlignLeft)
        holder_layout.addStretch(1)
        self.layout.addWidget(holder)
        self._apply_reading_widths()
        self._scroll_later()
        return card

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._apply_reading_widths()

    def _apply_reading_widths(self) -> None:
        margins = self.layout.contentsMargins()
        content_width = max(320, self.viewport().width() - margins.left() - margins.right())
        for block in self._message_blocks:
            block.set_reading_width(content_width - 56)
        for card in self._tool_cards:
            card.set_reading_width(content_width - 44)

    def _scroll_later(self) -> None:
        QTimer.singleShot(0, lambda: self.verticalScrollBar().setValue(self.verticalScrollBar().maximum()))


class SessionBridge:
    """Use onionmind_desktop_core when present; keep a small QSettings fallback."""

    def __init__(self, desktop_core: Any, root: Path) -> None:
        self.desktop_core = desktop_core
        self.root = root
        self.store = None
        if desktop_core is not None and hasattr(desktop_core, "SessionStore"):
            try:
                self.store = desktop_core.SessionStore(root)
            except Exception:
                self.store = None
        self.fallback = QSettings(APP_NAME, APP_ID)

    def create(self, title: str, model: str, workspace: Optional[str], messages: Iterable[dict[str, Any]]) -> Any:
        if self.store is not None:
            return self.store.create(title=title, model=model, workspace=workspace, messages=tuple(messages))
        now = _now_iso()
        return {
            "id": uuid.uuid4().hex,
            "title": title,
            "model": model,
            "workspace": workspace,
            "messages": list(messages),
            "created_at": now,
            "updated_at": now,
        }

    def list(self) -> list[Any]:
        if self.store is not None:
            try:
                return list(self.store.list())
            except Exception:
                return []
        try:
            return list(json.loads(self.fallback.value("sessions", "[]")))
        except (TypeError, ValueError):
            return []

    def save(self, session: Any, *, title: str, model: str, workspace: Optional[str], messages: list[dict[str, Any]]) -> Any:
        if self.store is not None:
            if dataclasses.is_dataclass(session):
                session = dataclasses.replace(
                    session,
                    title=title,
                    model=model,
                    workspace=workspace,
                    messages=list(messages),
                )
            else:
                for key, value in {
                    "title": title,
                    "model": model,
                    "workspace": workspace,
                    "messages": tuple(messages),
                }.items():
                    setattr(session, key, value)
            return self.store.save(session)
        payload = dict(session)
        payload.update(
            title=title,
            model=model,
            workspace=workspace,
            messages=list(messages),
            updated_at=_now_iso(),
        )
        sessions = [s for s in self.list() if _field(s, "id") != payload["id"]]
        sessions.insert(0, payload)
        self.fallback.setValue("sessions", json.dumps(sessions[:80]))
        return payload

    def archive(self, session_id: str) -> Any:
        if self.store is not None:
            try:
                return self.store.archive(session_id)
            except Exception:
                return None
        sessions = self.list()
        archived = next((item for item in sessions if _as_text(_field(item, "id")) == session_id), None)
        remaining = [item for item in sessions if _as_text(_field(item, "id")) != session_id]
        self.fallback.setValue("sessions", json.dumps(remaining[:80]))
        if archived is not None:
            try:
                archive_items = list(json.loads(self.fallback.value("archived_sessions", "[]")))
            except (TypeError, ValueError):
                archive_items = []
            archive_items.insert(0, archived)
            self.fallback.setValue("archived_sessions", json.dumps(archive_items[:80]))
        return archived


class SettingsBridge:
    def __init__(self, desktop_core: Any, root: Path) -> None:
        self.store = None
        self.fallback = QSettings(APP_NAME, APP_ID)
        if desktop_core is not None and hasattr(desktop_core, "SettingsStore"):
            try:
                self.store = desktop_core.SettingsStore(root / "settings.json", defaults={})
            except Exception:
                self.store = None

    def load(self) -> dict[str, Any]:
        if self.store is not None:
            try:
                return dict(self.store.load())
            except Exception:
                return {}
        try:
            return dict(json.loads(self.fallback.value("settings_json", "{}")))
        except (TypeError, ValueError):
            return {}

    def save(self, settings: dict[str, Any]) -> None:
        if self.store is not None:
            try:
                self.store.save(settings)
                return
            except Exception:
                pass
        self.fallback.setValue("settings_json", json.dumps(settings))


def _field(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


class WorkspaceBridge:
    def __init__(self, desktop_core: Any) -> None:
        self.inspector = None
        if desktop_core is not None and hasattr(desktop_core, "WorkspaceInspector"):
            try:
                self.inspector = desktop_core.WorkspaceInspector(max_entries=260, max_depth=5)
            except Exception:
                self.inspector = None

    def inspect(self, selected: str) -> dict[str, Any]:
        if self.inspector is not None:
            snap = self.inspector.inspect(selected)
            try:
                diff = self.inspector.diff(selected)
            except Exception as exc:
                diff = f"Diff unavailable: {exc}"
            changes = [
                {
                    "status": _field(change, "status", "?"),
                    "path": _field(change, "path", ""),
                    "original_path": _field(change, "original_path"),
                }
                for change in (_field(snap, "changes", ()) or ())
            ]
            return {
                "root": _as_text(_field(snap, "root", selected)),
                "is_git": bool(_field(snap, "is_git", False)),
                "branch": _as_text(_field(snap, "branch", "No repository")),
                "dirty": bool(_field(snap, "dirty", False)),
                "changes": changes,
                "agents_files": [_as_text(p) for p in (_field(snap, "agents_files", ()) or ())],
                "file_tree": [_as_text(p) for p in (_field(snap, "file_tree", ()) or ())],
                "tree_truncated": bool(_field(snap, "tree_truncated", False)),
                "summary": _as_text(_field(snap, "change_summary", "")),
                "diff": diff,
            }
        return self._fallback_inspect(selected)

    def _fallback_inspect(self, selected: str) -> dict[str, Any]:
        root = Path(selected).resolve()
        files: list[str] = []
        agents: list[str] = []
        for current, dirs, names in os.walk(root):
            current_path = Path(current)
            depth = len(current_path.relative_to(root).parts)
            safe_directories: list[str] = []
            for directory_name in dirs:
                if directory_name in SKIP_DIRS or directory_name.startswith("."):
                    continue
                candidate = current_path / directory_name
                is_junction = bool(
                    getattr(candidate, "is_junction", lambda: False)()
                )
                if candidate.is_symlink() or is_junction:
                    continue
                try:
                    candidate.resolve(strict=True).relative_to(root)
                except (OSError, ValueError):
                    continue
                safe_directories.append(directory_name)
            dirs[:] = safe_directories
            if depth >= 5:
                dirs[:] = []
            for name in sorted(names):
                relative = (current_path / name).relative_to(root).as_posix()
                files.append(relative)
                if name.upper() == "AGENTS.MD":
                    agents.append(relative)
                if len(files) >= 260:
                    break
            if len(files) >= 260:
                break
        git = shutil.which("git")
        branch, changes, diff = "No repository", [], "This folder is not a Git repository."
        is_git = False
        if git:
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            git_base = [
                git,
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.untrackedCache=false",
            ]
            try:
                probe = subprocess.run(
                    [*git_base, "rev-parse", "--is-inside-work-tree"], cwd=root, capture_output=True,
                    text=True, timeout=6, creationflags=flags,
                )
                is_git = probe.returncode == 0 and probe.stdout.strip() == "true"
                if is_git:
                    branch_run = subprocess.run(
                        [*git_base, "branch", "--show-current"], cwd=root, capture_output=True,
                        text=True, timeout=6, creationflags=flags,
                    )
                    branch = branch_run.stdout.strip() or "detached HEAD"
                    status_run = subprocess.run(
                        [*git_base, "status", "--short", "--untracked-files=normal", "--ignore-submodules=all"], cwd=root, capture_output=True,
                        text=True, timeout=8, creationflags=flags,
                    )
                    for line in status_run.stdout.splitlines():
                        if not line.strip():
                            continue
                        changes.append({"status": line[:2].strip() or "?", "path": line[3:], "original_path": None})
                    diff_run = subprocess.run(
                        [*git_base, "diff", "--no-ext-diff", "--no-textconv", "--ignore-submodules=all", "--unified=3", "HEAD", "--"], cwd=root, capture_output=True,
                        text=True, timeout=10, creationflags=flags,
                    )
                    diff = diff_run.stdout
                    if diff_run.returncode != 0:
                        cached_run = subprocess.run(
                            [*git_base, "diff", "--no-ext-diff", "--no-textconv", "--ignore-submodules=all", "--cached", "--"], cwd=root, capture_output=True,
                            text=True, timeout=10, creationflags=flags,
                        )
                        working_run = subprocess.run(
                            [*git_base, "diff", "--no-ext-diff", "--no-textconv", "--ignore-submodules=all", "--"], cwd=root, capture_output=True,
                            text=True, timeout=10, creationflags=flags,
                        )
                        diff = cached_run.stdout + working_run.stdout
                    untracked = [
                        change["path"] for change in changes if change["status"] == "??"
                    ]
                    if untracked:
                        diff += "\nUntracked files (content preview unavailable in fallback mode):\n"
                        diff += "".join(f"  {path}\n" for path in untracked[:50])
                    diff = diff[:200000] or "No staged or unstaged diff."
            except (OSError, subprocess.SubprocessError):
                pass
        return {
            "root": str(root), "is_git": is_git, "branch": branch, "dirty": bool(changes),
            "changes": changes, "agents_files": agents, "file_tree": files,
            "tree_truncated": len(files) >= 260, "summary": f"{len(changes)} observed change(s)", "diff": diff,
        }


class HarnessBridge:
    FALLBACK_LIMITATION = (
        "Onionmind Agent is an early-access local coding workflow. Interactive approval "
        "prompts are not available in this build, so protected actions stop safely. "
        "The agent reaches the web only through Tor and does not start without it."
    )

    def __init__(self, desktop_core: Any, core: Any = None) -> None:
        self.desktop_core = desktop_core
        self.core = core
        self.spec = None
        if desktop_core is not None and hasattr(desktop_core, "HarnessSpec"):
            try:
                self.spec = desktop_core.HarnessSpec()
            except Exception:
                self.spec = None

    def _launcher(self, model: str, task: str) -> Optional[list[str]]:
        """``onionmind.py --agent``: the one place Tor is verified and enforced.

        Launching the harness directly would inherit this window's environment,
        which has no proxy and no socket containment - the agent would have
        direct web access whether Tor is up or not.
        """
        script = _as_text(getattr(self.core, "__file__", ""))
        if not script or not callable(getattr(self.core, "run_agent", None)):
            return None
        return [sys.executable, os.path.abspath(script), "--agent", "--model", model, task]

    @property
    def limitation(self) -> str:
        if self.spec is not None:
            value = _as_text(getattr(self.spec, "limitation", "")) or _as_text(
                getattr(self.desktop_core, "HARNESS_LIMITATION", "")
            ) or self.FALLBACK_LIMITATION
            return _brand_runtime_text(value)
        return self.FALLBACK_LIMITATION

    def check(self) -> tuple[bool, str]:
        # The launcher IS the Tor boundary, so without one there is nothing to
        # start: launching the harness ourselves would hand it this window's
        # unproxied environment.
        if self._launcher("model", "task") is None:
            return False, (
                "Onionmind Agent needs the Onionmind runtime to route it through Tor, "
                "and that runtime is not loaded. Re-run Onionmind setup, then restart."
            )
        # A hint, not the gate: re-verifying Tor here would repeat the round trip
        # the launcher makes anyway, on every task. The launcher is what refuses.
        if not getattr(self.core, "_port", None):
            return False, (
                "Tor is not up. Onionmind Agent reaches the web only through Tor and "
                "does not start without it. Start Tor from the toolbar, then try again."
            )
        if self.spec is not None:
            availability = self.spec.check()
            return bool(_field(availability, "available", False)), _brand_runtime_text(
                _field(availability, "reason", "")
            )
        executable = shutil.which("ollama")
        return bool(executable), (
            ""
            if executable
            else "Onionmind Agent is not ready. Re-run Onionmind setup, then restart the app."
        )

    def build(self, *, model: str, task: str, cwd: str) -> tuple[list[str], str]:
        launcher = self._launcher(model, task)
        if launcher is None:                     # check() refuses first; belt and braces
            raise RuntimeError(
                "Onionmind Agent has no Tor-verified launcher, so it will not start."
            )
        return launcher, cwd


class LeftRail(QWidget):
    newTaskRequested = Signal()
    openFolderRequested = Signal()
    sessionSelected = Signal(str)
    projectSelected = Signal(str)
    modelsRequested = Signal()
    settingsRequested = Signal()
    exportRequested = Signal()
    archiveRequested = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("leftRail")
        self.setMinimumWidth(190)
        self.setMaximumWidth(290)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 15, 10, 10)
        layout.setSpacing(9)

        quick = QHBoxLayout()
        new_button = QPushButton("New task")
        new_button.setObjectName("newTaskButton")
        new_button.setIcon(_icon("new_task"))
        new_button.setAccessibleName("Create new task")
        new_button.clicked.connect(self.newTaskRequested)
        self.new_button = new_button
        folder_button = QToolButton()
        folder_button.setObjectName("openFolderButton")
        folder_button.setIcon(_icon("folder_open"))
        folder_button.setToolTip("Open folder (Ctrl+O)")
        folder_button.setAccessibleName("Open project folder")
        folder_button.clicked.connect(self.openFolderRequested)
        self.folder_button = folder_button
        quick.addWidget(new_button, 1)
        quick.addWidget(folder_button)
        layout.addLayout(quick)

        project_header = QHBoxLayout()
        project_label = QLabel("PROJECTS")
        project_label.setObjectName("sectionTitle")
        add_project = QToolButton()
        add_project.setObjectName("bareButton")
        add_project.setIcon(_icon("folder_plus"))
        add_project.setToolTip("Open another project")
        add_project.setAccessibleName("Open another project")
        add_project.clicked.connect(self.openFolderRequested)
        self.add_project_button = add_project
        project_header.addWidget(project_label)
        project_header.addStretch(1)
        project_header.addWidget(add_project)
        layout.addLayout(project_header)

        self.projects = QListWidget()
        self.projects.setMaximumHeight(240)
        self.projects.setAccessibleName("Projects")
        self.projects.itemClicked.connect(
            lambda item: self.projectSelected.emit(_as_text(item.data(Qt.ItemDataRole.UserRole)))
        )
        layout.addWidget(self.projects)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet("color:#3a3732;")
        layout.addWidget(divider)
        session_header = QHBoxLayout()
        session_label = QLabel("SESSIONS")
        session_label.setObjectName("sectionTitle")
        archive = QToolButton()
        archive.setObjectName("bareButton")
        archive.setIcon(_icon("archive"))
        archive.setToolTip("Archive selected session")
        archive.setAccessibleName("Archive selected session")
        archive.clicked.connect(self._archive_selected)
        archive.setEnabled(False)
        self.archive_button = archive
        session_header.addWidget(session_label)
        session_header.addStretch(1)
        session_header.addWidget(archive)
        layout.addLayout(session_header)
        self.sessions = QListWidget()
        self.sessions.setAccessibleName("Saved sessions")
        self.sessions.itemClicked.connect(
            lambda item: self.sessionSelected.emit(_as_text(item.data(Qt.ItemDataRole.UserRole)))
        )
        self.sessions.currentItemChanged.connect(
            lambda current, previous: self.archive_button.setEnabled(current is not None)
        )
        layout.addWidget(self.sessions, 1)

        export = QPushButton("Export conversation")
        export.setObjectName("railAction")
        export.setIcon(_icon("export"))
        export.setToolTip("Export this conversation as Markdown (Ctrl+Shift+S)")
        export.setAccessibleName("Export conversation as Markdown")
        export.clicked.connect(self.exportRequested)
        export.setEnabled(False)
        self.export_button = export
        models = QPushButton("Models")
        models.setObjectName("railAction")
        models.setIcon(_icon("model"))
        models.setAccessibleName("Manage local models")
        models.clicked.connect(self.modelsRequested)
        self.models_button = models
        settings = QPushButton("Settings")
        settings.setObjectName("railAction")
        settings.setIcon(_icon("settings"))
        settings.setAccessibleName("Open Onionmind settings")
        settings.clicked.connect(self.settingsRequested)
        self.settings_button = settings
        layout.addWidget(export)
        layout.addWidget(models)
        layout.addWidget(settings)

    def _archive_selected(self) -> None:
        item = self.sessions.currentItem()
        if item is not None:
            self.archiveRequested.emit(_as_text(item.data(Qt.ItemDataRole.UserRole)))

    def set_conversation_available(self, available: bool) -> None:
        self.export_button.setEnabled(bool(available))

    def add_project(self, path: str, select: bool = True) -> None:
        if not path:
            return
        normalized = os.path.normcase(os.path.abspath(path))
        for index in range(self.projects.count()):
            item = self.projects.item(index)
            if os.path.normcase(_as_text(item.data(Qt.ItemDataRole.UserRole))) == normalized:
                if select:
                    self.projects.setCurrentItem(item)
                return
        name = Path(path).name or path
        item = QListWidgetItem(f"{name}\n{path}")
        item.setIcon(_icon("folder"))
        item.setData(Qt.ItemDataRole.UserRole, path)
        item.setSizeHint(QSize(100, 47))
        self.projects.insertItem(0, item)
        if select:
            self.projects.setCurrentItem(item)

    def set_sessions(self, sessions: Iterable[Any], current_id: Optional[str] = None) -> None:
        self.sessions.clear()
        for session in sessions:
            session_id = _as_text(_field(session, "id"))
            title = _as_text(_field(session, "title", "New session"))
            updated = _field(session, "updated_at")
            if isinstance(updated, datetime):
                meta = updated.astimezone().strftime("%b %d, %H:%M")
            else:
                meta = _as_text(updated)[:16].replace("T", " ") or "Saved locally"
            item = QListWidgetItem(f"{title}\n{meta}")
            item.setData(Qt.ItemDataRole.UserRole, session_id)
            item.setSizeHint(QSize(100, 47))
            self.sessions.addItem(item)
            if session_id == current_id:
                self.sessions.setCurrentItem(item)


class TerminalPane(QFrame):
    closeRequested = Signal()
    stateChanged = Signal(str)

    def __init__(self, desktop_core: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.desktop_core = desktop_core
        self.setObjectName("terminalPane")
        self.setMinimumHeight(135)
        self.setMaximumHeight(200)
        self.workspace = str(Path.cwd())
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 6, 10, 8)
        outer.setSpacing(5)
        header = QHBoxLayout()
        title = QLabel("TERMINAL")
        title.setObjectName("sectionTitle")
        self.scope_label = QLabel(Path(self.workspace).name)
        self.scope_label.setObjectName("meta")
        stop = QToolButton()
        stop.setObjectName("bareButton")
        stop.setIcon(_icon("stop"))
        stop.setToolTip(
            "Stop the shell command; child processes started by it may require manual termination"
        )
        stop.setAccessibleName("Stop terminal command")
        stop.clicked.connect(self.stop)
        stop.setEnabled(False)
        self.stop_button = stop
        clear = QToolButton()
        clear.setObjectName("bareButton")
        clear.setIcon(_icon("clear"))
        clear.setToolTip("Clear terminal")
        clear.setAccessibleName("Clear terminal output")
        clear.clicked.connect(lambda: self.output.clear())
        clear.setEnabled(False)
        self.clear_button = clear
        close = QToolButton()
        close.setObjectName("bareButton")
        close.setIcon(_icon("close"))
        close.setToolTip("Close terminal drawer (Ctrl+`)")
        close.setAccessibleName("Close terminal drawer")
        close.clicked.connect(self.closeRequested)
        self.close_button = close
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.scope_label)
        header.addSpacing(8)
        header.addWidget(stop)
        header.addWidget(clear)
        header.addWidget(close)
        outer.addLayout(header)

        self.output = QPlainTextEdit()
        self.output.setObjectName("terminalOutput")
        self.output.setReadOnly(True)
        self.output.setAccessibleName("Terminal output")
        self.output.document().setMaximumBlockCount(5000)
        self.output.textChanged.connect(
            lambda: self.clear_button.setEnabled(bool(self.output.toPlainText()))
        )
        outer.addWidget(self.output, 1)
        command_row = QHBoxLayout()
        prompt = QLabel(">")
        prompt.setObjectName("accent")
        self.command = QLineEdit()
        self.command.setObjectName("terminalInput")
        shell_name = "PowerShell" if os.name == "nt" else "/bin/sh"
        self.command.setPlaceholderText(f"Run a {shell_name} command in this project")
        self.command.setToolTip(
            f"Commands run through {shell_name} in the active project directory."
        )
        self.command.setAccessibleName("Terminal command")
        self.command.returnPressed.connect(self.run_current)
        self.process.stateChanged.connect(self._sync_process_state)
        command_row.addWidget(prompt)
        command_row.addWidget(self.command, 1)
        outer.addLayout(command_row)

    def set_workspace(self, path: str) -> None:
        self.workspace = path
        self.scope_label.setText(Path(path).name or path)
        self.scope_label.setToolTip(path)

    def append(self, text: str) -> None:
        if not text:
            return
        cursor = self.output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self.output.setTextCursor(cursor)
        self.output.ensureCursorVisible()

    def run_current(self) -> None:
        command = self.command.text().strip()
        if not command:
            return
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.stateChanged.emit("A terminal command is already running.")
            return
        self.command.clear()
        self.append(f"\n{Path(self.workspace).name}> {command}\n")
        argv: tuple[str, ...]
        parser = getattr(self.desktop_core, "parse_terminal_command", None) if self.desktop_core else None
        try:
            if callable(parser):
                interpreter = "powershell" if os.name == "nt" else "sh"
                argv = tuple(parser(command, interpreter=interpreter))
            elif os.name == "nt":
                argv = ("powershell", "-NoLogo", "-NoProfile", "-Command", command)
            else:
                argv = ("/bin/sh", "-lc", command)
        except Exception as exc:
            self.append(f"Could not parse command: {exc}\n")
            return
        if not argv:
            self.append("No command to run.\n")
            return
        self.process.setWorkingDirectory(self.workspace)
        self.process.start(argv[0], list(argv[1:]))
        self.stateChanged.emit(f"Running {command}")

    def _sync_process_state(self, state: QProcess.ProcessState) -> None:
        running = state != QProcess.ProcessState.NotRunning
        self.stop_button.setEnabled(running)
        self.command.setEnabled(not running)

    def stop(self) -> None:
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.stop_button.setEnabled(False)
            self.stateChanged.emit("Stopping terminal command…")
            self.process.terminate()
            QTimer.singleShot(1200, self._kill_if_running)

    def _kill_if_running(self) -> None:
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()

    def _read_output(self) -> None:
        text = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self.append(ANSI_ESCAPE.sub("", text))

    def _finished(self, exit_code: int, status: QProcess.ExitStatus) -> None:
        label = "finished" if status == QProcess.ExitStatus.NormalExit else "crashed"
        self.append(f"\n[command {label}, exit {exit_code}]\n")
        self.stateChanged.emit(f"Terminal command {label} with exit code {exit_code}.")

    def _error(self, error: QProcess.ProcessError) -> None:
        del error
        self.append(f"\nCould not start command: {self.process.errorString()}\n")
        self.stateChanged.emit("The terminal command could not start.")


class InspectorPane(QWidget):
    refreshRequested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("inspector")
        self.setMinimumWidth(250)
        self.setMaximumWidth(390)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 5, 10, 10)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setAccessibleName("Workspace inspector")
        outer.addWidget(self.tabs)

        self.context_tab = QWidget()
        context_layout = QVBoxLayout(self.context_tab)
        context_layout.setContentsMargins(2, 12, 2, 3)
        context_layout.setSpacing(10)
        self.agent_card = QFrame()
        self.agent_card.setObjectName("card")
        agent_layout = QVBoxLayout(self.agent_card)
        agent_layout.setContentsMargins(10, 9, 10, 9)
        self.agent_title = QLabel("Repository guidance")
        self.agent_title.setObjectName("title")
        self.agent_state = QLabel("No AGENTS.md observed")
        self.agent_state.setObjectName("meta")
        self.agent_state.setWordWrap(True)
        agent_layout.addWidget(self.agent_title)
        agent_layout.addWidget(self.agent_state)
        context_layout.addWidget(self.agent_card)

        tree_header = QHBoxLayout()
        files_title = QLabel("PROJECT FILES")
        files_title.setObjectName("sectionTitle")
        self.context_count = QLabel("0 selected")
        self.context_count.setObjectName("meta")
        tree_header.addWidget(files_title)
        tree_header.addStretch(1)
        tree_header.addWidget(self.context_count)
        context_layout.addLayout(tree_header)
        self.file_tree = QTreeWidget()
        self.file_tree.setHeaderHidden(True)
        self.file_tree.setAccessibleName("Project files for context")
        self.file_tree.itemChanged.connect(self._context_selection_changed)
        context_layout.addWidget(self.file_tree, 1)

        privacy = QFrame()
        privacy.setObjectName("card")
        privacy_layout = QVBoxLayout(privacy)
        privacy_layout.setContentsMargins(10, 9, 10, 9)
        privacy_title = QHBoxLayout()
        p_title = QLabel("Privacy boundary")
        p_title.setObjectName("title")
        self.privacy_state = QLabel("Local inference")
        self.privacy_state.setObjectName("success")
        privacy_title.addWidget(p_title)
        privacy_title.addStretch(1)
        privacy_title.addWidget(self.privacy_state)
        privacy_layout.addLayout(privacy_title)
        privacy_copy = QLabel(
            "Prompts and model inference stay on this machine. Anything that leaves it goes over Tor: Chat search, and the agent, which verifies a circuit before it starts and refuses to run without one."
        )
        privacy_copy.setObjectName("meta")
        privacy_copy.setWordWrap(True)
        privacy_layout.addWidget(privacy_copy)
        context_layout.addWidget(privacy)

        self.changes_tab = QWidget()
        changes_layout = QVBoxLayout(self.changes_tab)
        changes_layout.setContentsMargins(2, 10, 2, 3)
        changes_header = QHBoxLayout()
        self.change_summary = QLabel("No observed changes")
        self.change_summary.setObjectName("title")
        refresh = QToolButton()
        refresh.setObjectName("bareButton")
        refresh.setIcon(_icon("refresh"))
        refresh.setToolTip("Refresh observed Git changes")
        refresh.setAccessibleName("Refresh Git changes")
        refresh.clicked.connect(self.refreshRequested)
        refresh.setEnabled(False)
        self.refresh_button = refresh
        changes_header.addWidget(self.change_summary)
        changes_header.addStretch(1)
        changes_header.addWidget(refresh)
        changes_layout.addLayout(changes_header)
        self.change_list = QListWidget()
        self.change_list.setMaximumHeight(175)
        self.change_list.setAccessibleName("Observed Git changes")
        changes_layout.addWidget(self.change_list)
        diff_title = QLabel("DIFF")
        diff_title.setObjectName("sectionTitle")
        changes_layout.addWidget(diff_title)
        self.diff_view = QPlainTextEdit()
        self.diff_view.setObjectName("diffView")
        self.diff_view.setReadOnly(True)
        self.diff_view.setAccessibleName("Git diff")
        self.diff_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        changes_layout.addWidget(self.diff_view, 1)

        self.activity_tab = QWidget()
        activity_layout = QVBoxLayout(self.activity_tab)
        activity_layout.setContentsMargins(2, 10, 2, 3)
        activity_copy = QLabel("Observed application events. Agent output is not treated as proof of a file change.")
        activity_copy.setObjectName("meta")
        activity_copy.setWordWrap(True)
        activity_layout.addWidget(activity_copy)
        self.activity = QListWidget()
        self.activity.setAccessibleName("Activity history")
        activity_layout.addWidget(self.activity, 1)

        self.tabs.addTab(self.context_tab, "Context")
        self.tabs.addTab(self.changes_tab, "Changes")
        self.tabs.addTab(self.activity_tab, "Activity")

    def append_activity(self, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M")
        self.activity.insertItem(0, QListWidgetItem(f"{stamp}  {_brand_runtime_text(text)}"))
        while self.activity.count() > 150:
            self.activity.takeItem(self.activity.count() - 1)

    def update_snapshot(self, snapshot: dict[str, Any]) -> None:
        self.refresh_button.setEnabled(bool(snapshot.get("root")))
        agents = snapshot.get("agents_files") or []
        if agents:
            self.agent_title.setText(Path(agents[0]).name)
            self.agent_state.setText(f"Loaded from {agents[0]}" + (f" and {len(agents) - 1} more" if len(agents) > 1 else ""))
        else:
            self.agent_title.setText("Repository guidance")
            self.agent_state.setText("No AGENTS.md observed in the inspected tree")
        self.populate_tree(snapshot.get("file_tree") or [], bool(snapshot.get("tree_truncated")))
        changes = snapshot.get("changes") or []
        self.change_list.clear()
        for change in changes:
            status = _as_text(change.get("status", "?"))
            path = _as_text(change.get("path", ""))
            original = change.get("original_path")
            text = f"{status:>2}  {path}" + (f"  from {original}" if original else "")
            self.change_list.addItem(QListWidgetItem(text))
        if changes:
            self.change_summary.setText(snapshot.get("summary") or f"{len(changes)} observed change(s)")
        else:
            self.change_summary.setText("Working tree clean" if snapshot.get("is_git") else "Not a Git repository")
        self.diff_view.setPlainText(snapshot.get("diff") or "No diff to display.")

    def populate_tree(self, paths: list[str], truncated: bool = False) -> None:
        self.file_tree.blockSignals(True)
        self.file_tree.clear()
        nodes: dict[tuple[str, ...], QTreeWidgetItem] = {}
        for raw in paths:
            slash_path = raw.replace("\\", "/")
            directory_entry = slash_path.endswith("/")
            normalized = slash_path.strip("/")
            if not normalized or normalized == ".":
                continue
            parts = tuple(part for part in normalized.split("/") if part)
            parent: Optional[QTreeWidgetItem] = None
            for index, part in enumerate(parts):
                key = parts[: index + 1]
                node = nodes.get(key)
                if node is None:
                    node = QTreeWidgetItem(parent if parent is not None else self.file_tree, [part])
                    nodes[key] = node
                    if index == len(parts) - 1 and not directory_entry:
                        node.setData(0, Qt.ItemDataRole.UserRole, normalized)
                        node.setFlags(node.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                        node.setCheckState(0, Qt.CheckState.Unchecked)
                    else:
                        node.setIcon(0, _icon("folder"))
                parent = node
        self.file_tree.blockSignals(False)
        self.file_tree.expandToDepth(0)
        self.context_count.setText("0 selected" + (" · tree limited" if truncated else ""))

    def selected_context_files(self) -> list[str]:
        selected: list[str] = []
        iterator = self.file_tree.invisibleRootItem()

        def walk(parent: QTreeWidgetItem) -> None:
            for index in range(parent.childCount()):
                child = parent.child(index)
                path = child.data(0, Qt.ItemDataRole.UserRole)
                if path and child.checkState(0) == Qt.CheckState.Checked:
                    selected.append(_as_text(path))
                walk(child)

        walk(iterator)
        return selected

    def _context_selection_changed(self, item: QTreeWidgetItem, column: int) -> None:
        del item, column
        count = len(self.selected_context_files())
        self.context_count.setText(f"{count} selected")


class ModelManagerDialog(QDialog):
    pullRequested = Signal(str)

    def __init__(
        self,
        models: list[str],
        current: str,
        label_for_model: Optional[Callable[[str], str]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._label_for_model = label_for_model or (lambda value: "ONIONMIND MODEL")
        self.setWindowTitle("Onionmind models")
        self.setModal(True)
        self.resize(520, 390)
        layout = QVBoxLayout(self)
        heading = QLabel("Onionmind models")
        heading.setObjectName("brand")
        copy_label = QLabel("Choose from models installed on this machine, or add another Onionmind model locally.")
        copy_label.setObjectName("meta")
        copy_label.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(copy_label)
        installed_label = QLabel("INSTALLED")
        installed_label.setObjectName("sectionTitle")
        layout.addWidget(installed_label)
        self.models = QListWidget()
        self.models.setAccessibleName("Installed local models")
        layout.addWidget(self.models, 1)
        self.set_models(models, current)
        row = QHBoxLayout()
        self.model_name = QLineEdit()
        self.model_name.setPlaceholderText("Onionmind model name, for example BLAZE")
        self.model_name.setAccessibleName("Onionmind model to add")
        self.pull_button = QPushButton("Add model")
        self.pull_button.setObjectName("primaryButton")
        self.pull_button.setAccessibleName("Add Onionmind model")
        self.pull_button.clicked.connect(self._request_pull)
        self.pull_button.setEnabled(False)
        self.model_name.textChanged.connect(self._sync_pull_button)
        self.model_name.returnPressed.connect(self._request_pull)
        row.addWidget(self.model_name, 1)
        row.addWidget(self.pull_button)
        layout.addLayout(row)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("Ready")
        layout.addWidget(self.progress)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
        if close_button is not None:
            close_button.setAccessibleName("Close Onionmind model manager")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def set_models(self, models: list[str], current: str = "") -> None:
        self.models.clear()
        counts: dict[str, int] = {}
        for model in models:
            base = self._label_for_model(model)
            counts[base] = counts.get(base, 0) + 1
            label = base if counts[base] == 1 else f"{base} {counts[base]}"
            item = QListWidgetItem(label + ("  · selected" if model == current else ""))
            item.setData(Qt.ItemDataRole.UserRole, model)
            self.models.addItem(item)

    def _sync_pull_button(self) -> None:
        self.pull_button.setEnabled(bool(self.model_name.text().strip()))

    def _request_pull(self) -> None:
        name = self.model_name.text().strip()
        if not name:
            self.progress.setFormat("Enter an Onionmind model name")
            return
        self.pull_button.setEnabled(False)
        self.model_name.setEnabled(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("Starting…")
        self.pullRequested.emit(name)

    def set_progress(self, fraction: float, status: str) -> None:
        self.progress.setRange(0, 100)
        self.progress.setValue(max(0, min(100, round(fraction * 100))))
        self.progress.setFormat(f"{_brand_runtime_text(status)} · %p%")

    def set_error(self, message: str) -> None:
        self.model_name.setEnabled(True)
        self._sync_pull_button()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat(_brand_runtime_text(message)[:80])

    def set_complete(self) -> None:
        self.model_name.setEnabled(True)
        self.model_name.clear()
        self.progress.setValue(100)
        self.progress.setFormat("Installed locally")


class SettingsDialog(QDialog):
    def __init__(self, data_root: Path, agent_limitation: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.data_root = data_root
        self.setWindowTitle("Onionmind settings")
        self.resize(540, 350)
        outer = QVBoxLayout(self)
        heading = QLabel("Boundaries and storage")
        heading.setObjectName("brand")
        outer.addWidget(heading)
        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.addRow("Inference", QLabel("Onionmind inference on this machine"))
        tor = QLabel("Chat search and the coding agent both leave over Tor; a failed Tor check never falls back to a direct request.")
        tor.setWordWrap(True)
        form.addRow("Tor", tor)
        agent = QLabel(_brand_runtime_text(agent_limitation))
        agent.setWordWrap(True)
        form.addRow("Agent", agent)
        form.addRow("Telemetry", QLabel("No Onionmind telemetry or account"))
        storage = QLabel(str(data_root))
        storage.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        storage.setWordWrap(True)
        form.addRow("Session storage", storage)
        outer.addLayout(form)
        outer.addStretch(1)
        self.storage_feedback = QLabel()
        self.storage_feedback.setObjectName("meta")
        self.storage_feedback.setWordWrap(True)
        outer.addWidget(self.storage_feedback)
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        open_folder = actions.addButton("Open storage folder", QDialogButtonBox.ButtonRole.ActionRole)
        open_folder.setAccessibleName("Open Onionmind storage folder")
        open_folder.clicked.connect(self._open_storage_folder)
        actions.rejected.connect(self.reject)
        outer.addWidget(actions)

    def _open_storage_folder(self) -> None:
        try:
            self.data_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.storage_feedback.setText(f"Could not open storage: {exc}")
            return
        opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.data_root)))
        self.storage_feedback.setText(
            "Opened Onionmind storage." if opened else "The system could not open the storage folder."
        )


class OnionmindWindow(QMainWindow):
    def __init__(self, core: Any, desktop_core: Any = None, demo: bool = False) -> None:
        super().__init__()
        self.core = core
        self.desktop_core = desktop_core
        self.demo = demo
        self._workers: set[SafeWorker] = set()
        self._tor_process: Optional[subprocess.Popen[bytes]] = None
        self._tor_ready = False
        self._tor_busy = False
        data_location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
        self.data_root = Path(data_location or (Path.home() / ".onionmind")) / "desktop"
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.settings_bridge = SettingsBridge(desktop_core, self.data_root)
        self.session_bridge = SessionBridge(desktop_core, self.data_root / "sessions")
        self.workspace_bridge = WorkspaceBridge(desktop_core)
        self.harness_bridge = HarnessBridge(desktop_core, core)
        self.settings_data = {} if demo else self.settings_bridge.load()
        self.workspace: Optional[str] = None
        self.current_snapshot: dict[str, Any] = {}
        self.current_session: Any = None
        self.session_objects: dict[str, Any] = {}
        self.chat_messages: list[dict[str, Any]] = []
        self.attachments: list[str] = []
        self.installed_model_ids: list[str] = []
        self.active_kind: Optional[str] = None
        self.stop_event: Optional[threading.Event] = None
        self.stream_block: Optional[MessageBlock] = None
        self.harness_process: Optional[QProcess] = None
        self.harness_output = ""
        self.harness_generation = 0
        self._rail_requested = True
        self._inspector_requested = True
        self._model_dialog: Optional[ModelManagerDialog] = None
        self._build_window()
        self._install_shortcuts()
        if demo:
            self._populate_demo()
        else:
            self._restore_state()
            self._probe_services()

    def _build_window(self) -> None:
        self.setWindowTitle("Onionmind — private local workbench")
        icon_path = MODULE_DIR / "onionmind.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.resize(1420, 900)
        self.setMinimumSize(760, 620)
        root = QWidget()
        root.setObjectName("windowRoot")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        self.setCentralWidget(root)

        toolbar = QWidget()
        toolbar.setObjectName("toolbar")
        toolbar.setFixedHeight(57)
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(10, 7, 12, 7)
        toolbar_layout.setSpacing(9)

        brand_box = QWidget()
        brand_box.setFixedWidth(205)
        self.brand_box = brand_box
        brand_layout = QHBoxLayout(brand_box)
        brand_layout.setContentsMargins(7, 0, 4, 0)
        brand_layout.setSpacing(9)
        logo = QLabel()
        if icon_path.exists():
            pixmap = QPixmap(str(icon_path)).scaled(26, 26, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            logo.setPixmap(pixmap)
        else:
            logo.setText("O")
            logo.setObjectName("avatarAssistant")
            logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setFixedSize(28, 28)
        logo.setAccessibleName("Onionmind logo")
        brand = QLabel("Onionmind")
        brand.setObjectName("brand")
        self.brand_label = brand
        brand_layout.addWidget(logo)
        brand_layout.addWidget(brand)
        brand_layout.addStretch(1)

        rail_toggle = QToolButton()
        rail_toggle.setObjectName("bareButton")
        rail_toggle.setCheckable(True)
        rail_toggle.setChecked(True)
        rail_toggle.setIcon(_icon("rail"))
        rail_toggle.setToolTip("Toggle projects and sessions")
        rail_toggle.setAccessibleName("Toggle projects and sessions rail")
        rail_toggle.clicked.connect(self.toggle_rail)
        self.rail_toggle = rail_toggle
        brand_layout.addWidget(rail_toggle)
        toolbar_layout.addWidget(brand_box)

        self.repo_label = QLabel("No project")
        self.repo_label.setObjectName("title")
        self.repo_label.setMinimumWidth(95)
        self.repo_label.setMaximumWidth(180)
        self.repo_label.setAccessibleName("Current project")
        toolbar_layout.addWidget(self.repo_label)
        self.toolbar_separator = QLabel("/")
        self.toolbar_separator.setObjectName("meta")
        toolbar_layout.addWidget(self.toolbar_separator)
        self.branch_label = QLabel("Open a folder")
        self.branch_label.setObjectName("muted")
        self.branch_label.setMaximumWidth(180)
        self.branch_label.setAccessibleName("Current Git branch")
        toolbar_layout.addWidget(self.branch_label)
        toolbar_layout.addStretch(1)

        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(190)
        self.model_combo.setMaximumWidth(260)
        self.model_combo.setAccessibleName("Onionmind model")
        self.model_combo.setToolTip("Choose the Onionmind model for the next run")
        self.model_combo.currentIndexChanged.connect(self._model_changed)
        toolbar_layout.addWidget(self.model_combo)
        toolbar_layout.addWidget(self._build_context_slider())
        self.model_status = StatusPill("Model", "Checking", "busy")
        toolbar_layout.addWidget(self.model_status)
        self.tor_status = StatusPill("Tor", "Checking", "busy")
        self.tor_status.make_clickable()
        self.tor_status.clicked.connect(self.toggle_tor)
        self.tor_status.setToolTip("Click to start or stop Tor. Tor search state is separate from Onionmind inference")
        toolbar_layout.addWidget(self.tor_status)

        terminal_toggle = QToolButton()
        terminal_toggle.setObjectName("bareButton")
        terminal_toggle.setCheckable(True)
        terminal_toggle.setChecked(True)
        terminal_toggle.setIcon(_icon("terminal"))
        terminal_toggle.setToolTip("Toggle terminal drawer (Ctrl+`)")
        terminal_toggle.setAccessibleName("Toggle terminal drawer")
        terminal_toggle.clicked.connect(self.toggle_terminal)
        self.terminal_toggle = terminal_toggle
        toolbar_layout.addWidget(terminal_toggle)
        inspector_toggle = QToolButton()
        inspector_toggle.setObjectName("bareButton")
        inspector_toggle.setCheckable(True)
        inspector_toggle.setChecked(True)
        inspector_toggle.setIcon(_icon("inspector"))
        inspector_toggle.setToolTip("Toggle inspector (Ctrl+Shift+I)")
        inspector_toggle.setAccessibleName("Toggle context, changes, and activity inspector")
        inspector_toggle.clicked.connect(self.toggle_inspector)
        self.inspector_toggle = inspector_toggle
        toolbar_layout.addWidget(inspector_toggle)
        root_layout.addWidget(toolbar)

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setHandleWidth(1)
        self.left_rail = LeftRail()
        self.left_rail.newTaskRequested.connect(self.new_task)
        self.left_rail.openFolderRequested.connect(self.open_folder)
        self.left_rail.projectSelected.connect(self.select_workspace)
        self.left_rail.sessionSelected.connect(self.load_session)
        self.left_rail.modelsRequested.connect(self.open_model_manager)
        self.left_rail.settingsRequested.connect(self.open_settings)
        self.left_rail.exportRequested.connect(self.export_conversation)
        self.left_rail.archiveRequested.connect(self.archive_session)
        self.main_splitter.addWidget(self.left_rail)

        center = QWidget()
        center.setObjectName("centerPane")
        center.setMinimumWidth(450)
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)
        self.transcript = TranscriptView()
        center_layout.addWidget(self.transcript, 1)
        self.terminal = TerminalPane(self.desktop_core)
        self.terminal.closeRequested.connect(lambda: self.toggle_terminal(False))
        self.terminal.stateChanged.connect(self.set_status)
        center_layout.addWidget(self.terminal)
        self.composer_frame = self._build_composer()
        center_layout.addWidget(self.composer_frame)
        self.main_splitter.addWidget(center)

        self.inspector = InspectorPane()
        self.inspector.refreshRequested.connect(self.refresh_workspace)
        self.main_splitter.addWidget(self.inspector)
        self.main_splitter.setSizes([224, 860, 292])
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setStretchFactor(2, 0)
        root_layout.addWidget(self.main_splitter, 1)

        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("meta")
        self.statusBar().setSizeGripEnabled(False)
        self.statusBar().setFixedHeight(24)
        self.statusBar().addWidget(self.status_label, 1)
        self.scope_status = QLabel("No project selected")
        self.scope_status.setObjectName("meta")
        self.statusBar().addPermanentWidget(self.scope_status)

    def _build_context_slider(self) -> QWidget:
        """The agent's context budget, as a slider.

        This is the number that decides whether a complex job is possible, and
        it is also the number that decides whether the model still fits in VRAM
        - so it is the one knob worth reaching for often enough to deserve a
        place in the toolbar rather than a settings page.

        Stops are powers of two because the KV cache is sized from this; the
        values in between buy nothing and make the control fiddly. The core
        clamps to the same range, so a hand-edited file cannot push it out.
        """
        steps = list(getattr(self.core, "CODE_STEPS", (8192, 16384, 32768, 65536, 131072)))
        self.context_steps = steps

        box = QWidget()
        box.setFixedWidth(150)
        layout = QHBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)

        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setMinimum(0)
        slider.setMaximum(len(steps) - 1)
        slider.setPageStep(1)
        slider.setAccessibleName("Agent context budget")
        self.context_slider = slider

        label = QLabel()
        label.setObjectName("meta")
        label.setFixedWidth(38)
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.context_label = label

        reader = getattr(self.core, "code_ctx", None)
        current = reader() if callable(reader) else steps[len(steps) // 2]
        # Nearest stop, not exact match: an env override or an older saved value
        # can sit between two of them, and the slider still has to land somewhere.
        index = min(range(len(steps)), key=lambda i: abs(steps[i] - current))
        slider.setValue(index)
        self._sync_context_label(index)
        # valueChanged fires for every pixel of a drag; sliderReleased would miss
        # the arrow keys, which is how the control is reachable without a mouse.
        slider.valueChanged.connect(self._context_changed)

        layout.addWidget(slider)
        layout.addWidget(label)
        return box

    def _sync_context_label(self, index: int) -> None:
        value = self.context_steps[index]
        self.context_label.setText(f"{value // 1024}k")
        locked = _as_text(os.environ.get("ONIONMIND_CODE_CTX", ""))
        if locked:
            # The env var wins in the core, so a slider that silently disagreed
            # with the running agent would be a lie. Say so instead.
            self.context_slider.setEnabled(False)
            self.context_slider.setToolTip(
                f"Context budget is pinned to {locked} by ONIONMIND_CODE_CTX"
            )
            return
        self.context_slider.setToolTip(
            f"Agent context budget: {value:,} tokens. Bigger fits more of the job "
            "in one session; too big and the model spills out of VRAM onto the CPU. "
            "Takes effect on the next agent run."
        )

    def _context_changed(self, index: int) -> None:
        self._sync_context_label(index)
        value = self.context_steps[index]
        writer = getattr(self.core, "set_code_ctx", None)
        if callable(writer) and not self.demo:
            try:
                writer(value)
            except OSError as exc:
                self.set_status(f"Could not save the context budget: {exc}")
                return
        self.set_status(f"Agent context budget {value:,} tokens · applies to the next run")

    def _build_composer(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("composerFrame")
        frame.setMinimumHeight(148)
        frame.setMaximumHeight(178)
        outer = QVBoxLayout(frame)
        outer.setContentsMargins(12, 9, 12, 9)
        outer.setSpacing(6)
        self.composer = ComposerEdit()
        self.composer.setPlaceholderText("Describe what you want Onionmind to do in this project…")
        self.composer.sendRequested.connect(self.submit)
        self.composer.filesDropped.connect(self.add_attachments)
        self.composer.textChanged.connect(self._sync_action_states)
        outer.addWidget(self.composer, 1)
        self.attachment_row = QWidget()
        attachment_layout = QHBoxLayout(self.attachment_row)
        attachment_layout.setContentsMargins(0, 0, 0, 0)
        self.attachment_label = QLabel()
        self.attachment_label.setObjectName("attachmentLabel")
        clear = QToolButton()
        clear.setObjectName("bareButton")
        clear.setIcon(_icon("close"))
        clear.setToolTip("Remove all attachments")
        clear.setAccessibleName("Remove all attachments")
        clear.clicked.connect(self.clear_attachments)
        attachment_layout.addWidget(self.attachment_label)
        attachment_layout.addWidget(clear)
        attachment_layout.addStretch(1)
        self.attachment_row.hide()
        outer.addWidget(self.attachment_row)

        controls = QHBoxLayout()
        controls.setSpacing(7)
        attach = QToolButton()
        attach.setIcon(_icon("attach"))
        attach.setToolTip("Attach files or images")
        attach.setAccessibleName("Attach files or images")
        attach.clicked.connect(self.choose_attachments)
        controls.addWidget(attach)
        mode_frame = QFrame()
        mode_frame.setObjectName("modeSwitch")
        mode_layout = QHBoxLayout(mode_frame)
        mode_layout.setContentsMargins(2, 2, 2, 2)
        mode_layout.setSpacing(0)
        self.chat_button = QPushButton("Chat")
        self.agent_button = QPushButton("Agent")
        self.chat_button.setAccessibleName("Use Onionmind Chat")
        self.chat_button.setToolTip("Chat privately with the selected Onionmind model")
        self.agent_button.setAccessibleName("Use Onionmind Agent")
        self.agent_button.setToolTip("Ask Onionmind Agent to work in the selected project")
        for button in (self.chat_button, self.agent_button):
            button.setObjectName("modeButton")
            button.setCheckable(True)
            mode_layout.addWidget(button)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_group.addButton(self.chat_button)
        self.mode_group.addButton(self.agent_button)
        self.chat_button.clicked.connect(lambda: self.set_mode("chat"))
        self.agent_button.clicked.connect(lambda: self.set_mode("agent"))
        controls.addWidget(mode_frame)
        self.approval_state = QLabel("Protected actions stop safely")
        self.approval_state.setObjectName("accent")
        self.approval_state.setToolTip(
            "This early-access Agent cannot answer interactive approval prompts, so protected actions stop instead of continuing"
        )
        self.approval_state.setAccessibleName(
            "Onionmind Agent protected actions stop safely"
        )
        controls.addWidget(self.approval_state)
        self.disclosure = QLabel()
        self.disclosure.setObjectName("disclosure")
        self.disclosure.setWordWrap(True)
        controls.addWidget(self.disclosure, 1)
        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("primaryButton")
        self.send_button.setAccessibleName("Send task")
        self.send_button.clicked.connect(self.submit)
        controls.addWidget(self.send_button)
        outer.addLayout(controls)
        self.set_mode(_as_text(self.settings_data.get("mode", "agent")))
        self._sync_action_states()
        return frame

    def _install_shortcuts(self) -> None:
        shortcuts = [
            ("Ctrl+N", self.new_task),
            ("Ctrl+O", self.open_folder),
            ("Ctrl+L", self.focus_composer),
            ("Ctrl+Shift+S", self.export_conversation),
            ("Ctrl+`", lambda: self.toggle_terminal(not self.terminal.isVisible())),
            ("Ctrl+Shift+I", lambda: self.toggle_inspector(not self.inspector.isVisible())),
            ("Escape", self.stop_active),
        ]
        self.shortcuts: list[QShortcut] = []
        for sequence, callback in shortcuts:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
            shortcut.activated.connect(callback)
            self.shortcuts.append(shortcut)

    def _restore_state(self) -> None:
        model = _as_text(self.settings_data.get("model") or getattr(self.core, "MODEL", "inferno"))
        self.set_model_options([model], model)
        recent = self.settings_data.get("recent_projects") or []
        for path in reversed(recent[:8]):
            if path:
                self.left_rail.add_project(_as_text(path), select=False)
        sessions = self.session_bridge.list()
        self.session_objects = {_as_text(_field(session, "id")): session for session in sessions}
        self.left_rail.set_sessions(sessions)
        workspace = _as_text(self.settings_data.get("workspace"))
        if workspace and Path(workspace).is_dir():
            self.select_workspace(workspace)
        else:
            self.new_task(save_current=False)

    def _probe_services(self) -> None:
        def model_probe(signals: WorkerSignals) -> tuple[str, list[str]]:
            del signals
            detector = getattr(self.core, "detect_backend", None)
            if callable(detector):
                detector()
            backend = _as_text(getattr(self.core, "BACKEND", "local service"))
            installed = getattr(self.core, "installed_models", None)
            models = list(installed()) if callable(installed) else []
            return backend, models

        worker = self._start_worker(model_probe)
        worker.signals.result.connect(self._model_probe_complete)
        worker.signals.error.connect(lambda message: self._model_probe_failed(message))

        checker = getattr(self.core, "tor_check", None)
        if not callable(checker):
            self._set_tor_state("Not checked", "idle")
            return

        def tor_probe(signals: WorkerSignals) -> Any:
            del signals
            checker()
            return getattr(self.core, "_port", None)

        tor_worker = self._start_worker(tor_probe)
        tor_worker.signals.result.connect(self._tor_probe_complete)
        tor_worker.signals.result.connect(lambda _: self.inspector.append_activity("Tor readiness verified separately"))
        tor_worker.signals.error.connect(lambda message: self._tor_probe_failed(message))

    def _start_worker(
        self,
        fn: Callable[[WorkerSignals], Any],
        setup: Optional[Callable[[SafeWorker], None]] = None,
    ) -> SafeWorker:
        worker = SafeWorker(fn, self.core)
        self._workers.add(worker)
        if setup is not None:
            # A fast job can finish before the caller connects, so wire it up first.
            setup(worker)

        def forget() -> None:
            self._workers.discard(worker)

        worker.signals.finished.connect(forget)
        threading.Thread(
            target=worker.run,
            name="onionmind-desktop-worker",
            daemon=True,
        ).start()
        return worker

    def _model_probe_complete(self, payload: tuple[str, list[str]]) -> None:
        _backend, models = payload
        current = self.current_model_id()
        self.set_model_options(models or [current], current)
        self.model_status.set_status("Ready", "good")
        self.model_status.setToolTip("Onionmind inference is ready on this machine")
        self.inspector.append_activity("Onionmind inference ready")

    def _model_probe_failed(self, message: str) -> None:
        self.model_status.set_status("Unavailable", "bad")
        self.set_status(message)
        self.inspector.append_activity(f"Onionmind inference unavailable: {message}")

    def _tor_probe_complete(self, port: Any) -> None:
        self._set_tor_state(f"Ready · {port}" if port else "Ready", "good")
        self.tor_status.setToolTip("Tor is up. Click to stop it. Search runs through Tor or not at all.")

    def _tor_probe_failed(self, message: str) -> None:
        self._set_tor_state("Not ready", "bad")
        self.tor_status.setToolTip(
            message + " Search fails closed and does not fall back to a direct request. Click to start Tor."
        )
        self.inspector.append_activity("Tor not ready; Chat search will fail closed")

    def _set_tor_state(self, text: str, state: str) -> None:
        self._tor_ready = state == "good"
        self._tor_busy = state == "busy"
        self.tor_status.set_status(text, state)

    def toggle_tor(self) -> None:
        if self.demo or self._tor_busy:
            return
        if self._tor_ready:
            self.stop_tor()
        else:
            self.start_tor()

    def start_tor(self) -> None:
        checker = getattr(self.core, "tor_check", None)
        if not callable(checker):
            return
        if self._tor_process is None or self._tor_process.poll() is not None:
            command = _tor_launch_command()
            if command is None:
                self._set_tor_state("Not installed", "bad")
                self.tor_status.setToolTip("No tor daemon or Tor Browser found. Install one, then click again.")
                self.set_status("No Tor daemon or Tor Browser found on this machine.")
                return
            try:
                self._tor_process = subprocess.Popen(command)
            except OSError as exc:
                self._set_tor_state("Not ready", "bad")
                self.set_status(f"Could not start Tor: {exc}")
                return
        self._set_tor_state("Starting", "busy")
        self.inspector.append_activity("Starting Tor")

        def wait_for_tor(signals: WorkerSignals) -> Any:
            del signals
            deadline = time.monotonic() + 150
            while time.monotonic() < deadline:
                try:
                    checker()
                    return getattr(self.core, "_port", None)
                except KeyboardInterrupt:
                    raise
                except BaseException:  # tor_check exits rather than raising when the proxy is down
                    time.sleep(3)
            raise RuntimeError("Tor did not come up in time.")

        def wire(worker: SafeWorker) -> None:
            worker.signals.result.connect(self._tor_probe_complete)
            worker.signals.result.connect(lambda _: self.inspector.append_activity("Tor started"))
            worker.signals.error.connect(self._tor_probe_failed)

        self._start_worker(wait_for_tor, wire)

    def stop_tor(self) -> None:
        process = self._tor_process
        if process is None or process.poll() is not None:
            self._tor_process = None
            self.set_status("Tor is running outside Onionmind; stop it where you started it.")
            return
        process.terminate()
        self._tor_process = None
        # Clear the pinned port so a search after this fails closed instead of
        # dialling a proxy that is on its way down.
        try:
            self.core._port = None
        except AttributeError:
            pass
        self._set_tor_state("Stopped", "idle")
        self.tor_status.setToolTip("Tor is stopped. Click to start it. Search fails closed until it is up.")
        self.inspector.append_activity("Tor stopped")

    def _describe_model(self, raw_id: str) -> str:
        helper = getattr(self.desktop_core, "describe_model", None) if self.desktop_core else None
        if callable(helper):
            try:
                display = helper(raw_id)
                tier = _as_text(_field(display, "tier", "")).upper()
                if tier:
                    return tier
            except Exception:
                pass
        lower = raw_id.lower()
        tiers = ("spark", "ember", "blaze", "inferno", "cinder", "wildfire", "flashpoint", "phoenix", "nova", "pyre")
        tier = next((name.upper() for name in tiers if name in lower), "")
        if tier:
            return tier
        tag = raw_id.rsplit(":", 1)[-1] if ":" in raw_id else ""
        size = tag.upper() if re.fullmatch(r"\d+(?:\.\d+)?B", tag, re.IGNORECASE) else ""
        return "ONIONMIND CUSTOM" + (f" · {size}" if size else "")

    def set_model_options(self, models: Iterable[str], current: str = "") -> None:
        values: list[str] = []
        for model in [current, *models]:
            model = _as_text(model).strip()
            if model and model not in values:
                values.append(model)
        self.installed_model_ids = values
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        counts: dict[str, int] = {}
        for model in values:
            base = self._describe_model(model)
            counts[base] = counts.get(base, 0) + 1
            label = base if counts[base] == 1 else f"{base} {counts[base]}"
            self.model_combo.addItem(label, model)
        index = self.model_combo.findData(current)
        self.model_combo.setCurrentIndex(max(0, index))
        self.model_combo.blockSignals(False)

    def current_model_id(self) -> str:
        return _as_text(self.model_combo.currentData()) or _as_text(getattr(self.core, "MODEL", "inferno")) or "inferno"

    def _model_changed(self, index: int) -> None:
        del index
        model = self.current_model_id()
        try:
            setattr(self.core, "MODEL", model)
        except Exception:
            pass
        self.settings_data["model"] = model
        if not self.demo:
            self.settings_bridge.save(self.settings_data)
        self.set_status(f"Model set to {self._describe_model(model)}")

    def set_mode(self, mode: str) -> None:
        mode = "chat" if mode.lower() == "chat" else "agent"
        self.chat_button.setChecked(mode == "chat")
        self.agent_button.setChecked(mode == "agent")
        self.mode = mode
        if mode == "chat":
            self.approval_state.hide()
            self.disclosure.setText("Private on-device chat · search is the only Tor-routed exception")
            self.composer.setPlaceholderText("Ask Onionmind anything…")
        else:
            self.approval_state.show()
            self.disclosure.setText("Early access · Agent web access is Tor-only and refuses to run without it")
            self.composer.setPlaceholderText("Describe what you want Onionmind Agent to change…")
        self.settings_data["mode"] = mode
        if not self.demo:
            self.settings_bridge.save(self.settings_data)

    def focus_composer(self) -> None:
        self.composer.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def set_status(self, text: str) -> None:
        text = _brand_runtime_text(text)
        self.status_label.setText(text)
        self.status_label.setAccessibleName(f"Application status: {text}")

    def toggle_rail(self, visible: Optional[bool] = None) -> None:
        target = (not self.left_rail.isVisible()) if visible is None else bool(visible)
        self._rail_requested = target
        self.left_rail.setVisible(target)
        self.rail_toggle.setChecked(target)

    def toggle_inspector(self, visible: Optional[bool] = None) -> None:
        target = (not self.inspector.isVisible()) if visible is None else bool(visible)
        self._inspector_requested = target
        self.inspector.setVisible(target)
        self.inspector_toggle.setChecked(target)

    def toggle_terminal(self, visible: Optional[bool] = None) -> None:
        target = (not self.terminal.isVisible()) if visible is None else bool(visible)
        self.terminal.setVisible(target)
        self.terminal_toggle.setChecked(target)
        if target:
            self.terminal.command.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def _sync_action_states(self) -> None:
        has_draft = bool(self.composer.toPlainText().strip() or self.attachments)
        self.send_button.setEnabled(bool(self.active_kind) or has_draft)
        self.left_rail.set_conversation_available(bool(self.chat_messages))

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        width = self.width()
        compact_toolbar = width < 1100
        self.brand_box.setFixedWidth(165 if width < 900 else 205)
        self.branch_label.setVisible(not compact_toolbar)
        self.toolbar_separator.setVisible(not compact_toolbar)
        self.model_status.setVisible(width >= 1280)
        self.tor_status.setVisible(True)
        self.model_combo.setMinimumWidth(145 if compact_toolbar else 190)
        if width < 820:
            self.left_rail.hide()
        elif self._rail_requested:
            self.left_rail.show()
        if width < 1080:
            self.inspector.hide()
        elif self._inspector_requested:
            self.inspector.show()
        self.rail_toggle.setChecked(not self.left_rail.isHidden())
        self.inspector_toggle.setChecked(not self.inspector.isHidden())

    def choose_attachments(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Attach local files or images",
            self.workspace or str(Path.home()),
            "Supported files (*.png *.jpg *.jpeg *.webp *.gif *.py *.js *.ts *.tsx *.md *.txt *.json *.toml *.yml *.yaml);;All files (*.*)",
        )
        self.add_attachments(paths)

    def add_attachments(self, paths: list[str]) -> None:
        for path in paths:
            absolute = os.path.abspath(path)
            if os.path.isfile(absolute) and absolute not in self.attachments:
                self.attachments.append(absolute)
        self._update_attachments()

    def clear_attachments(self) -> None:
        self.attachments.clear()
        self._update_attachments()

    def _update_attachments(self) -> None:
        if not self.attachments:
            self.attachment_row.hide()
            self.attachment_label.clear()
            self._sync_action_states()
            return
        names = [Path(path).name for path in self.attachments]
        display = ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")
        self.attachment_label.setText(f"Attached locally: {display}")
        self.attachment_label.setToolTip("\n".join(self.attachments))
        self.attachment_row.show()
        self._sync_action_states()

    def open_folder(self) -> None:
        if self.active_kind:
            self.set_status("Stop the active run before changing projects.")
            return
        path = QFileDialog.getExistingDirectory(self, "Open project folder", self.workspace or str(Path.home()))
        if path:
            self.select_workspace(path)

    def select_workspace(self, path: str) -> None:
        if self.active_kind:
            self.set_status("Stop the active run before changing projects.")
            return
        if not path or not Path(path).is_dir():
            self.set_status(f"Project folder is unavailable: {path}")
            return
        self.workspace = str(Path(path).resolve())
        self.left_rail.add_project(self.workspace)
        self.repo_label.setText(Path(self.workspace).name or self.workspace)
        self.repo_label.setToolTip(self.workspace)
        self.branch_label.setText("Inspecting…")
        self.scope_status.setText(self.workspace)
        self.terminal.set_workspace(self.workspace)
        recent = [_as_text(p) for p in self.settings_data.get("recent_projects", [])]
        recent = [p for p in recent if os.path.normcase(p) != os.path.normcase(self.workspace)]
        recent.insert(0, self.workspace)
        self.settings_data.update(workspace=self.workspace, recent_projects=recent[:10])
        if not self.demo:
            self.settings_bridge.save(self.settings_data)
        self.refresh_workspace()

    def refresh_workspace(self) -> None:
        if not self.workspace:
            self.set_status("Open a project folder to inspect context and Git changes.")
            return
        selected = self.workspace
        self.set_status("Inspecting project files and observed Git state…")

        def inspect_job(signals: WorkerSignals) -> dict[str, Any]:
            del signals
            return self.workspace_bridge.inspect(selected)

        worker = self._start_worker(inspect_job)
        worker.signals.result.connect(self._workspace_ready)
        worker.signals.error.connect(lambda message: self._workspace_failed(message))

    def _workspace_ready(self, snapshot: dict[str, Any]) -> None:
        if self.workspace and os.path.normcase(_as_text(snapshot.get("root"))) != os.path.normcase(self.workspace):
            return
        self.current_snapshot = snapshot
        branch = snapshot.get("branch") or ("No repository" if not snapshot.get("is_git") else "detached HEAD")
        self.branch_label.setText(_as_text(branch))
        self.inspector.update_snapshot(snapshot)
        count = len(snapshot.get("changes") or [])
        self.set_status(f"Project inspected · {count} observed change(s)")
        self.inspector.append_activity(f"Project refreshed; {count} observed change(s)")

    def _workspace_failed(self, message: str) -> None:
        self.branch_label.setText("Inspection failed")
        self.set_status(f"Could not inspect project: {message}")
        self.inspector.append_activity(f"Project inspection failed: {message}")

    def new_task(self, save_current: bool = True) -> None:
        if self.active_kind:
            self.stop_active()
            self.set_status("Stopping the active run; create the new task when it has finished.")
            return
        if save_current and not self.save_current_session():
            self.set_status(
                "The current session was not cleared because its history could not be saved."
            )
            return
        self.current_session = None
        self.chat_messages = []
        self.transcript.clear()
        self.transcript.add_message(
            "assistant",
            "Open a project, choose an Onionmind model, then describe the task. Chat answers privately; Agent works in the selected repository and reports only changes observed on disk.",
        )
        self.composer.clear()
        self.clear_attachments()
        self.left_rail.sessions.clearSelection()
        self.set_status("New task ready")
        self.focus_composer()

    def _session_title(self) -> str:
        first = next((_as_text(m.get("content")) for m in self.chat_messages if m.get("role") == "user"), "New session")
        first = re.sub(r"\s+", " ", first).strip()
        return first[:48] + ("…" if len(first) > 48 else "")

    def save_current_session(self) -> bool:
        if not self.chat_messages:
            return True
        try:
            title = self._session_title()
            model = self.current_model_id()
            if self.current_session is None:
                self.current_session = self.session_bridge.create(
                    title, model, self.workspace, self.chat_messages
                )
            self.current_session = self.session_bridge.save(
                self.current_session,
                title=title,
                model=model,
                workspace=self.workspace,
                messages=self.chat_messages,
            )
            session_id = _as_text(_field(self.current_session, "id"))
            self.session_objects[session_id] = self.current_session
            self.left_rail.set_sessions(self.session_bridge.list(), session_id)
            return True
        except Exception as exc:
            message = f"Session history could not be saved: {exc}"
            self.set_status(message)
            self.inspector.append_activity(message)
            return False

    def archive_session(self, session_id: str) -> None:
        if self.active_kind:
            self.set_status("Stop the active run before archiving a session.")
            return
        if not session_id:
            self.set_status("Select a saved session to archive.")
            return
        session = self.session_objects.get(session_id)
        title = _as_text(_field(session, "title", "this session"))
        answer = QMessageBox.question(
            self,
            "Archive local session",
            f"Archive “{title}”? It will leave the active session list but remain in local archive storage.",
            QMessageBox.StandardButton.Archive if hasattr(QMessageBox.StandardButton, "Archive") else QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        accepted = (
            getattr(QMessageBox.StandardButton, "Archive", QMessageBox.StandardButton.Yes),
            QMessageBox.StandardButton.Yes,
        )
        if answer not in accepted:
            return
        archived = self.session_bridge.archive(session_id)
        if archived is None:
            self.set_status("The selected session could not be archived.")
            return
        self.session_objects.pop(session_id, None)
        if self.current_session is not None and _as_text(_field(self.current_session, "id")) == session_id:
            self.new_task(save_current=False)
        sessions = self.session_bridge.list()
        self.session_objects = {_as_text(_field(item, "id")): item for item in sessions}
        self.left_rail.set_sessions(sessions, _as_text(_field(self.current_session, "id")) if self.current_session else None)
        self.set_status(f"Archived session: {title}")
        self.inspector.append_activity(f"Session archived locally: {title}")

    def export_conversation(self) -> None:
        if not self.chat_messages:
            self.set_status("There is no conversation to export yet.")
            return
        suggested = re.sub(r"[^A-Za-z0-9._-]+", "-", self._session_title()).strip("-") or "onionmind-session"
        default_path = str((Path(self.workspace) if self.workspace else Path.home()) / f"{suggested}.md")
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Onionmind conversation",
            default_path,
            "Markdown (*.md);;Text (*.txt)",
        )
        if not path:
            return
        destination = Path(path)
        if not destination.suffix:
            destination = destination.with_suffix(".md")
        lines = [f"# {self._session_title()}", "", f"- Model: `{self._describe_model(self.current_model_id())}`"]
        if self.workspace:
            lines.append(f"- Workspace: `{self.workspace}`")
        lines.extend(("", "---", ""))
        for message in self.chat_messages:
            role = _as_text(message.get("role", "message"))
            heading = {"user": "Developer", "assistant": "Onionmind", "tool": "Local tool"}.get(role, role.title())
            content = message.get("content")
            if not isinstance(content, str):
                content = "[local image attachment]"
            lines.extend((f"## {heading}", "", content, ""))
        try:
            destination.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        except OSError as exc:
            self.set_status(f"Could not export conversation: {exc}")
            QMessageBox.warning(self, "Export failed", f"Could not write the export.\n\n{exc}")
            return
        self.set_status(f"Conversation exported to {destination}")
        self.inspector.append_activity(f"Conversation exported locally: {destination.name}")

    def load_session(self, session_id: str) -> None:
        if self.active_kind:
            self.set_status("Stop the active run before switching sessions.")
            return
        if not self.save_current_session():
            self.set_status(
                "The current session was not switched because its history could not be saved."
            )
            return
        session = self.session_objects.get(session_id)
        if session is None:
            session = next((item for item in self.session_bridge.list() if _as_text(_field(item, "id")) == session_id), None)
        if session is None:
            self.set_status("That saved session is no longer available.")
            return
        self.current_session = session
        self.chat_messages = [dict(message) for message in (_field(session, "messages", ()) or ())]
        self.transcript.clear()
        for message in self.chat_messages:
            role = message.get("role")
            if role in ("user", "assistant"):
                content = message.get("content")
                self.transcript.add_message(role, content if isinstance(content, str) else "[local image attachment]")
            elif role == "tool":
                self.transcript.add_tool_card(
                    _as_text(message.get("tool_name") or "Tool result"),
                    [("Local tool output", _as_text(message.get("content"))[:80])],
                )
        workspace = _as_text(_field(session, "workspace"))
        if workspace and Path(workspace).is_dir() and workspace != self.workspace:
            self.select_workspace(workspace)
        model = _as_text(_field(session, "model"))
        if model:
            if self.model_combo.findData(model) < 0:
                self.set_model_options([*self.installed_model_ids, model], model)
            else:
                self.model_combo.setCurrentIndex(self.model_combo.findData(model))
        self.set_status(f"Loaded session: {_field(session, 'title', 'Saved session')}")

    def submit(self) -> None:
        if self.active_kind:
            self.stop_active()
            return
        task = self.composer.toPlainText().strip()
        if not task and not self.attachments:
            self.set_status("Describe a task or attach a local file first.")
            self.focus_composer()
            return
        if not task:
            task = "Review the attached local files."
        attachment_names = [Path(path).name for path in self.attachments]
        visible_task = task + ("\n\nAttached locally: " + ", ".join(attachment_names) if attachment_names else "")
        message, agent_task = self._build_user_payload(task)
        self.chat_messages.append(message)
        self.transcript.add_message("user", visible_task)
        self.composer.clear()
        self.clear_attachments()
        self._set_active(self.mode)
        if not self.save_current_session():
            self.transcript.add_tool_card(
                "Session storage",
                [("Not saved", "The run will continue; check local disk access")],
            )
        if self.mode == "chat":
            self._start_chat()
        else:
            self._start_harness(agent_task)

    def _build_user_payload(self, task: str) -> tuple[dict[str, Any], str]:
        message: dict[str, Any] = {"role": "user", "content": task}
        agent_notes: list[str] = []
        images: list[str] = []
        text_sections: list[str] = []
        image_bytes = 0
        attachment_text_remaining = MAX_TEXT_TOTAL_BYTES

        def embed_image(path: Path, label: str) -> None:
            nonlocal image_bytes
            if len(images) >= MAX_IMAGE_COUNT:
                text_sections.append(
                    f"\n\n[{label} was not embedded: the {MAX_IMAGE_COUNT}-image limit was reached]"
                )
                return
            size = path.stat().st_size
            if size > MAX_IMAGE_FILE_BYTES:
                text_sections.append(
                    f"\n\n[{label} is larger than 20 MB and was not embedded]"
                )
                return
            if image_bytes + size > MAX_IMAGE_TOTAL_BYTES:
                text_sections.append(
                    f"\n\n[{label} was not embedded: the 24 MB aggregate image limit was reached]"
                )
                return
            with path.open("rb") as handle:
                payload = handle.read(MAX_IMAGE_FILE_BYTES + 1)
            if len(payload) > MAX_IMAGE_FILE_BYTES:
                text_sections.append(
                    f"\n\n[{label} grew beyond 20 MB while being read and was not embedded]"
                )
                return
            if image_bytes + len(payload) > MAX_IMAGE_TOTAL_BYTES:
                text_sections.append(
                    f"\n\n[{label} was not embedded: the 24 MB aggregate image limit was reached]"
                )
                return
            images.append(base64.b64encode(payload).decode("ascii"))
            image_bytes += len(payload)

        for path_string in self.attachments:
            path = Path(path_string)
            agent_notes.append(str(path))
            try:
                if path.suffix.lower() in IMAGE_SUFFIXES:
                    embed_image(path, f"Image {path.name}")
                else:
                    if attachment_text_remaining <= 0:
                        text_sections.append(
                            f"\n\n[Attached file {path.name} was omitted after the 256 KB aggregate attachment limit]"
                        )
                        continue
                    read_limit = min(MAX_TEXT_FILE_BYTES, attachment_text_remaining)
                    with path.open("rb") as handle:
                        payload = handle.read(read_limit + 1)
                    truncated = len(payload) > read_limit
                    raw = payload[:read_limit]
                    attachment_text_remaining -= len(raw)
                    text = raw.decode("utf-8", errors="replace")
                    marker = (
                        f"\n[Attached file truncated after {len(raw)} bytes; remaining content omitted.]"
                        if truncated
                        else ""
                    )
                    text_sections.append(
                        f"\n\nAttached file {path.name}:\n```\n{text}\n```{marker}"
                    )
            except OSError as exc:
                text_sections.append(f"\n\n[Could not read attached file {path.name}: {exc}]")
        selected_context = self.inspector.selected_context_files()
        if selected_context and self.workspace:
            root = Path(self.workspace).resolve()
            remaining = 256 * 1024
            for relative_path in selected_context[:12]:
                candidate = (root / relative_path).resolve(strict=False)
                try:
                    candidate.relative_to(root)
                except ValueError:
                    text_sections.append(f"\n\n[Selected context escaped the project and was ignored: {relative_path}]")
                    continue
                try:
                    if not candidate.is_file():
                        continue
                    if candidate.suffix.lower() in IMAGE_SUFFIXES:
                        embed_image(candidate, f"Selected image {relative_path}")
                        continue
                    if remaining <= 0:
                        text_sections.append("\n\n[Additional selected context was omitted after the 256 KB local context limit]")
                        break
                    read_limit = min(MAX_TEXT_FILE_BYTES, remaining)
                    with candidate.open("rb") as handle:
                        payload = handle.read(read_limit + 1)
                    truncated = len(payload) > read_limit
                    raw = payload[:read_limit]
                    remaining -= len(raw)
                    text = raw.decode("utf-8", errors="replace")
                    marker = (
                        f"\n[Selected context truncated after {len(raw)} bytes; remaining content omitted.]"
                        if truncated
                        else ""
                    )
                    text_sections.append(
                        f"\n\nSelected project context {relative_path}:\n```\n{text}\n```{marker}"
                    )
                except OSError as exc:
                    text_sections.append(f"\n\n[Could not read selected context {relative_path}: {exc}]")
            if len(selected_context) > 12:
                text_sections.append(
                    f"\n\n[{len(selected_context) - 12} additional selected context paths were omitted after the 12-file limit]"
                )
        if text_sections:
            message["content"] = task + "".join(text_sections)
        if images:
            message["images"] = images
        agent_task = task
        if agent_notes:
            agent_task += "\n\nUser-attached local paths:\n" + "\n".join(f"- {path}" for path in agent_notes)
        if selected_context:
            agent_task += "\n\nSelected project context paths:\n" + "\n".join(f"- {path}" for path in selected_context)
        return message, agent_task

    def _set_active(self, kind: Optional[str]) -> None:
        self.active_kind = kind
        running = bool(kind)
        self.send_button.setText("Stop" if running else "Send")
        self.send_button.setAccessibleName("Stop active run" if running else "Send task")
        if kind == "agent":
            self.send_button.setToolTip(
                "Stop Onionmind Agent; child processes it started may require manual termination"
            )
        elif kind == "chat":
            self.send_button.setToolTip("Stop local generation after the current read")
        else:
            self.send_button.setToolTip("Send task (Enter)")
        self.chat_button.setEnabled(not running)
        self.agent_button.setEnabled(not running)
        self.model_combo.setEnabled(not running)
        self.left_rail.projects.setEnabled(not running)
        self.left_rail.sessions.setEnabled(not running)
        if not running:
            self.stop_event = None
            self.harness_process = None
        self._sync_action_states()

    def _start_chat(self) -> None:
        self.stop_event = threading.Event()
        stop_event = self.stop_event
        model = self.current_model_id()
        history = copy.deepcopy(self.chat_messages)
        block = self.transcript.add_message("assistant", "")
        self.stream_block = block
        self.set_status(f"Streaming from {self._describe_model(model)}…")
        self.inspector.append_activity("Chat turn started on the local inference path")

        def chat_job(signals: WorkerSignals) -> dict[str, Any]:
            setattr(self.core, "MODEL", model)
            if not getattr(self.core, "BACKEND", None):
                detector = getattr(self.core, "detect_backend", None)
                if callable(detector):
                    detector()
            turn_stream = getattr(self.core, "turn_stream", None)
            if not callable(turn_stream):
                raise RuntimeError("The Onionmind core does not expose streaming chat.")
            raw = ""
            visible = ""

            def on_text(chunk: str) -> None:
                nonlocal raw, visible
                raw += _as_text(chunk)
                if "<think>" in raw and "</think>" not in raw:
                    candidate = ""
                elif "</think>" in raw:
                    candidate = raw.split("</think>")[-1]
                else:
                    candidate = raw
                if len(candidate) > len(visible):
                    delta = candidate[len(visible):]
                    visible = candidate
                    signals.text.emit(delta)

            def on_event(event: dict[str, Any]) -> None:
                signals.event.emit(dict(event))

            try:
                answer = turn_stream(history, on_text, stop_event=stop_event, on_event=on_event)
            except TypeError as exc:
                if "on_event" not in _as_text(exc):
                    raise
                answer = turn_stream(history, on_text, stop_event)
            return {"answer": _as_text(answer), "history": history}

        worker = self._start_worker(chat_job)
        worker.signals.text.connect(self._append_stream)
        worker.signals.event.connect(self._chat_event)
        worker.signals.result.connect(self._chat_complete)
        worker.signals.error.connect(self._chat_failed)

    def _append_stream(self, text: str) -> None:
        if self.stream_block is not None:
            self.stream_block.append_text(text)
            self.transcript._scroll_later()

    def _chat_event(self, event: dict[str, Any]) -> None:
        kind = _as_text(event.get("kind"))
        name = _as_text(event.get("name", "local tool"))
        display_name = name.replace("_", " ").strip().title()
        if kind == "tool_started":
            arguments = event.get("arguments") or {}
            detail = _as_text(arguments.get("query")) if isinstance(arguments, dict) else ""
            state = "Running through Tor · fails closed" if name == "web_search" else "Running locally"
            self.transcript.add_tool_card(display_name, [(detail or "Tool request", state)])
            activity = "Tor search started" if name == "web_search" else f"Local tool started: {display_name}"
            self.inspector.append_activity(activity)
        elif kind == "tool_finished":
            state = "Tor result returned" if name == "web_search" else "Finished"
            self.transcript.add_tool_card(display_name, [("Tool result", state)])
            activity = "Tor search finished" if name == "web_search" else f"Local tool finished: {display_name}"
            self.inspector.append_activity(activity)

    def _chat_complete(self, payload: dict[str, Any]) -> None:
        answer = payload.get("answer") or "The local model returned no answer."
        if self.stream_block is not None:
            self.stream_block.set_text(answer)
        self.chat_messages = payload.get("history") or [*self.chat_messages, {"role": "assistant", "content": answer}]
        if self.chat_messages and self.chat_messages[-1].get("role") == "assistant":
            self.chat_messages[-1]["content"] = answer
        if not self.chat_messages or self.chat_messages[-1].get("role") != "assistant":
            self.chat_messages.append({"role": "assistant", "content": answer})
        self.set_status("Chat turn complete")
        self.inspector.append_activity("Chat turn completed locally")
        self._set_active(None)
        self.save_current_session()

    def _chat_failed(self, message: str) -> None:
        message = _brand_runtime_text(message)
        if self.stream_block is not None:
            self.stream_block.set_text(f"Local inference could not continue. {message}")
        self.chat_messages.append({"role": "assistant", "content": f"Local inference failed: {message}"})
        self.set_status(f"Local inference failed: {message}")
        self.inspector.append_activity(f"Chat turn failed: {message}")
        self._set_active(None)
        self.save_current_session()

    def _start_harness(self, task: str) -> None:
        if not self.workspace:
            block = self.transcript.add_message(
                "assistant", "Agent mode needs a project folder. Open one with Ctrl+O, then send the task again."
            )
            self.stream_block = block
            self.set_status("Agent mode needs a project folder")
            self._set_active(None)
            return
        model = self.current_model_id()
        workspace = self.workspace
        self.harness_generation += 1
        generation = self.harness_generation
        self.stream_block = self.transcript.add_message(
            "assistant",
            "Preparing Onionmind Agent…\n\n" + self.harness_bridge.limitation,
            "Onionmind Agent",
        )
        self.set_status("Preparing Onionmind Agent…")

        def prepare(signals: WorkerSignals) -> dict[str, Any]:
            del signals
            available, reason = self.harness_bridge.check()
            if not available:
                return {"available": False, "reason": reason}
            argv, cwd = self.harness_bridge.build(model=model, task=task, cwd=workspace)
            return {"available": True, "argv": argv, "cwd": cwd}

        worker = self._start_worker(prepare)
        worker.signals.result.connect(
            lambda payload, value=generation: self._harness_prepared(value, payload)
        )
        worker.signals.error.connect(
            lambda message, value=generation: self._harness_start_failed(
                message, value
            )
        )

    def _harness_prepared(self, generation: int, payload: dict[str, Any]) -> None:
        if generation != self.harness_generation or self.active_kind != "agent":
            return
        if not payload.get("available"):
            reason = payload.get("reason") or "Onionmind Agent is unavailable."
            text = _brand_runtime_text(reason) + "\n\n" + self.harness_bridge.limitation
            if self.stream_block is None:
                self.stream_block = self.transcript.add_message("assistant", text, "Onionmind Agent")
            else:
                self.stream_block.set_text(text)
            self.chat_messages.append({"role": "assistant", "content": text})
            self.set_status("Onionmind Agent is unavailable")
            self.inspector.append_activity("Agent unavailable; no repository action started")
            self._set_active(None)
            self.save_current_session()
            return
        argv = list(payload.get("argv") or [])
        cwd = _as_text(payload.get("cwd") or self.workspace)
        if not argv:
            self._harness_start_failed(
                "The Agent adapter returned no executable command.", generation
            )
            return
        self.harness_output = ""
        start_text = (
            "Starting Onionmind Agent…\n"
            + self.harness_bridge.limitation
            + "\n\nAgent output (unverified until disk refresh):\n"
        )
        if self.stream_block is None:
            self.stream_block = self.transcript.add_message("assistant", start_text, "Onionmind Agent")
        else:
            self.stream_block.set_text(start_text)
        self.harness_output = self.stream_block.text
        self.terminal.show()
        self.terminal_toggle.setChecked(True)
        self.terminal.append("\n[agent] Starting Onionmind Agent\n")
        process = QProcess(self)
        self.harness_process = process
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.setWorkingDirectory(cwd)
        process.readyReadStandardOutput.connect(self._read_harness_output)
        process.finished.connect(self._harness_finished)
        process.errorOccurred.connect(self._harness_process_error)
        process.start(argv[0], argv[1:])
        self.set_status("Onionmind Agent is working…")
        self.inspector.append_activity("Agent run started; output is not yet proof of disk changes")

    def _read_harness_output(self) -> None:
        if self.harness_process is None:
            return
        chunk = bytes(self.harness_process.readAllStandardOutput()).decode("utf-8", errors="replace")
        clean = _brand_runtime_text(ANSI_ESCAPE.sub("", chunk))
        self.harness_output += clean
        if self.stream_block is not None:
            self.stream_block.append_text(clean)
        self.terminal.append(clean)
        self.transcript._scroll_later()

    def _harness_finished(self, exit_code: int, status: QProcess.ExitStatus) -> None:
        if self.active_kind != "agent":
            return
        normal = status == QProcess.ExitStatus.NormalExit
        suffix = f"\n\nAgent {'finished' if normal else 'crashed'} with exit code {exit_code}. Refreshing observed disk state."
        if self.stream_block is not None:
            self.stream_block.append_text(suffix)
        content = (self.stream_block.text if self.stream_block is not None else self.harness_output + suffix)
        self.chat_messages.append({"role": "assistant", "content": content})
        self.terminal.append(suffix + "\n")
        self.set_status(f"Agent exited with code {exit_code}; refreshing observed changes…")
        self.inspector.append_activity(f"Agent exited with code {exit_code}; disk inspection requested")
        self._set_active(None)
        self.save_current_session()
        self.refresh_workspace()

    def _harness_process_error(self, error: QProcess.ProcessError) -> None:
        if self.harness_process is None:
            return
        if error != QProcess.ProcessError.FailedToStart:
            return
        self._harness_start_failed(
            self.harness_process.errorString(), self.harness_generation
        )

    def _harness_start_failed(
        self, message: str, generation: Optional[int] = None
    ) -> None:
        if self.active_kind != "agent":
            return
        if generation is not None and generation != self.harness_generation:
            return
        message = _brand_runtime_text(message)
        text = f"Onionmind Agent could not start: {message}\n\n{self.harness_bridge.limitation}"
        if self.stream_block is None:
            self.stream_block = self.transcript.add_message("assistant", text, "Onionmind Agent")
        else:
            self.stream_block.set_text(text)
        self.chat_messages.append({"role": "assistant", "content": text})
        self.set_status(f"Agent could not start: {message}")
        self.inspector.append_activity("Agent failed before a repository action could be verified")
        self._set_active(None)
        self.save_current_session()

    def stop_active(self) -> None:
        if not self.active_kind:
            return
        if self.active_kind == "chat" and self.stop_event is not None:
            self.stop_event.set()
            self.set_status("Stopping the local model after the current read…")
        elif self.active_kind == "agent":
            if self.harness_process is None:
                self.harness_generation += 1
                text = "Agent start canceled before a repository action began."
                if self.stream_block is not None:
                    self.stream_block.set_text(text)
                self.chat_messages.append({"role": "assistant", "content": text})
                self.inspector.append_activity(text)
                self.set_status(text)
                self._set_active(None)
                self.save_current_session()
            else:
                self.harness_process.terminate()
                QTimer.singleShot(1500, self._kill_harness_if_running)
                self.set_status(
                    "Stopping Onionmind Agent; spawned child processes may require manual termination."
                )

    def _kill_harness_if_running(self) -> None:
        if self.harness_process is not None and self.harness_process.state() != QProcess.ProcessState.NotRunning:
            self.harness_process.kill()

    def open_model_manager(self) -> None:
        dialog = ModelManagerDialog(
            self.installed_model_ids,
            self.current_model_id(),
            self._describe_model,
            self,
        )
        self._model_dialog = dialog
        dialog.pullRequested.connect(lambda name: self.pull_model(name, dialog))
        dialog.exec()
        self._model_dialog = None

    def pull_model(self, name: str, dialog: ModelManagerDialog) -> None:
        pull = getattr(self.core, "pull_model", None)
        if not callable(pull):
            dialog.set_error("This Onionmind installation cannot add models yet")
            return
        stop_event = threading.Event()

        def pull_job(signals: WorkerSignals) -> bool:
            def progress(fraction: float, status: str) -> None:
                signals.progress.emit(float(fraction), _as_text(status))

            return bool(pull(name, on_progress=progress, stop_event=stop_event))

        worker = self._start_worker(pull_job)
        worker.signals.progress.connect(dialog.set_progress)
        worker.signals.error.connect(dialog.set_error)
        worker.signals.result.connect(lambda ok: self._model_pull_complete(name, dialog, ok))

    def _model_pull_complete(self, name: str, dialog: ModelManagerDialog, ok: bool) -> None:
        if not ok:
            dialog.set_error("Adding the model was stopped")
            return
        if name not in self.installed_model_ids:
            self.installed_model_ids.append(name)
        current = self.current_model_id()
        self.set_model_options(self.installed_model_ids, current)
        dialog.set_models(self.installed_model_ids, current)
        dialog.set_complete()
        self.inspector.append_activity(f"Onionmind model installed: {self._describe_model(name)}")

    def open_settings(self) -> None:
        SettingsDialog(self.data_root, self.harness_bridge.limitation, self).exec()

    def _populate_demo(self) -> None:
        self.set_model_options(["inferno", "blaze", "ember"], "inferno")
        self.model_status.set_status("Local · Ready", "good")
        self._set_tor_state("Connected", "good")
        self.workspace = str(Path.home() / "onion" / "leaflink")
        self.repo_label.setText("leaflink")
        self.repo_label.setToolTip(self.workspace)
        self.branch_label.setText("main")
        self.scope_status.setText(self.workspace)
        self.terminal.set_workspace(self.workspace)
        for project in ("membrane", "tor-watch", "ciphernote", "relay", "leaflink"):
            self.left_rail.add_project(str(Path.home() / "onion" / project), select=project == "leaflink")
        demo_sessions = [
            {"id": "s1", "title": "Refactor test helpers", "updated_at": "Today · 10:24"},
            {"id": "s2", "title": "Fix login redirect loop", "updated_at": "Yesterday"},
            {"id": "s3", "title": "Add onion address healthcheck", "updated_at": "Aug 20"},
            {"id": "s4", "title": "Update deps and lint", "updated_at": "Aug 18"},
            {"id": "s5", "title": "Investigate CI failure", "updated_at": "Aug 16"},
            {"id": "s6", "title": "Clarify CONTRIBUTING", "updated_at": "Aug 14"},
        ]
        self.left_rail.set_sessions(demo_sessions, "s1")
        self.transcript.clear()
        self.transcript.add_message(
            "user",
            "The user factory in tests is duplicated. Extract a shared builder in tests/helpers, update the callers, and run the test suite.",
        )
        self.transcript.add_message(
            "assistant",
            "I’ll inspect the existing factories and test conventions, make the smallest shared helper, then run the focused tests before the full suite.",
        )
        self.transcript.add_tool_card(
            "Read project context",
            [("tests/factories/user_factory.rb", "142 lines · OK"), ("tests/support/test_users.rb", "98 lines · OK")],
        )
        self.transcript.add_tool_card("Shell command", [("bundle exec rake test", "512 runs · exit 0")])
        self.transcript.add_message(
            "assistant",
            "All 512 tests passed. The shared UserBuilder now lives in tests/helpers/user_builder.rb and six call sites use it.\n\nObserved after the run: 4 changed files · +192 / -84. Review the actual diff in Changes.",
        )
        self.chat_messages = [
            {
                "role": "user",
                "content": "The user factory in tests is duplicated. Extract a shared builder in tests/helpers, update the callers, and run the test suite.",
            },
            {
                "role": "assistant",
                "content": "I’ll inspect the existing factories and test conventions, make the smallest shared helper, then run the focused tests before the full suite.",
            },
            {
                "role": "assistant",
                "content": "All 512 tests passed. The shared UserBuilder now lives in tests/helpers/user_builder.rb and six call sites use it.\n\nObserved after the run: 4 changed files · +192 / -84. Review the actual diff in Changes.",
            },
        ]
        self.terminal.output.setPlainText(
            "leaflink on main · local runtime 3.2.2\n"
            "> bundle exec rake test\n"
            "Run options: --seed 12345\n\n"
            "512 runs, 1532 assertions, 0 failures, 0 errors, 0 skips\n\n"
            "Coverage report generated for RSpec to coverage/.\n"
            "leaflink on main> "
        )
        demo_snapshot = {
            "root": self.workspace,
            "is_git": True,
            "branch": "main",
            "dirty": True,
            "agents_files": ["AGENTS.md"],
            "file_tree": [
                "AGENTS.md", "README.md", "app/models/user.rb", "tests/helpers/user_builder.rb",
                "tests/models/user_test.rb", "tests/controllers/users_controller_test.rb", "tests/factories/user_factory.rb",
            ],
            "tree_truncated": False,
            "summary": "4 observed changes · +192 / -84",
            "changes": [
                {"status": "M", "path": "tests/factories/user_factory.rb"},
                {"status": "M", "path": "tests/models/user_test.rb"},
                {"status": "M", "path": "tests/controllers/users_controller_test.rb"},
                {"status": "??", "path": "tests/helpers/user_builder.rb"},
            ],
            "diff": (
                "diff --git a/tests/factories/user_factory.rb b/tests/factories/user_factory.rb\n"
                "index 7e1a0c9..d4b32bf 100644\n"
                "--- a/tests/factories/user_factory.rb\n"
                "+++ b/tests/factories/user_factory.rb\n"
                "@@ -1,8 +1,5 @@\n"
                "-def build_user(overrides = {})\n"
                "-  User.new(default_user.merge(overrides))\n"
                "-end\n"
                "+require_relative '../helpers/user_builder'\n"
                "+include UserBuilder\n"
            ),
        }
        self.current_snapshot = demo_snapshot
        self.inspector.update_snapshot(demo_snapshot)
        self.inspector.append_activity("Observed Git state refreshed after Agent exit")
        self.inspector.append_activity("Agent finished with exit code 0")
        self.inspector.append_activity("Onionmind inference and Tor readiness reported separately")
        self.set_mode("agent")
        self.set_status("Ready · 4 observed changes · all inference local")
        self._sync_action_states()
        QTimer.singleShot(0, lambda: self.transcript.verticalScrollBar().setValue(0))

    def closeEvent(self, event: Any) -> None:
        self.save_current_session()
        if self.stop_event is not None:
            self.stop_event.set()
        if self.harness_process is not None and self.harness_process.state() != QProcess.ProcessState.NotRunning:
            self.harness_process.kill()
        self.terminal.stop()
        if self._tor_process is not None and self._tor_process.poll() is None:
            self._tor_process.terminate()
        event.accept()


def _load_desktop_core() -> Any:
    try:
        return importlib.import_module("onionmind_desktop_core")
    except (ImportError, ModuleNotFoundError):
        return None


def run(core_module: Any = None, demo: bool = False) -> int:
    """Run the standalone native Onionmind workbench."""
    if core_module is None:
        core_module = importlib.import_module("onionmind")
    app = QApplication.instance()
    owns_app = app is None
    if app is None:
        app = QApplication(sys.argv[:1])
    app.setOrganizationName(APP_NAME)
    app.setApplicationName(APP_ID)
    app.setApplicationDisplayName("Onionmind")
    app.setStyle("Fusion")
    _register_system_fonts(app)
    app.setStyleSheet(STYLE_SHEET)
    window = OnionmindWindow(core_module, _load_desktop_core(), demo=demo)
    app.setProperty("onionmindWindow", window)
    window.show()
    if _SCREENSHOT_PATH:
        window.resize(1440, 900)

        def capture() -> None:
            path = Path(_SCREENSHOT_PATH).expanduser().resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            ok = window.grab().save(str(path))
            print(f"Screenshot {'saved' if ok else 'failed'}: {path}")
            app.exit(0 if ok else 2)

        QTimer.singleShot(450, capture)
    if owns_app:
        return app.exec()
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Onionmind native local AI workbench")
    parser.add_argument("--demo", action="store_true", help="Show deterministic populated demo state")
    parser.add_argument("--screenshot", metavar="PATH", help="Save a deterministic demo screenshot and exit")
    args = parser.parse_args(argv)
    global _SCREENSHOT_PATH
    _SCREENSHOT_PATH = args.screenshot
    if args.screenshot:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return run(demo=args.demo or bool(args.screenshot))


if __name__ == "__main__":
    raise SystemExit(main())
