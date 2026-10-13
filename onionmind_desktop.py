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
    QColor,
    QDesktopServices,
    QFont,
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
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSplitter,
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
ACCENT = "#9184d9"
ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
MAX_TEXT_FILE_BYTES = 64 * 1024
MAX_TEXT_TOTAL_BYTES = 256 * 1024
MAX_IMAGE_FILE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_TOTAL_BYTES = 24 * 1024 * 1024
MAX_IMAGE_COUNT = 4
SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", ".venv", "venv", "dist", "build", "__pycache__"}
_SCREENSHOT_PATH: Optional[str] = None


# Nocturne: the workbench palette (dark first, plus the light ramp the
# appearance feature switches to). Ramps run 100 = lightest in dark and are
# reversed in light, so "n400" means the same contrast step in both themes.
NOCTURNE_DARK: dict[str, str] = {
    "bg": "#161826", "surface": "#232532", "text": "#e9e9ed", "accent": "#9184d9",
    "n100": "#f3f5fe", "n200": "#e4e7f5", "n300": "#cfd3e5", "n400": "#b2b6ca", "n500": "#9397ab",
    "n600": "#75798c", "n700": "#595d6c", "n800": "#3f424d", "n900": "#292b31",
    "a100": "#f5f4ff", "a200": "#e7e5fe", "a300": "#d2cefd", "a400": "#b5abfc", "a500": "#968ae0",
    "a600": "#796cbf", "a700": "#5d5294", "a800": "#423a6a", "a900": "#2b2741",
    # oklch(0.76 0.11 155), oklch(0.80 0.11 80), oklch(0.72 0.13 30) in sRGB
    "good": "#74c692", "warn": "#e3b667", "bad": "#eb8373",
}
NOCTURNE_LIGHT: dict[str, str] = {
    "bg": "#f2f3f8", "surface": "#fbfbfe", "text": "#1d1f2b", "accent": "#6b5fc0",
    "n100": "#292b31", "n200": "#3f424d", "n300": "#4a4e5c", "n400": "#5f6373", "n500": "#75798c",
    "n600": "#9397ab", "n700": "#c4c8d8", "n800": "#dcdfec", "n900": "#e8eaf4",
    "a100": "#2b2741", "a200": "#423a6a", "a300": "#5d5294", "a400": "#796cbf", "a500": "#968ae0",
    "a600": "#b5abfc", "a700": "#d2cefd", "a800": "#e1ddfd", "a900": "#ecebfd",
    "good": "#2f8a55", "warn": "#9a6a12", "bad": "#b8483a",
}
_PALETTE: dict[str, str] = dict(NOCTURNE_DARK)


def _mix(first: str, second: str, weight: float) -> str:
    """first blended over second: weight 1.0 is all first. Solid hex, so Qt
    style sheets get the same colour a CSS color-mix() would paint."""
    a = QColor(first)
    b = QColor(second)
    w = max(0.0, min(1.0, weight))
    return QColor(
        round(a.red() * w + b.red() * (1 - w)),
        round(a.green() * w + b.green() * (1 - w)),
        round(a.blue() * w + b.blue() * (1 - w)),
    ).name()


def theme_tokens(palette: Optional[dict[str, str]] = None) -> dict[str, str]:
    """Semantic roles over the palette - the only colours the sheet uses."""
    p = palette or _PALETTE
    bg, surface, text, accent = p["bg"], p["surface"], p["text"], p["accent"]
    dark = QColor(bg).lightness() < 128
    control = _mix(surface, bg, 0.55)
    return {
        "bg": bg,
        "pane": _mix(surface, bg, 0.35),
        "deep": _mix(bg, "#000000", 0.82) if dark else _mix(bg, "#ffffff", 0.6),
        "composer": _mix(surface, bg, 0.5),
        "card": surface,
        "tool": _mix(surface, bg, 0.6),
        "field": _mix(surface, bg, 0.7),
        "line": _mix(text, bg, 0.16),
        "line_soft": _mix(text, bg, 0.10),
        "line_strong": _mix(text, bg, 0.30),
        "text": text,
        "strong": p["n100"] if dark else text,
        "muted": p["n400"],
        "faint": p["n500"],
        "disabled": p["n600"],
        "control": control,
        "control_hover": _mix(text, control, 0.07),
        "control_pressed": _mix(text, control, 0.14),
        "accent": accent,
        "accent_text": p["a300"],
        "accent_hover": _mix(accent, bg, 0.12),
        "accent_pressed": _mix(accent, bg, 0.22),
        "accent_line": _mix(accent, bg, 0.5),
        "selection": _mix(accent, bg, 0.30),
        "on_accent": p["a100"] if dark else "#ffffff",
        "avatar_user": p["a600"] if dark else accent,
        "scroll": p["n800"],
        "scroll_hover": p["n700"],
        "good": p["good"], "warn": p["warn"], "bad": p["bad"],
        "good_bg": _mix(p["good"], bg, 0.12), "good_line": _mix(p["good"], bg, 0.45),
        "warn_bg": _mix(p["warn"], bg, 0.12), "warn_line": _mix(p["warn"], bg, 0.45),
        "bad_bg": _mix(p["bad"], bg, 0.12), "bad_line": _mix(p["bad"], bg, 0.45),
        "icon": p["n300"] if dark else p["n300"],
    }


def set_theme(name: str) -> None:
    """Switch the process palette; callers then rebuild the sheet and icons."""
    _PALETTE.clear()
    _PALETTE.update(NOCTURNE_LIGHT if name == "light" else NOCTURNE_DARK)


def current_theme() -> str:
    return "light" if _PALETTE.get("bg") == NOCTURNE_LIGHT["bg"] else "dark"


_STYLE_TEMPLATE = r"""
* {
    color: @text@;
}
QMainWindow, QDialog { background: @bg@; }
QWidget#windowRoot, QWidget#centerPane, QWidget#transcriptViewport { background: @bg@; }
QWidget#toolbar { background: @pane@; border-bottom: 1px solid @line@; }
QWidget#leftRail { background: @pane@; border-right: 1px solid @line@; }
QWidget#inspector { background: @pane@; border-left: 1px solid @line@; }
QFrame#terminalPane { background: @deep@; border-top: 1px solid @line@; }
QFrame#composerFrame { background: @composer@; border-top: 1px solid @line@; }
QFrame#card { background: @card@; border: 1px solid @line@; border-radius: 12px; }
QFrame#toolCard { background: @tool@; border: 1px solid @line@; border-radius: 8px; }
QFrame#modeSwitch { background: @bg@; border: 1px solid @line@; border-radius: 8px; }
QFrame#separator { background: @line@; min-width: 1px; max-width: 1px; }
QFrame#railDivider { color: @line@; }
QFrame#statusPill { background: @control@; border: 1px solid @line@; border-radius: 8px; }
QFrame#amberCard { background: @warn_bg@; border: 1px solid @warn_line@; border-radius: 12px; }
QFrame#errorCard { background: @bad_bg@; border: 1px solid @bad_line@; border-radius: 12px; }
QFrame#popover { background: @card@; border: 1px solid @line_strong@; border-radius: 12px; }
QFrame#banner { background: @card@; border: 1px solid @line@; border-radius: 8px; }
QFrame#chip { background: transparent; border: 1px solid @line@; border-radius: 8px; }
QFrame#boundaryPanel { background: @pane@; border-bottom: 1px solid @line@; }
QFrame#zoneMachine { background: @card@; border: 1px solid @line@; border-radius: 8px; }
QFrame#zoneOutside { background: transparent; border: 1px solid @line@; border-radius: 8px; }
QWidget#overlay { background: @bg@; }
QWidget#faultHost { background: transparent; }
QScrollArea#settingsPage { background: transparent; border: none; }
QScrollArea#settingsPage > QWidget > QWidget { background: transparent; }
QLabel { background: transparent; }
QLabel#brand { color: @strong@; font-size: @BRAND_PT@; font-weight: 500; }
QLabel#sectionTitle { color: @faint@; font-size: @LABEL_PT@; font-weight: 500; }
QLabel#muted, QLabel#meta, QLabel#disclosure { color: @muted@; }
QLabel#title { color: @strong@; font-weight: 500; }
QLabel#avatarUser { background: @avatar_user@; color: @on_accent@; border-radius: 16px; font-weight: 600; }
QLabel#avatarAssistant { background: @card@; color: @accent_text@; border: 1px solid @accent_line@; border-radius: 16px; font-weight: 700; }
QLabel#attachmentLabel { color: @accent_text@; background: @accent_hover@; border: 1px solid @accent_line@; border-radius: 8px; padding: 3px 7px; }
QLabel#success { color: @good@; }
QLabel#danger { color: @bad@; }
QLabel#warning { color: @warn@; }
QLabel#accent { color: @accent_text@; }
QLabel#thinkingLabel { color: @muted@; }
QLabel#chipLabel { color: @muted@; }
QLabel#kbd { color: @muted@; background: @control@; border: 1px solid @line@; border-radius: 4px; padding: 1px 6px; }
QPushButton, QToolButton, QComboBox, QLineEdit {
    background: @control@;
    border: 1px solid @line@;
    border-radius: 8px;
    padding: 5px 9px;
    min-height: 18px;
}
QPushButton:hover, QToolButton:hover, QComboBox:hover { background: @control_hover@; border-color: @line_strong@; }
QPushButton:pressed, QToolButton:pressed { background: @control_pressed@; }
QPushButton:disabled, QToolButton:disabled { color: @disabled@; background: @control@; border-color: @line_soft@; }
QPushButton:focus, QToolButton:focus, QComboBox:focus, QLineEdit:focus,
QTextEdit:focus, QPlainTextEdit:focus, QListWidget:focus, QTreeWidget:focus, QTabWidget:focus {
    border: 2px solid @accent@;
}
QPushButton#primaryButton { background: transparent; border: 1px solid @accent@; color: @accent@; font-weight: 500; padding-left: 17px; padding-right: 17px; }
QPushButton#primaryButton:hover { background: @accent_hover@; }
QPushButton#primaryButton:pressed { background: @accent_pressed@; }
QPushButton#primaryButton:focus { border: 2px solid @accent@; }
QPushButton#primaryButton:disabled { background: transparent; border-color: @line@; color: @disabled@; }
QPushButton#dangerButton { background: transparent; border: 1px solid @bad@; color: @bad@; }
QPushButton#dangerButton:hover { background: @bad_bg@; }
QPushButton#modeButton { background: transparent; border: none; padding: 4px 16px; color: @muted@; }
QPushButton#modeButton:checked { background: @accent_pressed@; color: @strong@; border: 1px solid @accent_line@; }
QPushButton#railAction { text-align: left; background: transparent; border-color: transparent; padding: 7px 9px; }
QPushButton#railAction:hover { background: @control_hover@; border-color: @line@; }
QPushButton#linkButton { background: transparent; border-color: transparent; color: @accent@; padding: 2px 6px; }
QPushButton#linkButton:hover { background: @accent_hover@; }
QPushButton#chipButton { background: transparent; border: 1px solid @line@; border-radius: 8px; color: @muted@; padding: 2px 9px; }
QPushButton#chipButton:hover { background: @control_hover@; color: @text@; }
QPushButton#paletteRow { text-align: left; background: transparent; border-color: transparent; padding: 7px 10px; }
QPushButton#paletteRow:hover, QPushButton#paletteRow[current="true"] { background: @accent_hover@; border-color: @accent_line@; }
QToolButton#bareButton { background: transparent; border-color: transparent; padding: 4px; }
QToolButton#bareButton:hover { background: @control_hover@; border-color: @line@; }
QToolButton#bareButton:checked { background: @accent_hover@; border-color: @accent_line@; }
QToolButton#bareButton:disabled { background: transparent; border-color: transparent; }
QComboBox { padding-right: 25px; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: @card@; border: 1px solid @line_strong@; selection-background-color: @selection@; selection-color: @strong@; outline: 0; }
QMenu { background: @card@; border: 1px solid @line_strong@; padding: 4px; }
QMenu::item { border-radius: 6px; padding: 6px 26px 6px 9px; }
QMenu::item:selected { background: @accent_hover@; color: @strong@; }
QMenu::item:disabled { color: @disabled@; }
QMenu::separator { height: 1px; background: @line@; margin: 4px 7px; }
QListWidget, QTreeWidget, QTableWidget {
    background: transparent;
    border: none;
    outline: 0;
    alternate-background-color: @field@;
}
QListWidget::item, QTreeWidget::item { border-radius: 6px; padding: 5px; }
QListWidget::item:hover, QTreeWidget::item:hover { background: @control_hover@; }
QListWidget::item:selected, QTreeWidget::item:selected { background: @accent_hover@; color: @strong@; }
QHeaderView::section { background: @pane@; color: @faint@; border: none; border-bottom: 1px solid @line@; padding: 5px 7px; }
QTableWidget { gridline-color: @line_soft@; }
QTreeWidget::branch { background: transparent; }
QTreeView::indicator { width: 13px; height: 13px; border: 1px solid @line_strong@; border-radius: 3px; background: @field@; }
QTreeView::indicator:hover { border-color: @accent@; }
QTreeView::indicator:checked { border-color: @accent@; background: @accent@; }
QTextEdit, QPlainTextEdit {
    background: @field@;
    border: 1px solid @line@;
    border-radius: 8px;
    selection-background-color: @selection@;
    selection-color: @strong@;
    padding: 7px;
}
QTextEdit#composer { background: @field@; border: 1px solid @line@; padding: 10px; }
QPlainTextEdit#terminalOutput, QPlainTextEdit#diffView, QPlainTextEdit#runOutput {
    font-family: "Cascadia Mono", "Consolas", "DejaVu Sans Mono", monospace;
    font-size: @MONO_PT@;
    background: @deep@;
    border: none;
    border-radius: 0;
    padding: 7px 10px;
}
QLineEdit#terminalInput { font-family: "Cascadia Mono", "Consolas", monospace; background: @deep@; border-color: @line@; }
QTabWidget::pane { border: none; border-top: 1px solid @line@; top: -1px; }
QTabBar::tab { background: transparent; color: @muted@; padding: 12px 16px 10px 16px; border-bottom: 2px solid transparent; }
QTabBar::tab:hover { color: @text@; }
QTabBar::tab:selected { color: @strong@; border-bottom-color: @accent@; }
QCheckBox { spacing: 7px; color: @text@; }
QCheckBox::indicator { width: 15px; height: 15px; border: 1px solid @line_strong@; background: @field@; border-radius: 4px; }
QCheckBox::indicator:checked { background: @accent@; border-color: @accent@; }
QCheckBox:focus { color: @strong@; }
QRadioButton { spacing: 7px; color: @text@; }
QProgressBar { background: @field@; border: 1px solid @line@; border-radius: 6px; text-align: center; min-height: 18px; }
QProgressBar::chunk { background: @accent@; border-radius: 5px; }
QProgressBar#meter { background: @line@; border: none; border-radius: 2px; min-height: 4px; max-height: 4px; }
QProgressBar#meter::chunk { background: @accent@; border-radius: 2px; }
QProgressBar#meter[level="warn"]::chunk { background: @warn@; }
QSplitter::handle { background: @line@; }
QSplitter::handle:hover { background: @accent_line@; }
QStatusBar { background: @pane@; border-top: 1px solid @line@; color: @faint@; }
QStatusBar::item { border: none; }
QPushButton#updateStatus { background: transparent; border: 1px solid transparent; border-radius: 8px; color: @muted@; padding: 2px 8px; }
QPushButton#updateStatus:hover { background: @control_hover@; border-color: @line@; color: @strong@; }
QPushButton#updateStatus[attention="true"] { background: @accent_pressed@; border: 1px solid @accent@; border-radius: 8px; color: @strong@; font-weight: 600; padding: 2px 12px; }
QPushButton#updateStatus[attention="true"]:hover { background: @accent_hover@; }
QPushButton#torStatusAction {
    background: @control@;
    border: 1px solid @line_strong@;
    border-radius: 12px;
    color: @text@;
    font-weight: 500;
    min-width: 116px;
    min-height: 36px;
    padding: 2px 10px 2px 3px;
    text-align: left;
}
QPushButton#torStatusAction[torState="ready"] { background: @good_bg@; border-color: @good_line@; color: @good@; }
QPushButton#torStatusAction[torState="checking"] { background: @warn_bg@; border-color: @warn_line@; color: @warn@; }
QPushButton#torStatusAction[torState="error"] { background: @bad_bg@; border-color: @bad_line@; color: @bad@; }
QPushButton#torStatusAction:hover { background: @control_hover@; border-color: @line_strong@; }
QPushButton#torStatusAction:focus { border: 2px solid @accent@; }
QPushButton#torStatusAction:pressed { background: @control_pressed@; border-color: @accent@; }
QPushButton#torStatusAction:disabled { background: @control@; border-color: @line_soft@; color: @disabled@; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: @scroll@; min-height: 30px; border-radius: 4px; margin: 2px; }
QScrollBar::handle:vertical:hover { background: @scroll_hover@; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: transparent; height: 10px; }
QScrollBar::handle:horizontal { background: @scroll@; min-width: 30px; border-radius: 4px; margin: 2px; }
QToolTip { background: @card@; color: @strong@; border: 1px solid @line_strong@; padding: 5px; }
"""


def build_style_sheet(scale: float = 1.0) -> str:
    """The committed stylesheet with its three fixed point sizes scaled.

    Every other rule inherits the application font, which apply_text_scale
    scales from the captured platform base - so one knob moves all text
    without a second size system. Colours come from the current palette's
    semantic roles (theme_tokens), so a theme switch is one rebuild.
    """
    scale = max(0.75, min(1.5, float(scale)))
    sheet = (
        _STYLE_TEMPLATE.replace("@BRAND_PT@", f"{11 * scale:g}pt")
        .replace("@LABEL_PT@", f"{8.5 * scale:g}pt")
        .replace("@MONO_PT@", f"{9 * scale:g}pt")
    )
    for name, value in sorted(theme_tokens().items(), key=lambda item: -len(item[0])):
        sheet = sheet.replace(f"@{name}@", value)
    return sheet


STYLE_SHEET = build_style_sheet(1.0)

# Mirrors the core's PREFERENCE_DEFAULTS for the rare window that runs without
# a desktop_core; preferences are presentation-only and never touch the Tor
# boundary or the updater permission.
_FALLBACK_PREFERENCES: dict[str, Any] = {
    "text_scale": "system",
    "enter_sends": True,
    "show_terminal_on_launch": False,
    "startup_mode": "remember",
    "reduce_motion": "system",
    "save_history": True,
    "remember_drafts": True,
    "clear_on_exit": False,
    "context_window": 16384,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _as_text(value: Any) -> str:
    return "" if value is None else str(value)


def _path_key(value: Any) -> str:
    """Return a stable comparison key without requiring the path to still exist."""

    text = _as_text(value).strip()
    if not text:
        return ""
    return os.path.normcase(os.path.normpath(os.path.abspath(os.path.expanduser(text))))


def _path_is_within(value: Any, directory: Any) -> bool:
    """Return whether value is the directory itself or one of its descendants."""

    path = _path_key(value)
    parent = _path_key(directory)
    if not path or not parent:
        return False
    try:
        return os.path.commonpath((path, parent)) == parent
    except ValueError:  # Different drives on Windows.
        return False


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


_THINK_TAG_PATTERN = r"<\s*(/?)\s*think(?:\s[^>]*)?>"
_THINK_TAG = re.compile(_THINK_TAG_PATTERN, re.IGNORECASE)
_REASONING_FIELDS = frozenset(
    {"analysis", "reasoning", "reasoning_content", "thinking"}
)


def _think_tag_candidate(candidate: str) -> tuple[str, bool]:
    """Classify text beginning with ``<`` against prefixes of _THINK_TAG."""
    if not candidate.startswith("<"):
        return "invalid", False
    index, size = 1, len(candidate)
    while index < size and candidate[index].isspace():
        index += 1
    if index == size:
        return "prefix", False

    closing = candidate[index] == "/"
    if closing:
        index += 1
        while index < size and candidate[index].isspace():
            index += 1
        if index == size:
            return "prefix", True

    for expected in "think":
        if index == size:
            return "prefix", closing
        if re.fullmatch(expected, candidate[index], re.IGNORECASE) is None:
            return "invalid", closing
        index += 1
    if index == size:
        return "prefix", closing
    if candidate[index] == ">":
        match = _THINK_TAG.fullmatch(candidate[:index + 1])
        return ("complete", bool(match.group(1))) if match else ("invalid", closing)
    if not candidate[index].isspace():
        return "invalid", closing
    index += 1
    while index < size:
        if candidate[index] == ">":
            match = _THINK_TAG.fullmatch(candidate[:index + 1])
            return ("complete", bool(match.group(1))) if match else ("invalid", closing)
        index += 1
    return "prefix", closing


def _partial_think_tag(text: str) -> tuple[int, bool] | None:
    start = text.find("<")
    while start >= 0:
        state, closing = _think_tag_candidate(text[start:])
        if state == "prefix":
            return start, closing
        start = text.find("<", start + 1)
    return None


def _strip_thinking(text: Any) -> str:
    """Fail closed when removing completed or truncated reasoning blocks."""

    value = _as_text(text)
    visible: list[str] = []
    cursor = 0
    depth = 0
    for tag in _THINK_TAG.finditer(value):
        closing = bool(tag.group(1))
        if closing:
            if depth:
                depth -= 1
                if depth == 0:
                    cursor = tag.end()
            else:
                visible.clear()
                cursor = tag.end()
            continue
        if depth == 0:
            visible.append(value[cursor:tag.start()])
        depth += 1
    if depth == 0:
        tail = value[cursor:]
        partial = _partial_think_tag(tail)
        if partial is None:
            visible.append(tail)
        elif partial[1]:
            visible.clear()
        else:
            visible.append(tail[:partial[0]])
    return "".join(visible).strip()


def _sanitize_assistant_messages(
    messages: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Copy history while preserving tool protocol and dropping reasoning."""

    sanitized: list[dict[str, Any]] = []
    for message in messages:
        item = copy.deepcopy(dict(message))
        if item.get("role") == "assistant":
            for key in list(item):
                if isinstance(key, str) and key.casefold() in _REASONING_FIELDS:
                    item.pop(key, None)
            content = item.get("content")
            item["content"] = _sanitize_assistant_content(content)
        sanitized.append(item)
    return sanitized


def _sanitize_assistant_content(value: Any) -> Any:
    if isinstance(value, str):
        return _strip_thinking(value)
    if isinstance(value, dict):
        return {
            key: _sanitize_assistant_content(item) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_assistant_content(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_assistant_content(item) for item in value)
    return copy.deepcopy(value)


def _conversation_markdown(
    title: str,
    model: str,
    workspace: Optional[str],
    messages: Iterable[dict[str, Any]],
) -> str:
    """Format a local export after defensively cleaning legacy history."""

    lines = [f"# {title}", "", f"- Model: `{model}`"]
    if workspace:
        lines.append(f"- Workspace: `{workspace}`")
    lines.extend(("", "---", ""))
    for message in _sanitize_assistant_messages(messages):
        role = _as_text(message.get("role", "message"))
        heading = {"user": "Developer", "assistant": "Onionmind", "tool": "Local tool"}.get(
            role, role.title()
        )
        content = message.get("content")
        if not isinstance(content, str):
            content = "[local image attachment]"
        lines.extend((f"## {heading}", "", content, ""))
    return "\n".join(lines).rstrip() + "\n"


class ThinkingStreamFilter:
    """Bound model output until the completed response can be sanitized.

    Incremental display cannot safely handle a model that omits its opening
    reasoning tag: text may already be visible when a later closing tag proves
    it was private.  Buffering makes the completed sanitizer the only release
    boundary.  The hard limit bounds memory and fails closed on abnormal output.
    """

    MAX_CHARACTERS = 1_048_576

    def __init__(self, max_characters: int = MAX_CHARACTERS) -> None:
        if not isinstance(max_characters, int) or max_characters <= 0:
            raise ValueError("max_characters must be a positive integer")
        self._max_characters = max_characters
        self._chunks: list[str] = []
        self._characters = 0
        self._finished = False

    def feed(self, chunk: Any) -> str:
        """Store one transport chunk and deliberately emit nothing."""
        if self._finished:
            return ""
        text = _as_text(chunk)
        if self._characters + len(text) > self._max_characters:
            self.abort()
            raise RuntimeError("Model response exceeded the privacy buffer limit.")
        if text:
            self._chunks.append(text)
            self._characters += len(text)
        return ""

    def finish(self) -> str:
        """Sanitize one completed response, erase its raw chunks, and return it."""
        if self._finished:
            return ""
        raw = "".join(self._chunks)
        self._chunks.clear()
        self._characters = 0
        self._finished = True
        return _strip_thinking(raw)

    def abort(self) -> None:
        """Drop buffered model output after stop or failure."""
        self._chunks.clear()
        self._characters = 0
        self._finished = True


def _friendly_error(core: Any, exc: BaseException) -> str:
    text = _as_text(exc) or exc.__class__.__name__
    helper = getattr(core, "user_error", None)
    if callable(helper):
        try:
            text = _as_text(helper(exc)) or text
        except Exception:
            pass
    return _brand_runtime_text(text)


def _icon(name: str, size: int = 18, color: Optional[str] = None) -> QIcon:
    """Render Onionmind's compact, platform-neutral monochrome icon language."""
    color = color or theme_tokens()["icon"]
    canvas = QPixmap(size, size)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(size / 18.0, size / 18.0)
    pen = QPen(QColor(color))
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
    elif name == "pencil":
        line(5, 13, 12.8, 5.2)
        line(11.4, 3.8, 14.2, 6.6)
        line(3.6, 14.4, 5, 13)
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
        painter.setBrush(QColor(color))
        painter.drawRoundedRect(QRectF(5, 5, 8, 8), 1.2, 1.2)
    elif name == "power":
        line(9, 2.5, 9, 8.8)
        painter.drawArc(QRectF(3.5, 4, 11, 11), 45 * 16, 270 * 16)
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
    elif name == "sparkle":
        for x1, y1, x2, y2 in ((9, 2.5, 9, 6), (9, 12, 9, 15.5), (2.5, 9, 6, 9), (12, 9, 15.5, 9)):
            line(x1, y1, x2, y2)
        line(6, 6, 7.4, 7.4)
        line(12, 12, 10.6, 10.6)
        line(12, 6, 10.6, 7.4)
        line(6, 12, 7.4, 10.6)
    elif name == "lock":
        box(4, 8, 10, 7.5, 1.4)
        painter.drawArc(QRectF(5.8, 3, 6.4, 8), 0, 180 * 16)
        line(9, 10.8, 9, 12.6)
    elif name == "search":
        painter.drawEllipse(QRectF(3, 3, 9, 9))
        line(10.8, 10.8, 15, 15)
    elif name == "cpu":
        box(5, 5, 8, 8, 1)
        for offset in (7, 9, 11):
            line(offset, 2.5, offset, 5)
            line(offset, 13, offset, 15.5)
            line(2.5, offset, 5, offset)
            line(13, offset, 15.5, offset)
    elif name == "shield":
        path = QPainterPath(QPointF(9, 2.5))
        path.lineTo(14.5, 4.5)
        path.cubicTo(14.5, 10, 12.5, 13.5, 9, 15.5)
        path.cubicTo(5.5, 13.5, 3.5, 10, 3.5, 4.5)
        path.closeSubpath()
        painter.drawPath(path)
    elif name == "globe":
        painter.drawEllipse(QRectF(2.5, 2.5, 13, 13))
        painter.drawEllipse(QRectF(6, 2.5, 6, 13))
        line(2.8, 9, 15.2, 9)
    elif name == "copy":
        box(6, 6, 9, 9.5, 1.2)
        line(3.5, 12, 3.5, 3.5)
        line(3.5, 3.5, 11, 3.5)
    elif name == "regenerate":
        painter.drawArc(QRectF(3, 3, 12, 12), 60 * 16, 300 * 16)
        line(12.6, 2.6, 12.2, 5.6)
        line(12.2, 5.6, 15.1, 6)
    elif name == "branch":
        painter.drawEllipse(QRectF(3.5, 2.5, 3.5, 3.5))
        painter.drawEllipse(QRectF(3.5, 12, 3.5, 3.5))
        painter.drawEllipse(QRectF(11, 5, 3.5, 3.5))
        line(5.25, 6, 5.25, 12)
        painter.drawArc(QRectF(5.25, 4, 7.5, 7.5), 180 * 16, 90 * 16)
    elif name == "revert":
        line(3.5, 7, 7, 3.5)
        line(3.5, 7, 7, 10.5)
        path = QPainterPath(QPointF(3.8, 7))
        path.lineTo(11, 7)
        path.cubicTo(15.5, 7, 15.5, 14.5, 11, 14.5)
        path.lineTo(7, 14.5)
        painter.drawPath(path)
    elif name == "keyboard":
        box(2, 5, 14, 9, 1.4)
        for x in (5, 8, 11):
            line(x, 8, x + 0.4, 8)
        line(5.5, 11, 12.5, 11)
    elif name == "warning":
        path = QPainterPath(QPointF(9, 2.5))
        path.lineTo(16, 15)
        path.lineTo(2, 15)
        path.closeSubpath()
        painter.drawPath(path)
        line(9, 7, 9, 10.5)
        line(9, 12.6, 9, 12.8)
    elif name == "check":
        line(3.5, 9.5, 7.5, 13.5)
        line(7.5, 13.5, 14.5, 4.5)
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


_ICON_ROLE = int(Qt.ItemDataRole.UserRole) + 7


def _themed_icon(target: Any, name: str, size: int = 18) -> Any:
    """Set an authored icon and remember which, so a theme switch can redraw it.

    Widgets and actions keep the name as a property; list items (not
    QObjects) keep it in a data role."""
    spec = f"{name}:{size}"
    if hasattr(target, "setProperty"):
        target.setProperty("omIcon", spec)
    elif hasattr(target, "setData"):
        target.setData(_ICON_ROLE, spec)
    target.setIcon(_icon(name, size))
    return target


def _retheme_icons(root: QWidget) -> None:
    from PySide6.QtCore import QObject as _QObject

    def redraw(holder: Any, spec: Any) -> None:
        if spec:
            name, _, size = _as_text(spec).partition(":")
            holder.setIcon(_icon(name, int(size or 18)))

    for child in root.findChildren(_QObject):
        if hasattr(child, "setIcon") and hasattr(child, "property"):
            redraw(child, child.property("omIcon"))
        if isinstance(child, QListWidget):
            for row in range(child.count()):
                item = child.item(row)
                redraw(item, item.data(_ICON_ROLE))
        if isinstance(child, QTreeWidget):
            pending = [child.topLevelItem(i) for i in range(child.topLevelItemCount())]
            while pending:
                node = pending.pop()
                spec = node.data(0, _ICON_ROLE)
                if spec:
                    name, _, size = _as_text(spec).partition(":")
                    node.setIcon(0, _icon(name, int(size or 18)))
                pending.extend(node.child(i) for i in range(node.childCount()))


def _tor_control_icon(state: str, angle: int = 0, size: int = 36) -> QIcon:
    """Render one clean 1B-style power target or one verification spinner."""
    tokens = theme_tokens()
    colors = {
        "off": (tokens["line_strong"], tokens["strong"]),
        "ready": (_mix(tokens["good"], "#000000", 0.62), "#ffffff"),
        "checking": (tokens["warn_line"], tokens["warn"]),
        "error": (tokens["bad_line"], "#ffffff"),
    }
    fill, ink = colors.get(state, colors["off"])
    canvas = QPixmap(size, size)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(size / 36.0, size / 36.0)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(fill))
    if state == "checking":
        # The connecting state is deliberately a single spinner on a quiet
        # halo: no power glyph, inset disc border, or second ring competes with it.
        painter.drawEllipse(QRectF(2.0, 2.0, 32.0, 32.0))
        spinner_pen = QPen(QColor(ink))
        spinner_pen.setWidthF(2.6)
        spinner_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(spinner_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(QRectF(7.0, 7.0, 22.0, 22.0), (90 - angle) * 16, 270 * 16)
    else:
        # At rest the filled target and the correctly oriented power symbol are
        # the only two layers. The arc leaves its gap at twelve o'clock, clear of
        # the stem, instead of reading as a clipped C-shaped ring.
        painter.drawEllipse(QRectF(2.0, 2.0, 32.0, 32.0))
        glyph_pen = QPen(QColor(ink))
        glyph_pen.setWidthF(2.6)
        glyph_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        glyph_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(glyph_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(QPointF(18.0, 7.0), QPointF(18.0, 17.0))
        painter.drawArc(QRectF(9.0, 9.0, 18.0, 18.0), 135 * 16, 270 * 16)
    painter.end()
    return QIcon(canvas)


def _register_system_fonts(app: QApplication) -> None:
    """Register platform fonts and keep the platform's proportional UI size.

    The first proportional family that registers becomes the UI font, so the
    order is a legibility ranking rather than a list: Verdana and DejaVu Sans
    (its free cousin) carry a taller x-height and wider spacing than the
    platform defaults, which reads better at UI sizes without needing a bigger
    point size - and a bigger point size is what clips the two-line rows in
    the rail. Both ship with their platform, so nothing is bundled.
    """
    system_point_size = app.font().pointSizeF()
    candidates = [
        Path("C:/Windows/Fonts/verdana.ttf"),
        Path("C:/Windows/Fonts/verdanab.ttf"),
        Path("C:/Windows/Fonts/segoeui.ttf"),
        Path("C:/Windows/Fonts/segoeuib.ttf"),
        Path("C:/Windows/Fonts/consola.ttf"),
        Path("C:/Windows/Fonts/consolab.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
    ]
    ui_family = ""
    # Nocturne's face is Inter at weight 500 for headings. It is not bundled,
    # so it wins only where the system already has it.
    if "Inter" in QFontDatabase.families():
        ui_family = "Inter"
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


# The platform UI font captured before the first text-scale application, so
# every later scale multiplies the system's own point size instead of compound
# scaling whatever the previous preference left behind.
_BASE_APP_FONT: Optional[QFont] = None


def apply_text_scale(scale: float) -> None:
    """Scale all workbench text from the captured platform base font."""
    global _BASE_APP_FONT
    app = QApplication.instance()
    if app is None:
        return
    if _BASE_APP_FONT is None:
        _BASE_APP_FONT = QFont(app.font())
    font = QFont(_BASE_APP_FONT)
    size = _BASE_APP_FONT.pointSizeF()
    if size > 0:
        font.setPointSizeF(size * scale)
    app.setFont(font)
    app.setStyleSheet(build_style_sheet(scale))


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)
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


class StatusDot(QWidget):
    def __init__(self, color: Optional[str] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._color = QColor(color or theme_tokens()["faint"])
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


class _StateColors:
    """StatusPill.COLORS, read from the live palette so a theme switch repaints."""

    _ROLES = {"good": "good", "warn": "warn", "bad": "bad", "idle": "faint", "busy": "accent"}

    def get(self, state: str, default: Any = None) -> Any:
        role = self._ROLES.get(state)
        return theme_tokens()[role] if role else default

    def __getitem__(self, state: str) -> str:
        return theme_tokens()[self._ROLES[state]]


class StatusPill(QFrame):
    clicked = Signal()

    COLORS = _StateColors()

    def __init__(self, prefix: str, text: str, state: str = "idle", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.prefix = prefix
        self.setObjectName("statusPill")
        self._state = state
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
        self._state = state
        self.label.setText(text)
        self.dot.set_color(self.COLORS.get(state, self.COLORS["idle"]))
        self.setAccessibleName(f"{self.prefix} status: {text}")

    def refresh_theme(self) -> None:
        self.dot.set_color(self.COLORS.get(self._state, self.COLORS["idle"]))

    def make_clickable(self) -> None:
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class StatusActionButton(QPushButton):
    """A compact toolbar translation of reference 1B's circular power target."""

    COLORS = StatusPill.COLORS

    def __init__(
        self,
        prefix: str,
        text: str,
        state: str,
        action: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.prefix = prefix
        self._status_text = text
        self._action_text = action
        self._action_accessible = action
        self._visual_state = "off"
        self._spinner_angle = 0
        self._spinner_timer = QTimer(self)
        self._spinner_timer.setInterval(75)
        self._spinner_timer.timeout.connect(self._advance_spinner)
        self.setObjectName("torStatusAction")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.set_status(text, state)

    def status_text(self) -> str:
        return self._status_text

    def action_text(self) -> str:
        return self._action_text

    def set_status(self, text: str, state: str = "idle") -> None:
        self._status_text = text
        visual_state = {
            "good": "ready",
            "warn": "checking",
            "busy": "checking",
            "bad": "error",
        }.get(state, "off")
        self._visual_state = visual_state
        self._spinner_angle = 0
        self.setProperty("torState", visual_state)
        self._update_state_icon()
        style = self.style()
        style.unpolish(self)
        style.polish(self)
        self._sync_copy()
        self._sync_spinner()

    def set_action(
        self,
        text: str,
        *,
        enabled: bool,
        tooltip: str,
        accessible: str,
    ) -> None:
        self._action_text = text
        self._action_accessible = accessible
        self.setEnabled(enabled)
        self.setToolTip(tooltip)
        self._sync_copy()

    def _sync_copy(self) -> None:
        visible_text = f"{self.prefix} is " + (
            "on" if self.property("torState") == "ready" else "off"
        )
        self.setText(visible_text)
        self.setAccessibleName(f"{visible_text}. {self._action_accessible}")
        description = {
            "Ready": "Onionmind has verified a Tor circuit.",
            "Checking…": "Onionmind is starting or verifying Tor.",
            "Unavailable": "Onionmind could not verify Tor.",
        }.get(self._status_text, "Tor is not active for Onionmind.")
        self.setAccessibleDescription(description)

    def _update_state_icon(self) -> None:
        self.setIcon(_tor_control_icon(self._visual_state, self._spinner_angle))
        self.setIconSize(QSize(36, 36))

    def _advance_spinner(self) -> None:
        self._spinner_angle = (self._spinner_angle + 30) % 360
        self._update_state_icon()

    def _sync_spinner(self) -> None:
        should_run = (
            self._visual_state == "checking"
            and self.isVisible()
            and _ui_animations_enabled()
        )
        if should_run:
            self._spinner_timer.start()
        else:
            self._spinner_timer.stop()

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        self._sync_spinner()

    def hideEvent(self, event: Any) -> None:
        self._spinner_timer.stop()
        super().hideEvent(event)


def _v2(host: Any, name: str, *args: Any, default: Any = None) -> Any:
    """Call a v2 hook when the host has it. Core paths (chat, Tor) are also
    driven on minimal hosts by the original tests; v2 must not be required."""
    hook = getattr(host, name, None)
    return hook(*args) if callable(hook) else default


def _tor_is_enabled(core: Any) -> bool:
    getter = getattr(core, "tor_enabled", None)
    if not callable(getter):
        return True
    try:
        return bool(getter())
    except Exception:
        return False


def _set_tor_enabled(core: Any, enabled: bool) -> bool:
    setter = getattr(core, "set_tor_enabled", None)
    if not callable(setter):
        return False
    setter(enabled)
    return True


def _set_tor_action(
    host: Any,
    text: str,
    *,
    enabled: bool = True,
    tooltip: str = "",
) -> None:
    """Keep the Tor status/action control explicit, operable, and truthful."""
    accessible = {
        "Turn on": "Turn on Tor proxy",
        "Turn off": "Turn off Tor proxy for Onionmind",
        "Cancel": "Cancel Tor proxy startup",
        "Retry": "Retry Tor proxy verification",
        "External": "Tor proxy is managed outside Onionmind",
        "Unavailable": "Tor proxy control is unavailable",
    }.get(text, f"Tor proxy action: {text}")
    status_control = getattr(host, "tor_status", None)
    set_action = getattr(status_control, "set_action", None)
    if callable(set_action):
        set_action(text, enabled=enabled, tooltip=tooltip, accessible=accessible)


# The reduce-motion preference as a process-wide override. None means follow
# the system (and the ONIONMIND_REDUCE_MOTION environment variable).
_MOTION_OVERRIDE: Optional[bool] = None


def set_motion_override(reduce_motion: str) -> None:
    global _MOTION_OVERRIDE
    if reduce_motion == "reduced":
        _MOTION_OVERRIDE = False
    elif reduce_motion == "full":
        _MOTION_OVERRIDE = True
    else:
        _MOTION_OVERRIDE = None


# v2 keyboard: also follow the reduced-motion setting of GNOME and macOS,
# which the original only read on Windows. None = not probed yet.
_SYSTEM_MOTION_PROBE = False
_SYSTEM_REDUCES_MOTION: Optional[bool] = None


def set_system_motion_probe(on: bool) -> None:
    global _SYSTEM_MOTION_PROBE
    _SYSTEM_MOTION_PROBE = bool(on)


def _system_reduces_motion() -> bool:
    """GNOME's enable-animations / macOS reduceMotion, read once."""
    global _SYSTEM_REDUCES_MOTION
    if _SYSTEM_REDUCES_MOTION is None:
        command = (["defaults", "read", "com.apple.universalaccess", "reduceMotion"] if sys.platform == "darwin"
                   else ["gsettings", "get", "org.gnome.desktop.interface", "enable-animations"])
        try:
            output = subprocess.run(command, capture_output=True, text=True, timeout=3).stdout.strip().lower()
        except (OSError, subprocess.SubprocessError):
            output = ""
        _SYSTEM_REDUCES_MOTION = output == "1" if sys.platform == "darwin" else output == "false"
    return _SYSTEM_REDUCES_MOTION


def _ui_animations_enabled() -> bool:
    if _MOTION_OVERRIDE is not None:
        return _MOTION_OVERRIDE
    override = os.environ.get("ONIONMIND_REDUCE_MOTION", "").strip().lower()
    if override in {"1", "true", "yes", "on"}:
        return False
    if os.name != "nt":
        return not (_SYSTEM_MOTION_PROBE and _system_reduces_motion())
    try:
        import ctypes

        enabled = ctypes.c_int(1)
        # SPI_GETCLIENTAREAANIMATION follows Windows' Animation effects setting.
        if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0):
            return bool(enabled.value)
    except (AttributeError, OSError):
        pass
    return True


def _apply_native_dark_title_bar(window: Any) -> None:
    """Pin the Windows title bar to dark regardless of the system scheme.

    The workbench is dark by design on every platform, so on a light-mode
    Windows install the native frame would be the one bright surface in the
    room. DWMWA_USE_IMMERSIVE_DARK_MODE (attribute 20; 19 on pre-2004 builds)
    matches it to the body. Purely cosmetic: every failure path is silent and
    the app runs identically without it.
    """
    if os.name != "nt":
        return
    try:
        import ctypes

        for attribute in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                int(window.winId()), attribute, ctypes.byref(ctypes.c_int(1)), 4
            ) == 0:
                return
    except (AttributeError, OSError, TypeError, ValueError):
        pass


class ThinkingDots(QWidget):
    """A tiny, low-cost progress cue; adjacent text carries the meaning."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._frame = 0
        self.setFixedSize(34, 16)
        self.setAccessibleName("Thinking progress")

    def advance(self) -> None:
        self._frame = (self._frame + 1) % 3
        self.update()

    def reset(self) -> None:
        self._frame = 0
        self.update()

    def paintEvent(self, event: Any) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        for index, x in enumerate((6, 17, 28)):
            active = index == self._frame
            tokens = theme_tokens()
            painter.setBrush(QColor(tokens["accent"] if active else tokens["line_strong"]))
            radius = 3.0 if active else 2.5
            painter.drawEllipse(QPointF(float(x), 8.0), radius, radius)


class ThinkingIndicator(QWidget):
    """Accessible pending state that becomes static when motion is reduced."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._running = False
        self._motion_enabled = _ui_animations_enabled()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 1, 0, 2)
        layout.setSpacing(6)
        self.label = QLabel("Thinking")
        self.label.setObjectName("thinkingLabel")
        self.dots = ThinkingDots(self)
        layout.addWidget(self.label)
        layout.addWidget(self.dots)
        layout.addStretch(1)
        self.timer = QTimer(self)
        self.timer.setInterval(280)
        self.timer.timeout.connect(self.dots.advance)
        app = QApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(self._application_state_changed)
        self.setAccessibleName("Onionmind is thinking")
        self.setAccessibleDescription("A local response is pending")
        self.hide()

    def start(self, text: str = "Thinking") -> None:
        self._running = True
        self.set_label(text)
        self.dots.reset()
        self.show()
        self._sync_timer()

    def stop(self) -> None:
        self._running = False
        self.timer.stop()
        self.hide()

    def set_label(self, text: str) -> None:
        label = text.strip() or "Thinking"
        self.label.setText(label)
        self.setAccessibleName(f"Onionmind is {label.lower()}")

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        self._sync_timer()

    def hideEvent(self, event: Any) -> None:
        self.timer.stop()
        super().hideEvent(event)

    def _application_state_changed(self, state: Qt.ApplicationState) -> None:
        del state
        self._sync_timer()

    def _sync_timer(self) -> None:
        app = QApplication.instance()
        active = app is None or app.applicationState() == Qt.ApplicationState.ApplicationActive
        should_run = self._running and self._motion_enabled and self.isVisible() and active
        if should_run:
            self.timer.start()
        else:
            self.timer.stop()


class ComposerEdit(QTextEdit):
    sendRequested = Signal()
    filesDropped = Signal(list)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("composer")
        self.setAcceptDrops(True)
        self.setAccessibleName("Task composer")
        self.setTabChangesFocus(True)
        # A workbench preference: Enter sends (Shift+Enter is always a newline),
        # or Ctrl+Enter sends when Enter-to-send is turned off.
        self.enterSends = True

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            control = bool(
                event.modifiers()
                & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier)
            )
            if not shift and (self.enterSends or control):
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
        self._author_name = who.text()
        self.body.setAccessibleName(f"{self._author_name} message")
        self.thinking = ThinkingIndicator(self)
        column.addWidget(self.thinking)
        column.addWidget(self.body)
        # v2 msgActions: built on demand, shown only while the feature is on.
        self.message_index: Optional[int] = None
        self.actions_row: Optional[QWidget] = None
        self._column = column
        outer.addLayout(column, 1)

    def set_actions(self, actions: list[tuple[str, str, Callable[[], None]]]) -> None:
        """(label, icon, callback) buttons under the message; [] hides them."""
        if self.actions_row is not None:
            self._column.removeWidget(self.actions_row)
            self.actions_row.deleteLater()
            self.actions_row = None
        if not actions:
            self._sync_body_height()
            return
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        for label, icon, callback in actions:
            button = QPushButton(label)
            button.setObjectName("chipButton")
            button.setIcon(_icon(icon, 14))
            button.setAccessibleName(f"{label} this message")
            button.clicked.connect(lambda _checked=False, run=callback: run())
            layout.addWidget(button)
        layout.addStretch(1)
        self._column.addWidget(row)
        self.actions_row = row
        self.updateGeometry()

    @property
    def text(self) -> str:
        return self._text

    def set_text(self, text: str) -> None:
        self.stop_thinking()
        self._text = text
        self.body.setText(text)
        self._sync_body_height()

    def append_text(self, text: str) -> None:
        if text:
            self.stop_thinking()
        self._text += text
        self.body.setText(self._text)
        self._sync_body_height()

    def start_thinking(self, text: str = "Thinking") -> None:
        self._text = ""
        self.body.clear()
        self.body.hide()
        self.thinking.start(text)
        self.setAccessibleName(self.thinking.accessibleName())
        self.updateGeometry()

    def set_pending_label(self, text: str) -> None:
        if self.thinking._running:
            self.thinking.set_label(text)
            self.setAccessibleName(self.thinking.accessibleName())

    def stop_thinking(self) -> None:
        if self.thinking._running or not self.thinking.isHidden():
            self.thinking.stop()
            self.body.show()
            self.setAccessibleName(f"{self._author_name} message")
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

    def remove_message(self, block: Optional["MessageBlock"]) -> None:
        if block is None:
            return
        if block in self._message_blocks:
            self._message_blocks.remove(block)
        self.layout.removeWidget(block)
        block.hide()
        block.deleteLater()

    def add_widget(self, widget: QWidget, indent: int = 44) -> QWidget:
        """A v2 card (webAsk, approval, provenance) aligned with message copy."""
        holder = QWidget()
        holder_layout = QHBoxLayout(holder)
        holder_layout.setContentsMargins(indent, 0, 0, 12)
        holder_layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignLeft)
        holder_layout.addStretch(1)
        widget.setMaximumWidth(max(320, self.viewport().width() - indent - 96))
        self.layout.addWidget(holder)
        self._scroll_later()
        return holder

    def remove_widget(self, holder: Optional[QWidget]) -> None:
        if holder is None:
            return
        self.layout.removeWidget(holder)
        holder.hide()
        holder.deleteLater()

    def add_timeline_step(self, title: str, detail: str, state: str) -> "TimelineStep":
        """Append one step to the timeline that follows the latest message."""
        last = self.layout.itemAt(self.layout.count() - 1) if self.layout.count() else None
        holder = last.widget() if last is not None else None
        timeline = getattr(holder, "timeline", None)
        if not isinstance(timeline, TimelineView):
            timeline = TimelineView()
            holder = QWidget()
            holder.timeline = timeline
            holder_layout = QHBoxLayout(holder)
            holder_layout.setContentsMargins(44, 0, 0, 12)
            holder_layout.addWidget(timeline, 0, Qt.AlignmentFlag.AlignLeft)
            holder_layout.addStretch(1)
            self.layout.addWidget(holder)
        step = timeline.add_step(title, detail, state)
        self._scroll_later()
        return step

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


class TimelineStep(QWidget):
    """One step: a 9px node on the 1px line, a title and a detail.

    While a step runs its node is an arc spinner (still when motion is
    reduced); finished steps are filled, failed or refused ones hollow red.
    """

    def __init__(self, title: str, detail: str, state: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._state = state
        self._angle = 0
        layout = QHBoxLayout(self)
        layout.setContentsMargins(22, 3, 0, 3)
        layout.setSpacing(8)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("title")
        self.detail_label = QLabel(detail)
        self.detail_label.setObjectName("meta")
        self.detail_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.title_label)
        layout.addWidget(self.detail_label, 1)
        self.timer = QTimer(self)
        self.timer.setInterval(90)
        self.timer.timeout.connect(self._spin)
        self.set_state(state, detail)

    @property
    def state(self) -> str:
        return self._state

    def set_state(self, state: str, detail: Optional[str] = None) -> None:
        self._state = state
        if detail is not None:
            self.detail_label.setText(detail)
        self.setAccessibleName(f"{self.title_label.text()}: {self.detail_label.text()} ({state})")
        if state == "running" and _ui_animations_enabled():
            self.timer.start()
        else:
            self.timer.stop()
        self.update()

    def _spin(self) -> None:
        self._angle = (self._angle + 30) % 360
        self.update()

    def paintEvent(self, event: Any) -> None:
        super().paintEvent(event)
        tokens = theme_tokens()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(tokens["line_strong"]), 1))
        painter.drawLine(QPointF(8.5, 0), QPointF(8.5, self.height()))
        centre = QPointF(8.5, self.height() / 2)
        rect = QRectF(centre.x() - 4.5, centre.y() - 4.5, 9, 9)
        colour = {"done": tokens["accent"], "error": tokens["bad"], "denied": tokens["bad"],
                  "waiting": tokens["warn"]}.get(self._state, tokens["faint"])
        if self._state == "running":
            painter.setBrush(QColor(tokens["bg"]))
            painter.setPen(QPen(QColor(tokens["line_strong"]), 1.4))
            painter.drawEllipse(rect)
            pen = QPen(QColor(tokens["accent"]), 1.6)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawArc(rect, (90 - self._angle) * 16, 120 * 16)
        elif self._state in ("error", "denied", "waiting"):
            painter.setBrush(QColor(tokens["bg"]))
            painter.setPen(QPen(QColor(colour), 1.6))
            painter.drawEllipse(rect)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(colour))
            painter.drawEllipse(rect)
        painter.end()


class TimelineView(QWidget):
    """Agent and tool steps as a compact vertical timeline instead of cards."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("timeline")
        self.setAccessibleName("Steps")
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(0)
        self.steps: list[TimelineStep] = []
        self.setMinimumWidth(420)

    def add_step(self, title: str, detail: str, state: str) -> TimelineStep:
        step = TimelineStep(title, detail, state, self)
        self.steps.append(step)
        self.layout_.addWidget(step)
        return step


class NoticeCard(QFrame):
    """A titled card with copy and action buttons: amber for questions
    (webAsk, approvals), red for errors. Buttons are real QPushButtons, so
    every action is reachable from the keyboard."""

    def __init__(self, kind: str, title: str, body: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("errorCard" if kind == "error" else "amberCard")
        self.setAccessibleName(title)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 11, 14, 11)
        layout.setSpacing(7)
        head = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(_icon("warning", 16, theme_tokens()["bad" if kind == "error" else "warn"]).pixmap(16, 16))
        head.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("title")
        self.title_label.setWordWrap(True)
        head.addWidget(self.title_label, 1)
        layout.addLayout(head)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(4)
        layout.addLayout(self.rows)
        self.body_label = QLabel(body)
        self.body_label.setObjectName("meta")
        self.body_label.setWordWrap(True)
        self.body_label.setVisible(bool(body))
        layout.addWidget(self.body_label)
        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(7)
        self.buttons.addStretch(1)
        layout.addLayout(self.buttons)
        self.spinner_label = QLabel()
        self.spinner_label.setObjectName("meta")
        self.spinner_label.hide()
        layout.addWidget(self.spinner_label)

    def add_row(self, label: str, value: str) -> QLabel:
        row = QHBoxLayout()
        name = QLabel(label)
        name.setObjectName("meta")
        name.setFixedWidth(60)
        text = QLabel(value)
        text.setWordWrap(True)
        text.setTextFormat(Qt.TextFormat.PlainText)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(name, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(text, 1)
        self.rows.addLayout(row)
        return text

    def add_button(self, text: str, callback: Callable[[], None], primary: bool = False) -> QPushButton:
        button = QPushButton(text)
        if primary:
            button.setObjectName("primaryButton")
        button.setAccessibleName(text)
        button.clicked.connect(callback)
        self.buttons.addWidget(button)
        return button

    def set_busy(self, text: str) -> None:
        for index in range(self.buttons.count()):
            widget = self.buttons.itemAt(index).widget()
            if widget is not None:
                widget.setEnabled(False)
        self.spinner_label.setText(text)
        self.spinner_label.show()


class ThoughtLine(QWidget):
    """'Thought for Ns' under a reply; it expands to say why that is all you see."""

    EXPLAIN = (
        "The model’s reasoning stays private. Onionmind removes it before the reply is shown, "
        "so only how long it took is kept."
    )

    def __init__(self, seconds: float, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        whole = max(1, round(seconds))
        self.toggle = QPushButton(f"Thought for {whole}s")
        self.toggle.setObjectName("chipButton")
        self.toggle.setCheckable(True)
        self.toggle.setAccessibleName(f"Thought for {whole} seconds. Show what this means")
        self.detail = QLabel(self.EXPLAIN)
        self.detail.setObjectName("meta")
        self.detail.setWordWrap(True)
        self.detail.hide()
        self.toggle.toggled.connect(self.detail.setVisible)
        layout.addWidget(self.toggle, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.detail)


class RunPanel(QWidget):
    """The inspector's Run tab: one Agent run's status, plan, approval and output.

    Every row comes from the agent's own stream - its todo list when it keeps
    one, otherwise the tools it actually called - never from a script.
    """

    STATES = {
        "pending": "Pending", "running": "Working…", "waiting": "Waiting for you",
        "done": "Done", "stopped": "Stopped by you", "denied": "Denied · nothing written",
        "error": "Failed",
    }
    EMPTY = (
        "No agent run yet. Switch the composer to Agent to start one. Its plan, "
        "approvals and output show up here."
    )

    stopRequested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 10, 2, 3)
        layout.setSpacing(8)
        head = QHBoxLayout()
        self.status_label = QLabel("No run")
        self.status_label.setObjectName("title")
        self.elapsed_label = QLabel("")
        self.elapsed_label.setObjectName("meta")
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("dangerButton")
        self.stop_button.setAccessibleName("Stop the Agent run")
        self.stop_button.clicked.connect(self.stopRequested)
        self.stop_button.setEnabled(False)
        head.addWidget(self.status_label)
        head.addWidget(self.elapsed_label)
        head.addStretch(1)
        head.addWidget(self.stop_button)
        layout.addLayout(head)
        self.meta_label = QLabel("")
        self.meta_label.setObjectName("meta")
        self.meta_label.setWordWrap(True)
        layout.addWidget(self.meta_label)
        self.empty_label = QLabel(self.EMPTY)
        self.empty_label.setObjectName("meta")
        self.empty_label.setWordWrap(True)
        layout.addWidget(self.empty_label)
        plan_title = QLabel("PLAN")
        plan_title.setObjectName("sectionTitle")
        layout.addWidget(plan_title)
        self.plan = QListWidget()
        self.plan.setAccessibleName("Agent plan")
        self.plan.setMaximumHeight(190)
        layout.addWidget(self.plan)
        self.approval_holder = QVBoxLayout()
        layout.addLayout(self.approval_holder)
        output_title = QLabel("OUTPUT")
        output_title.setObjectName("sectionTitle")
        layout.addWidget(output_title)
        self.output = QPlainTextEdit()
        self.output.setObjectName("runOutput")
        self.output.setReadOnly(True)
        self.output.setAccessibleName("Agent output")
        self.output.document().setMaximumBlockCount(4000)
        layout.addWidget(self.output, 1)
        self._started: Optional[float] = None
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)
        self._rows: dict[str, QListWidgetItem] = {}
        self._todo_mode = False

    def start(self, meta: str) -> None:
        self.plan.clear()
        self._rows.clear()
        self._todo_mode = False
        self.output.clear()
        self.clear_approval()
        self.meta_label.setText(meta)
        self.empty_label.hide()
        self.status_label.setText("Running")
        self.stop_button.setEnabled(True)
        self._started = time.monotonic()
        self._tick()
        self.timer.start()

    def finish(self, status: str) -> None:
        self.status_label.setText(status)
        self.stop_button.setEnabled(False)
        self.timer.stop()
        self._tick()
        for key, item in self._rows.items():
            state = item.data(Qt.ItemDataRole.UserRole)
            if state in ("running", "waiting", "pending") and not self._todo_mode:
                self.set_step(key, "stopped", None)

    def set_status(self, status: str) -> None:
        self.status_label.setText(status)

    def _tick(self) -> None:
        if self._started is None:
            self.elapsed_label.setText("")
            return
        seconds = int(time.monotonic() - self._started)
        self.elapsed_label.setText(f"{seconds // 60}:{seconds % 60:02d}")

    def set_step(self, key: str, state: str, title: Optional[str], detail: str = "") -> None:
        if self._todo_mode and not key.startswith("todo:"):
            self.append_output(f"{title or key} · {self.STATES.get(state, state)}")
            return
        item = self._rows.get(key)
        if item is None:
            item = QListWidgetItem()
            self._rows[key] = item
            self.plan.addItem(item)
        if title is not None:
            item.setData(Qt.ItemDataRole.UserRole + 1, title)
        name = item.data(Qt.ItemDataRole.UserRole + 1) or key
        item.setData(Qt.ItemDataRole.UserRole, state)
        mark = {"done": "✓", "denied": "✕", "error": "✕", "stopped": "■", "waiting": "!",
                "running": "›", "pending": "·"}.get(state, "·")
        label = detail or self.STATES.get(state, state)
        item.setText(f"{mark}  {name}\n    {label}")
        item.setToolTip(f"{name}: {label}")

    def set_todos(self, todos: Iterable[Any]) -> None:
        """The agent's own todo list (its todo_write tool) becomes the plan."""
        self._todo_mode = True
        for key in [k for k in self._rows if k.startswith("todo:")]:
            item = self._rows.pop(key)
            self.plan.takeItem(self.plan.row(item))
        for index, todo in enumerate(todos):
            if not isinstance(todo, dict):
                continue
            status = {"completed": "done", "in_progress": "running"}.get(_as_text(todo.get("status")), "pending")
            self.set_step(f"todo:{index}", status, _as_text(todo.get("content"))[:120] or f"Step {index + 1}")

    def append_output(self, text: str) -> None:
        if text:
            self.output.appendPlainText(text.rstrip("\n"))

    def show_approval(self, card: QWidget) -> None:
        self.clear_approval()
        self.approval_holder.addWidget(card)

    def clear_approval(self) -> None:
        while self.approval_holder.count():
            widget = self.approval_holder.takeAt(0).widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()


class _PaletteSearch(QLineEdit):
    """The palette's field keeps arrows, Enter and Esc for itself, ahead of
    the window's application shortcuts (Esc there means 'stop the run')."""

    moved = Signal(int)
    chosen = Signal()
    dismissed = Signal()

    def event(self, event: Any) -> bool:
        if event.type() == event.Type.ShortcutOverride and event.key() in (
            Qt.Key.Key_Escape, Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Return, Qt.Key.Key_Enter
        ):
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            self.moved.emit(1 if key == Qt.Key.Key_Down else -1)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.chosen.emit()
        elif key == Qt.Key.Key_Escape:
            self.dismissed.emit()
        else:
            super().keyPressEvent(event)


class CommandPalette(QDialog):
    """Ctrl+K: jump to a project, session, model or command.

    Every row is something real - a remembered project, a saved session, an
    installed model, an action the window can take now. Focus goes back to
    where it was when the palette closes.
    """

    GROUPS = ("Projects", "Sessions", "Model power", "Commands")

    def __init__(self, parent: QWidget, rows: list[tuple[str, str, str, Callable[[], None]]]) -> None:
        super().__init__(parent, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setObjectName("popover")
        self.setModal(True)
        self.setAccessibleName("Command palette")
        self.rows = rows
        self._return_focus = QApplication.focusWidget()
        frame = QFrame(self)
        frame.setObjectName("popover")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(10, 10, 10, 8)
        layout.setSpacing(6)
        self.search = _PaletteSearch()
        self.search.setPlaceholderText("Jump to a project, session, model or command")
        self.search.setAccessibleName("Search projects, sessions, models and commands")
        self.search.textChanged.connect(self._filter)
        self.search.moved.connect(self._move)
        self.search.chosen.connect(self._choose)
        self.search.dismissed.connect(self.reject)
        layout.addWidget(self.search)
        self.list = QListWidget()
        self.list.setAccessibleName("Matches")
        self.list.itemActivated.connect(lambda item: self._choose())
        self.list.itemClicked.connect(lambda item: self._choose())
        layout.addWidget(self.list, 1)
        self.empty = QLabel("Nothing matches that.")
        self.empty.setObjectName("meta")
        self.empty.hide()
        layout.addWidget(self.empty)
        footer = QHBoxLayout()
        hint = QLabel("↑↓ to move · Enter to open")
        hint.setObjectName("meta")
        esc = QLabel("esc")
        esc.setObjectName("kbd")
        footer.addWidget(hint)
        footer.addStretch(1)
        footer.addWidget(esc)
        layout.addLayout(footer)
        self.resize(600, 420)
        self._filter("")

    def place(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        top_left = parent.mapToGlobal(parent.rect().topLeft())
        width = min(600, max(360, parent.width() - 40))
        self.resize(width, min(440, max(260, parent.height() - 120)))
        self.move(top_left.x() + (parent.width() - width) // 2, top_left.y() + 72)

    def _filter(self, text: str) -> None:
        needle = text.strip().casefold()
        self.list.clear()
        shown = 0
        for group in self.GROUPS:
            matches = [row for row in self.rows if row[0] == group and (
                not needle or needle in row[1].casefold() or needle in row[2].casefold())]
            if not matches:
                continue
            header = QListWidgetItem(group.upper())
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(header)
            for row in matches:
                item = QListWidgetItem(f"{row[1]}    {row[2]}".rstrip())
                item.setData(Qt.ItemDataRole.UserRole, self.rows.index(row))
                item.setToolTip(row[2])
                self.list.addItem(item)
                shown += 1
        self.empty.setVisible(shown == 0)
        self._move(0)

    def _selectable(self) -> list[int]:
        return [i for i in range(self.list.count()) if self.list.item(i).flags() & Qt.ItemFlag.ItemIsEnabled]

    def _move(self, step: int) -> None:
        rows = self._selectable()
        if not rows:
            return
        current = self.list.currentRow()
        if current not in rows or step == 0:
            target = rows[0]
        else:
            position = rows.index(current) + step
            target = rows[max(0, min(len(rows) - 1, position))]
        self.list.setCurrentRow(target)

    def _choose(self) -> None:
        item = self.list.currentItem()
        if item is None or not (item.flags() & Qt.ItemFlag.ItemIsEnabled):
            return
        action = self.rows[int(item.data(Qt.ItemDataRole.UserRole))][3]
        self.accept()
        QTimer.singleShot(0, action)

    def done(self, result: int) -> None:
        super().done(result)
        target = self._return_focus
        if target is not None:
            try:
                owner = target.window()
                owner.activateWindow()
                target.setFocus(Qt.FocusReason.PopupFocusReason)
            except RuntimeError:
                pass


class BoundaryButton(QPushButton):
    """cpu - line - shield - line - globe, then 'N searches today'.

    The lines take the accent while a Tor path is open; a dot travels along
    them while a search is actually running (still when motion is reduced).
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("boundaryButton")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setCheckable(True)
        self._open = False
        self._searching = False
        self._phase = 0.0
        self._label = "Nothing left today"
        self._show_label = True
        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self._advance)
        self.setMinimumHeight(34)
        self._sync()

    def set_state(self, open_path: bool, searching: bool, label: str) -> None:
        self._open, self._searching, self._label = open_path, searching, label
        if searching and _ui_animations_enabled():
            self.timer.start()
        else:
            self.timer.stop()
        self._sync()

    def set_label_visible(self, visible: bool) -> None:
        self._show_label = visible
        self._sync()

    def _sync(self) -> None:
        text = self._label if self._show_label else ""
        self.setFixedWidth(118 + (self.fontMetrics().horizontalAdvance(text) + 30 if text else 0))
        path = "open over Tor" if self._open else "closed: nothing can leave over Tor"
        self.setAccessibleName(f"Boundary map. The path out is {path}. {self._label}")
        self.setToolTip("Boundary map (Ctrl+B): what stays on this machine and what leaves")
        self.update()

    def _advance(self) -> None:
        self._phase = (self._phase + 0.04) % 1.0
        self.update()

    def paintEvent(self, event: Any) -> None:
        super().paintEvent(event)
        tokens = theme_tokens()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        y = self.height() / 2
        icons = ((10, "cpu"), (52, "shield"), (94, "globe"))
        for x, name in icons:
            painter.drawPixmap(int(x), int(y - 8), _icon(name, 16).pixmap(16, 16))
        colour = QColor(tokens["accent"] if self._open else tokens["line_strong"])
        painter.setPen(QPen(colour, 1.4))
        for start in (30, 72):
            painter.drawLine(QPointF(start, y), QPointF(start + 18, y))
        if self._searching:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(tokens["accent"]))
            for start in (30, 72):
                painter.drawEllipse(QPointF(start + 18 * self._phase, y), 2.5, 2.5)
        if self._show_label:
            painter.setPen(QColor(tokens["muted"]))
            painter.drawText(QRectF(118, 0, self.width() - 122, self.height()),
                             int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), self._label)
        painter.end()


class BoundaryPanel(QFrame):
    """Ctrl+B: what stays on this machine, the Tor node, and what leaves it."""

    FOOTNOTE = (
        "Commands that ignore proxy settings, such as ping or nslookup, can still reach the network "
        "directly. For a fully closed machine, use Matchstick, a container or an OS egress rule."
    )

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("boundaryPanel")
        self.setAccessibleName("Boundary map")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 12, 22, 12)
        outer.setSpacing(10)
        head = QHBoxLayout()
        title = QLabel("Boundary map")
        title.setObjectName("title")
        self.receipt = QLabel("")
        self.receipt.setObjectName("meta")
        self.close_button = QToolButton()
        self.close_button.setObjectName("bareButton")
        _themed_icon(self.close_button, "close")
        self.close_button.setAccessibleName("Close the boundary map")
        head.addWidget(title)
        head.addWidget(self.receipt, 1)
        head.addWidget(self.close_button)
        outer.addLayout(head)
        row = QHBoxLayout()
        row.setSpacing(12)
        self.machine = QFrame()
        self.machine.setObjectName("zoneMachine")
        self.machine_rows = self._column(self.machine, "STAYS ON THIS MACHINE",
                                         ("Model", "Project", "Disk", "Sessions"))
        self.outside = QFrame()
        self.outside.setObjectName("zoneOutside")
        self.outside_rows = self._column(self.outside, "LEAVES THIS MACHINE",
                                         ("Chat search", "Agent network", "Downloads", "Updates"))
        node = QVBoxLayout()
        node.setSpacing(4)
        logo = QLabel()
        icon_path = MODULE_DIR / "onionmind.ico"
        if icon_path.exists():
            logo.setPixmap(QPixmap(str(icon_path)).scaled(44, 44, Qt.AspectRatioMode.KeepAspectRatio,
                                                          Qt.TransformationMode.SmoothTransformation))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.node_title = QLabel("Tor")
        self.node_title.setObjectName("title")
        self.node_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.node_state = QLabel("")
        self.node_state.setObjectName("meta")
        self.node_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.node_state.setWordWrap(True)
        node.addStretch(1)
        node.addWidget(logo)
        node.addWidget(self.node_title)
        node.addWidget(self.node_state)
        node.addStretch(1)
        node_holder = QWidget()
        node_holder.setLayout(node)
        node_holder.setFixedWidth(170)
        row.addWidget(self.machine, 1)
        row.addWidget(node_holder)
        row.addWidget(self.outside, 1)
        outer.addLayout(row)
        foot = QLabel(self.FOOTNOTE)
        foot.setObjectName("meta")
        foot.setWordWrap(True)
        outer.addWidget(foot)
        self.hide()

    @staticmethod
    def _column(frame: QFrame, title: str, names: Iterable[str]) -> dict[str, QLabel]:
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 11, 14, 11)
        layout.setSpacing(6)
        heading = QLabel(title)
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        values: dict[str, QLabel] = {}
        for name in names:
            line = QHBoxLayout()
            label = QLabel(name)
            label.setObjectName("meta")
            label.setFixedWidth(100)
            value = QLabel("")
            value.setWordWrap(True)
            line.addWidget(label, 0, Qt.AlignmentFlag.AlignTop)
            line.addWidget(value, 1)
            layout.addLayout(line)
            values[name] = value
        return values


class HunkDialog(QDialog):
    """Pick which hunks of one file go back to the last commit; the rest stay."""

    PREVIEW_LINES = 14

    def __init__(self, path: str, hunks: list[Any], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Revert parts of {path}")
        self.resize(640, 480)
        layout = QVBoxLayout(self)
        heading = QLabel(f"Revert parts of {path}")
        heading.setObjectName("brand")
        intro = QLabel("Tick the changes to put back to the last commit. Unticked changes stay on "
                       "disk. Undo restores the whole file as it is now.")
        intro.setObjectName("meta")
        intro.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(intro)
        self.list = QListWidget()
        self.list.setAccessibleName(f"Changes in {path}")
        self.list.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        for hunk in hunks:
            lines = [line.rstrip("\n") for line in _field(hunk, "lines", ())]
            shown = lines[: self.PREVIEW_LINES]
            if len(lines) > len(shown):
                shown.append(f"… {len(lines) - len(shown)} more line(s)")
            added, removed = _field(hunk, "added", 0), _field(hunk, "removed", 0)
            item = QListWidgetItem(f"{_field(hunk, 'header', '')}  +{added} / −{removed}\n" + "\n".join(shown))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, int(_field(hunk, "index", 0)))
            self.list.addItem(item)
        self.list.itemChanged.connect(lambda _item: self._sync())
        layout.addWidget(self.list, 1)
        buttons = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.revert_button = QPushButton("Revert selected")
        self.revert_button.setObjectName("primaryButton")
        self.revert_button.clicked.connect(self.accept)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(self.revert_button)
        layout.addLayout(buttons)
        self._sync()

    def selected(self) -> list[int]:
        return [int(self.list.item(i).data(Qt.ItemDataRole.UserRole)) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.CheckState.Checked]

    def _sync(self) -> None:
        count = len(self.selected())
        self.revert_button.setEnabled(count > 0)
        self.revert_button.setText(f"Revert {count} change(s)" if count else "Revert selected")


class NetworkLogDialog(QDialog):
    """Every connection Onionmind made from this machine today, with exports.

    Rows are times, routes and destinations only - the log never held a
    prompt or a query, so an export cannot leak one.
    """

    INTRO = (
        "Every connection Onionmind made from this machine today. Inference is not listed because "
        "it never leaves the computer. The exported file contains times and destinations only, "
        "not your prompts."
    )

    def __init__(self, rows: list[dict[str, str]], desktop_core: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem

        self.rows = rows
        self.desktop_core = desktop_core
        self.saved_path: Optional[str] = None
        self.setWindowTitle("Network log")
        self.resize(640, 460)
        layout = QVBoxLayout(self)
        heading = QLabel("Network log")
        heading.setObjectName("brand")
        intro = QLabel(self.INTRO)
        intro.setObjectName("meta")
        intro.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(intro)
        self.table = QTableWidget(len(rows), 3)
        self.table.setHorizontalHeaderLabels(["Time", "Route", "What"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setAccessibleName("Connections that left this machine today")
        for index, row in enumerate(rows):
            stamp = _as_text(row.get("time"))
            shown = stamp[11:19] if len(stamp) >= 19 else stamp
            for column, value in enumerate((shown, row.get("route", ""), row.get("what", ""))):
                self.table.setItem(index, column, QTableWidgetItem(_as_text(value)))
        layout.addWidget(self.table, 1)
        self.empty = QLabel("No connections left this machine today.")
        self.empty.setObjectName("meta")
        self.empty.setVisible(not rows)
        layout.addWidget(self.empty)
        self.feedback = QLabel("")
        self.feedback.setObjectName("meta")
        layout.addWidget(self.feedback)
        buttons = QHBoxLayout()
        self.json_button = QPushButton("Save as JSON")
        self.csv_button = QPushButton("Save as CSV")
        close = QPushButton("Close")
        self.json_button.clicked.connect(lambda: self.save("json"))
        self.csv_button.clicked.connect(lambda: self.save("csv"))
        close.clicked.connect(self.accept)
        buttons.addWidget(self.json_button)
        buttons.addWidget(self.csv_button)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    def default_path(self, kind: str) -> str:
        documents = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        folder = Path(documents or Path.home()) / "Onionmind"
        return str(folder / f"network-log-{datetime.now().strftime('%Y-%m-%d')}.{kind}")

    def save(self, kind: str, path: Optional[str] = None) -> Optional[str]:
        if path is None:
            default = self.default_path(kind)
            path, _ = QFileDialog.getSaveFileName(
                self, "Save network log", default,
                "JSON (*.json)" if kind == "json" else "CSV (*.csv)")
            if not path:
                return None
        writer = getattr(self.desktop_core, "network_log_json" if kind == "json" else "network_log_csv", None)
        if not callable(writer):
            self.feedback.setText("This build cannot export the network log.")
            return None
        target = Path(path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(writer(self.rows), encoding="utf-8")
        except OSError as exc:
            self.feedback.setText(f"Could not save the network log: {exc}")
            return None
        self.saved_path = str(target)
        self.feedback.setText(f"Saved {target} · {len(self.rows)} entries")
        return self.saved_path


class ProvenanceChips(QWidget):
    """Where a reply came from: always this machine, plus any Tor search."""

    def __init__(self, chips: list[tuple[str, str]], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        for text, tooltip in chips:
            chip = QLabel(text)
            chip.setObjectName("kbd")
            chip.setToolTip(tooltip)
            chip.setAccessibleName(text)
            layout.addWidget(chip)
        layout.addStretch(1)


class _Overlay(QWidget):
    """A full-window layer over the workbench (setup, lock)."""

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self.setObjectName("overlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.hide()

    def cover(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())
        self.raise_()
        self.show()


class LockOverlay(_Overlay):
    """'Onionmind is locked': the workbench stays hidden until the passphrase."""

    unlocked = Signal()

    def __init__(self, window: QWidget, verify: Callable[[str], bool]) -> None:
        super().__init__(window)
        self.verify = verify
        outer = QVBoxLayout(self)
        outer.addStretch(1)
        box = QFrame()
        box.setObjectName("card")
        box.setFixedWidth(380)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(10)
        title = QLabel("Onionmind is locked")
        title.setObjectName("brand")
        copy_label = QLabel("Conversations and projects are hidden until you unlock. "
                            "Runs that were in progress keep going.")
        copy_label.setObjectName("meta")
        copy_label.setWordWrap(True)
        self.passphrase = QLineEdit()
        self.passphrase.setEchoMode(QLineEdit.EchoMode.Password)
        self.passphrase.setPlaceholderText("Passphrase")
        self.passphrase.setAccessibleName("Passphrase")
        self.passphrase.returnPressed.connect(self.try_unlock)
        self.error = QLabel("")
        self.error.setObjectName("danger")
        self.error.hide()
        self.unlock_button = QPushButton("Unlock")
        self.unlock_button.setObjectName("primaryButton")
        self.unlock_button.clicked.connect(self.try_unlock)
        for widget in (title, copy_label, self.passphrase, self.error, self.unlock_button):
            layout.addWidget(widget)
        outer.addWidget(box, 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addStretch(2)

    def lock(self) -> None:
        self.passphrase.clear()
        self.error.hide()
        self.cover()
        self.passphrase.setFocus(Qt.FocusReason.OtherFocusReason)

    def try_unlock(self) -> None:
        text = self.passphrase.text()
        if not text:
            self.error.setText("Enter your passphrase to unlock.")
            self.error.show()
            return
        if not self.verify(text):
            self.error.setText("That passphrase is not right.")
            self.error.show()
            self.passphrase.selectAll()
            return
        self.passphrase.clear()
        self.hide()
        self.unlocked.emit()


class SetupOverlay(_Overlay):
    """First-launch setup: Welcome, Check this machine, Choose a model, Tor,
    Open a project. Every check and action is the real one the app uses."""

    STEPS = ("Welcome", "Check this machine", "Choose a model", "Tor", "Open a project")

    def __init__(self, window: "OnionmindWindow") -> None:
        super().__init__(window)
        self.window_ = window
        self.step = 0
        self.checks: dict[str, tuple[str, str]] = {}
        self.project: Optional[str] = None
        self.chosen_model: Optional[str] = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch(1)
        box = QFrame()
        box.setObjectName("card")
        box.setFixedWidth(560)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(26, 22, 26, 20)
        layout.setSpacing(12)
        self.progress_label = QLabel("")
        self.progress_label.setObjectName("meta")
        self.title = QLabel("")
        self.title.setObjectName("brand")
        self.body = QLabel("")
        self.body.setObjectName("meta")
        self.body.setWordWrap(True)
        layout.addWidget(self.progress_label)
        layout.addWidget(self.title)
        layout.addWidget(self.body)
        self.content = QVBoxLayout()
        self.content.setSpacing(6)
        layout.addLayout(self.content)
        nav = QHBoxLayout()
        self.skip_button = QPushButton("Skip setup")
        self.skip_button.clicked.connect(self.finish)
        self.back_button = QPushButton("Back")
        self.back_button.clicked.connect(lambda: self.go(self.step - 1))
        self.next_button = QPushButton("Get started")
        self.next_button.setObjectName("primaryButton")
        self.next_button.clicked.connect(self.next_step)
        nav.addWidget(self.skip_button)
        nav.addStretch(1)
        nav.addWidget(self.back_button)
        nav.addWidget(self.next_button)
        layout.addLayout(nav)
        outer.addWidget(box, 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addStretch(2)
        self.tor_timer = QTimer(self)
        self.tor_timer.setInterval(400)
        self.tor_timer.timeout.connect(self._sync_tor)

    def open(self) -> None:
        self.go(0)
        self.cover()
        self.next_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def _clear(self) -> None:
        while self.content.count():
            item = self.content.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()

    def go(self, step: int) -> None:
        self.step = max(0, min(len(self.STEPS) - 1, step))
        self._clear()
        self.tor_timer.stop()
        self.progress_label.setText(f"Step {self.step + 1} of {len(self.STEPS)} · {self.STEPS[self.step]}")
        self.back_button.setVisible(self.step > 0)
        getattr(self, f"_step_{self.step}")()

    def next_step(self) -> None:
        if self.step == len(self.STEPS) - 1:
            if self.project:
                self.window_.select_workspace(self.project)
            self.finish()
            return
        self.go(self.step + 1)

    def finish(self) -> None:
        self.tor_timer.stop()
        self.hide()
        self.window_.settings_data["setup_done"] = True
        if not self.window_.demo:
            self.window_.settings_bridge.save(self.window_.settings_data)
        self.window_.set_status("Setup finished. Onionmind is ready.")

    def _step_0(self) -> None:
        self.title.setText("Welcome to Onionmind")
        self.body.setText("A private workbench: the model runs on this machine, and anything that has to leave "
                          "it goes over Tor. Setup checks this machine, picks a model, turns on Tor if you want "
                          "it, and opens a project. You can skip any step and run setup again from Settings.")
        self.next_button.setText("Get started")

    def _step_1(self) -> None:
        self.title.setText("Check this machine")
        self.body.setText("Checking what is already here…")
        self.next_button.setText("Continue")
        self.check_rows: dict[str, QLabel] = {}
        for name in ("Local model server", "Graphics memory", "Agent runtime", "Tor"):
            row = QLabel(f"{name}    checking…")
            row.setAccessibleName(f"{name}: checking")
            self.content.addWidget(row)
            self.check_rows[name] = row
        window = self.window_
        core, desktop_core = window.core, window.desktop_core

        def job(signals: WorkerSignals) -> dict[str, tuple[str, str]]:
            results: dict[str, tuple[str, str]] = {}
            try:
                detector = getattr(core, "detect_backend", None)
                if callable(detector):
                    detector()
                results["Local model server"] = ("ok", "Found · ready")
            except BaseException:
                results["Local model server"] = ("warn", "Not running. Start it, then check again.")
            signals.event.emit(dict(results))
            memory = getattr(desktop_core, "gpu_memory_mb", None)
            name = getattr(desktop_core, "gpu_name", None)
            ram = getattr(desktop_core, "machine_memory_mb", None)
            _used, total = memory() if callable(memory) else (None, None)
            if total:
                gpu = name() if callable(name) else None
                results["Graphics memory"] = ("ok", f"{total / 1024:.0f} GB GPU" + (f" · {gpu}" if gpu else ""))
            else:
                ram_mb = ram() if callable(ram) else None
                results["Graphics memory"] = ("info", "No GPU reported" + (f" · {ram_mb / 1024:.0f} GB RAM" if ram_mb else ""))
            signals.event.emit(dict(results))
            version_of = getattr(desktop_core, "node_version", None)
            supported = getattr(desktop_core, "node_supports_agent", None)
            version = version_of() if callable(version_of) else None
            if version and callable(supported) and supported(version):
                results["Agent runtime"] = ("ok", f"Node.js {version.lstrip('v')} found")
            elif version:
                results["Agent runtime"] = ("warn", f"Node.js {version.lstrip('v')} is too old. Agent needs 22.19 or newer.")
            else:
                results["Agent runtime"] = ("warn", "Node.js not found. Agent stays off until it’s installed.")
            signals.event.emit(dict(results))
            probe = getattr(core, "tor_proxy_port", None)
            port = probe() if callable(probe) else None
            results["Tor"] = ("ok", "Running") if port else ("info", "Not running yet. That’s fine.")
            return results

        def show(results: dict[str, tuple[str, str]]) -> None:
            if self.step != 1:
                return
            self.checks.update(results)
            mark = {"ok": "✓", "warn": "!", "info": "·"}
            for key, (state, text) in results.items():
                row = self.check_rows.get(key)
                if row is not None:
                    row.setText(f"{mark.get(state, '·')}  {key}    {text}")
                    row.setAccessibleName(f"{key}: {text}")
            if len(results) == 4:
                agent_ok = results["Agent runtime"][0] == "ok"
                server_ok = results["Local model server"][0] == "ok"
                self.body.setText("Everything Onionmind needs is on this machine." if agent_ok and server_ok else
                                  "Chat works now. Install Node.js 22.19 or newer to turn on Agent." if server_ok else
                                  "Start the local model server, then come back to this step.")

        worker = window._start_worker(job)
        worker.signals.event.connect(show)
        worker.signals.result.connect(show)

    def _step_2(self) -> None:
        window = self.window_
        self.title.setText("Choose a model")
        models = [m for m in window.installed_model_ids if m]
        self.next_button.setText("Continue")
        self.model_group = QButtonGroup(self)
        fit = getattr(window.desktop_core, "model_fit", None)
        power = window._model_power
        recommended = None
        sizes = power.get("sizes") or {}
        if callable(fit):
            fitting = [m for m in models if fit(sizes.get(m), power.get("vram_total"), power.get("ram")) == "Fits"]
            recommended = max(fitting, key=lambda m: sizes.get(m) or 0) if fitting else None
        if models:
            self.body.setText("These models are already on this machine. Pick the one Onionmind should use.")
            for model in models:
                label = f"{window._tier_name(model)} · {window._describe_model(model)}"
                if model == recommended:
                    label += "  · Recommended"
                button = QCheckBox(label)
                button.setAutoExclusive(True)
                button.setChecked(model == (recommended or window.current_model_id()))
                button.toggled.connect(lambda on, m=model: on and self._pick_model(m))
                self.model_group.addButton(button)
                self.content.addWidget(button)
        else:
            self.body.setText("No model is installed yet. Name one to download; the download goes directly to "
                              "its registry, not over Tor, and starts only after you confirm.")
        row = QHBoxLayout()
        self.model_name = QLineEdit()
        self.model_name.setPlaceholderText("BLAZE, llama3.2:3b, or a huggingface.co link")
        self.model_name.setAccessibleName("Model to download")
        self.download_button = QPushButton("Download")
        self.download_button.clicked.connect(self._download)
        holder = QWidget()
        holder_layout = QHBoxLayout(holder)
        holder_layout.setContentsMargins(0, 6, 0, 0)
        holder_layout.addWidget(self.model_name, 1)
        holder_layout.addWidget(self.download_button)
        self.content.addWidget(holder)
        self.download_progress = QProgressBar()
        self.download_progress.setRange(0, 100)
        self.download_progress.hide()
        self.content.addWidget(self.download_progress)

    def _pick_model(self, model: str) -> None:
        self.chosen_model = model
        index = self.window_.model_combo.findData(model)
        if index >= 0:
            self.window_.model_combo.setCurrentIndex(index)

    def _download(self) -> None:
        raw = self.model_name.text().strip()
        if not raw:
            return
        normalize = getattr(self.window_.desktop_core, "normalize_model_reference", None)
        name = normalize(raw) if callable(normalize) else raw
        answer = QMessageBox.warning(
            self, "Direct model download",
            "The local model service will download this model directly from its configured registry. "
            "This is not Tor-routed and exposes this machine's network address. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.download_button.setEnabled(False)
        self.download_progress.setValue(0)
        self.download_progress.show()
        self.window_.pull_model(name, self)

    # The ModelManagerDialog interface pull_model reports into.
    def set_progress(self, fraction: float, status: str) -> None:
        self.download_progress.setValue(max(0, min(100, round(fraction * 100))))
        self.download_progress.setFormat(f"{_brand_runtime_text(status)} · %p%")

    def set_error(self, message: str) -> None:
        self.download_button.setEnabled(True)
        self.download_progress.setFormat(_brand_runtime_text(message)[:80])

    def set_complete(self) -> None:
        self.download_button.setEnabled(True)
        self.download_progress.setValue(100)
        self.download_progress.setFormat("Installed locally")

    def set_models(self, models: list[str], current: str = "") -> None:
        del models, current

    def _step_3(self) -> None:
        self.title.setText("Tor")
        self.body.setText("Tor is what lets a search or the agent reach the web without exposing this machine. "
                          "Chat works without it. Turning it on starts Onionmind’s own background Tor - no "
                          "browser window opens.")
        self.tor_state = QLabel("")
        self.tor_button = QPushButton("Turn on Tor")
        self.tor_button.clicked.connect(self.window_._start_tor_from_toolbar)
        self.content.addWidget(self.tor_state)
        self.content.addWidget(self.tor_button, 0, Qt.AlignmentFlag.AlignLeft)
        self._sync_tor()
        self.tor_timer.start()

    def _sync_tor(self) -> None:
        if self.step != 3:
            return
        phase = self.window_.tor_phase
        on = phase == "running"
        self.tor_state.setText({"running": "Tor is on · circuit verified", "starting": "Starting Tor and verifying a circuit…",
                                "probing": "Checking Tor…", "error": "Tor did not start. You can try again or continue without it."}
                               .get(phase, "Tor is off."))
        self.tor_button.setVisible(not on and phase not in ("starting", "probing"))
        self.next_button.setText("Continue" if on else "Continue without Tor")

    def _step_4(self) -> None:
        window = self.window_
        self.title.setText("Open a project")
        self.body.setText("Agent works inside one folder at a time. Pick one now, or later with Ctrl+O.")
        self.project_label = QLabel(window.workspace or "No folder chosen")
        self.project_label.setWordWrap(True)
        choose = QPushButton("Choose a folder…")
        choose.clicked.connect(self._choose_folder)
        self.content.addWidget(self.project_label)
        self.content.addWidget(choose, 0, Qt.AlignmentFlag.AlignLeft)
        self.project = window.workspace
        self._sync_finish()

    def _choose_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Open project folder", self.project or str(Path.home()))
        if path:
            self.set_project(path)

    def set_project(self, path: str) -> None:
        self.project = path
        self.project_label.setText(path)
        self._sync_finish()

    def _sync_finish(self) -> None:
        self.next_button.setText(f"Open {Path(self.project).name}" if self.project else "Finish")


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

    @staticmethod
    def _clean_messages(messages: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        return _sanitize_assistant_messages(messages)

    @classmethod
    def _clean_session(cls, session: Any) -> Any:
        messages = cls._clean_messages(_field(session, "messages", ()) or ())
        if dataclasses.is_dataclass(session):
            return dataclasses.replace(session, messages=messages)
        if isinstance(session, dict):
            cleaned = copy.deepcopy(session)
            cleaned["messages"] = messages
            return cleaned
        setattr(session, "messages", messages)
        return session

    def create(self, title: str, model: str, workspace: Optional[str], messages: Iterable[dict[str, Any]]) -> Any:
        clean_messages = self._clean_messages(messages)
        if self.store is not None:
            return self.store.create(title=title, model=model, workspace=workspace, messages=clean_messages)
        now = _now_iso()
        return {
            "id": uuid.uuid4().hex,
            "title": title,
            "model": model,
            "workspace": workspace,
            "messages": clean_messages,
            "created_at": now,
            "updated_at": now,
        }

    def list(self, *, archived: bool = False) -> list[Any]:
        if self.store is not None:
            try:
                return [
                    self._clean_session(item)
                    for item in self.store.list(archived=archived)
                ]
            except Exception:
                return []
        key = "archived_sessions" if archived else "sessions"
        try:
            return [
                self._clean_session(item)
                for item in json.loads(self.fallback.value(key, "[]"))
            ]
        except (TypeError, ValueError):
            return []

    def save(self, session: Any, *, title: str, model: str, workspace: Optional[str], messages: list[dict[str, Any]]) -> Any:
        clean_messages = self._clean_messages(messages)
        if self.store is not None:
            if dataclasses.is_dataclass(session):
                session = dataclasses.replace(
                    session,
                    title=title,
                    model=model,
                    workspace=workspace,
                    messages=clean_messages,
                )
            else:
                for key, value in {
                    "title": title,
                    "model": model,
                    "workspace": workspace,
                    "messages": clean_messages,
                }.items():
                    setattr(session, key, value)
            return self.store.save(session)
        payload = dict(session)
        payload.update(
            title=title,
            model=model,
            workspace=workspace,
            messages=clean_messages,
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

    def delete(self, session_id: str) -> bool:
        """Permanently remove a session instead of moving it to local archive storage."""

        if self.store is not None:
            deleter = getattr(self.store, "delete", None)
            if not callable(deleter):
                return False
            try:
                return bool(deleter(session_id))
            except Exception:
                return False

        deleted = False
        for key in ("sessions", "archived_sessions"):
            try:
                items = list(json.loads(self.fallback.value(key, "[]")))
            except (TypeError, ValueError):
                items = []
            remaining = [
                item for item in items if _as_text(_field(item, "id")) != session_id
            ]
            if len(remaining) != len(items):
                deleted = True
                self.fallback.setValue(key, json.dumps(remaining[:80]))
        return deleted


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


def _optional_callable(source: Any, name: str) -> Optional[Callable[..., Any]]:
    fn = getattr(source, name, None)
    return fn if callable(fn) else None


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
        "Onionmind Agent is an early-access local coding workflow. Approvals are on "
        "by default, and YOLO runs edits and commands without asking - which never "
        "moves the network boundary. "
        "The agent reaches the web only through Tor and does not start without it.\n\n"
        "Tor is enforced by the environment the agent runs in, not by the operating "
        "system: a compiled binary, python -S, or a tool that ignores proxies (ping, "
        "nslookup) can still reach the network directly. Closing that needs an OS "
        "egress rule - a firewall rule, a container - or the Matchstick live USB."
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

    def _launcher(self, model: str, task: str, cwd: str = "",
                  yolo: bool = False, stream: bool = False,
                  deny: Iterable[str] = (), no_network: bool = False,
                  resume: str = "") -> Optional[list[str]]:
        """``onionmind.py --agent``: the one place Tor is verified and enforced.

        Launching the agent directly would inherit this window's environment,
        which has no proxy and no socket containment - the agent would have
        direct web access whether Tor is up or not.
        """
        script = _as_text(getattr(self.core, "__file__", ""))
        if not script or not callable(getattr(self.core, "run_agent", None)):
            return None
        argv = [sys.executable, os.path.abspath(script), "--agent",
                "--model", model]
        if yolo:
            argv.append("--yolo")
        if stream:
            # v2: the structured mode. The task goes to stdin, not the argv.
            argv.append("--stream-json")
        if no_network:
            argv.append("--no-network")
        for rule in deny:
            argv += ["--deny", rule]
        if resume:
            argv += ["--resume", resume]
        if cwd:
            # The working directory is an argument, not just the process cwd:
            # run_agent writes the project settings there before it starts.
            argv += ["--cwd", cwd]
        return argv if stream else argv + [task]

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

    def build(self, *, model: str, task: str, cwd: str,
              yolo: bool = False, stream: bool = False,
              deny: Iterable[str] = (), no_network: bool = False,
              resume: str = "") -> tuple[list[str], str]:
        launcher = self._launcher(model, task, cwd, yolo, stream, deny, no_network, resume)
        if launcher is None:                     # check() refuses first; belt and braces
            raise RuntimeError(
                "Onionmind Agent has no Tor-verified launcher, so it will not start."
            )
        return launcher, cwd


class UpdateBridge:
    """Tor-only self-update for the installed standalone bundle.

    Source installs and development checkouts have no bundle to swap, so the
    bridge reports itself unavailable there and the UI says so instead of
    half-offering an update. Every network call goes through the verified Tor
    port with a fresh isolated circuit, exactly like Chat search: a failed
    check never falls back to a direct request.
    """

    def __init__(self, core: Any, desktop_core: Any) -> None:
        self.core = core
        self.desktop_core = desktop_core
        # Nuitka puts __compiled__ into each compiled module's globals (and
        # sets sys.frozen in standalone); a plain `python onionmind_desktop.py`
        # checkout has neither.
        frozen = "__compiled__" in globals() or bool(getattr(sys, "frozen", False))
        candidate = Path(sys.executable).resolve().parent if frozen else None
        if candidate is not None and not (candidate / "Onionmind.exe").is_file():
            candidate = None
        self.install_dir: Optional[Path] = candidate
        self.work_dir: Optional[Path] = (
            self.install_dir.parent / "onionmind-update" if self.install_dir else None
        )

    @property
    def available(self) -> bool:
        return self.desktop_core is not None and self.install_dir is not None

    def revision(self) -> Optional[str]:
        reader = getattr(self.desktop_core, "installed_revision", None)
        if not self.available or not callable(reader):
            return None
        return reader(self.install_dir)

    def revision_label(self) -> str:
        revision = self.revision()
        if revision is None:
            return "development copy" if not self.available else "unknown revision"
        helper = getattr(self.desktop_core, "short_revision", None)
        return f"revision {helper(revision) if callable(helper) else revision[:7]}"

    def _updater(self) -> Any:
        factory = getattr(self.core, "_proxies", None)
        if not callable(factory):
            raise RuntimeError("This Onionmind build cannot build Tor proxy settings.")
        user_agent = _as_text(getattr(self.core, "UA", "")) or (
            "Mozilla/5.0 (Windows NT 10.0; rv:128.0) Gecko/20100101 Firefox/128.0"
        )
        return self.desktop_core.BundleUpdater(
            self.install_dir,
            self.work_dir,
            lambda port: factory(port, True),   # fresh credentials => fresh circuit
            user_agent,
        )

    def tor_port(self) -> Optional[int]:
        port = getattr(self.core, "_port", None)
        return int(port) if port else None

    def check(self) -> Any:
        """Fetch and validate the feed manifest. Worker thread body."""

        port = self.tor_port()
        if port is None:
            raise RuntimeError("No verified Tor proxy this session; refusing a direct update check.")
        return self._updater().fetch_manifest(port)

    def download(
        self,
        manifest: Any,
        progress: Callable[[Optional[float], str], None],
        stop_event: Any,
    ) -> Path:
        """Download, verify, and stage the new bundle. Worker thread body."""

        port = self.tor_port()
        if port is None:
            raise RuntimeError("No verified Tor proxy this session; refusing a direct download.")
        updater = self._updater()
        archive = updater.download(port, manifest, progress=progress, stop_event=stop_event)
        return updater.stage(manifest, archive)

    def pending(self) -> Optional[Path]:
        finder = getattr(self.desktop_core, "pending_staging_dir", None)
        if not self.available or not callable(finder):
            return None
        return finder(self.work_dir)

    def housekeep(self) -> None:
        pruner = getattr(self.desktop_core, "prune_update_workdir", None)
        if self.available and callable(pruner):
            try:
                pruner(self.work_dir, running_revision=self.revision())
            except OSError:
                pass

    def apply_command(self, staging_dir: str) -> list[str]:
        return self._updater().apply_command(staging_dir)


class LeftRail(QWidget):
    newTaskRequested = Signal()
    addSessionRequested = Signal()
    openFolderRequested = Signal()
    sessionSelected = Signal(str)
    projectSelected = Signal(str)
    removeProjectRequested = Signal(str)
    deleteProjectRequested = Signal(str)
    modelsRequested = Signal()
    settingsRequested = Signal()
    exportRequested = Signal()
    archiveRequested = Signal(str)
    deleteSessionRequested = Signal(str)
    renameSessionRequested = Signal(str, str)

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
        _themed_icon(new_button, "new_task")
        new_button.setAccessibleName("Create new task")
        new_button.clicked.connect(self.newTaskRequested)
        self.new_button = new_button
        folder_button = QToolButton()
        folder_button.setObjectName("openFolderButton")
        _themed_icon(folder_button, "folder_open")
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
        _themed_icon(add_project, "folder_plus")
        add_project.setToolTip("Open another project")
        add_project.setAccessibleName("Open another project")
        add_project.clicked.connect(self.openFolderRequested)
        self.add_project_button = add_project
        remove_project = QToolButton()
        remove_project.setObjectName("bareButton")
        _themed_icon(remove_project, "close")
        remove_project.setToolTip(
            "Remove selected project from this list; its folder stays on this machine"
        )
        remove_project.setAccessibleName(
            "Remove selected project from Projects and keep its folder"
        )
        remove_project.clicked.connect(self._remove_selected_project)
        remove_project.setEnabled(False)
        self.remove_project_button = remove_project
        delete_project = QToolButton()
        delete_project.setObjectName("bareButton")
        _themed_icon(delete_project, "clear")
        delete_project.setToolTip(
            "Permanently delete the selected project folder from this machine"
        )
        delete_project.setAccessibleName(
            "Permanently delete selected project folder from this machine"
        )
        delete_project.clicked.connect(self._delete_selected_project)
        delete_project.setEnabled(False)
        self.delete_project_button = delete_project
        project_header.addWidget(project_label)
        project_header.addStretch(1)
        project_header.addWidget(add_project)
        project_header.addWidget(remove_project)
        project_header.addWidget(delete_project)
        layout.addLayout(project_header)

        self.projects = QListWidget()
        self.projects.setMaximumHeight(240)
        self.projects.setAccessibleName("Projects")
        self.projects.itemClicked.connect(
            lambda item: self.projectSelected.emit(_as_text(item.data(Qt.ItemDataRole.UserRole)))
        )
        self.projects.itemActivated.connect(
            lambda item: self.projectSelected.emit(_as_text(item.data(Qt.ItemDataRole.UserRole)))
        )
        self.projects.currentItemChanged.connect(self._project_current_changed)
        self.projects.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.projects.customContextMenuRequested.connect(self._show_project_menu)
        self.project_menu = QMenu(self)
        self.project_add_action = _themed_icon(self.project_menu.addAction("Add project…"), "folder_plus")
        self.project_add_action.triggered.connect(self.openFolderRequested)
        self.project_menu.addSeparator()
        self.project_remove_action = _themed_icon(self.project_menu.addAction("Remove from Projects (keep folder)"), "close")
        self.project_remove_action.triggered.connect(self._remove_selected_project)
        self.project_remove_action.setEnabled(False)
        self.project_delete_action = _themed_icon(self.project_menu.addAction("Delete folder from machine…"), "clear")
        self.project_delete_action.triggered.connect(self._delete_selected_project)
        self.project_delete_action.setEnabled(False)
        layout.addWidget(self.projects)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setObjectName("railDivider")
        layout.addWidget(divider)
        session_header = QHBoxLayout()
        session_label = QLabel("SESSIONS")
        session_label.setObjectName("sectionTitle")
        self.session_label = session_label
        add_session = QToolButton()
        add_session.setObjectName("bareButton")
        _themed_icon(add_session, "new_task")
        add_session.setToolTip("Add a saved session")
        add_session.setAccessibleName("Add saved session")
        add_session.clicked.connect(self.addSessionRequested)
        self.add_session_button = add_session
        archive = QToolButton()
        archive.setObjectName("bareButton")
        _themed_icon(archive, "archive")
        archive.setToolTip(
            "Remove selected session from this list; keep it in local archive storage"
        )
        archive.setAccessibleName(
            "Remove selected session from Sessions and keep an archived copy"
        )
        archive.clicked.connect(self._archive_selected)
        archive.setEnabled(False)
        self.archive_button = archive
        delete_session = QToolButton()
        delete_session.setObjectName("bareButton")
        _themed_icon(delete_session, "clear")
        delete_session.setToolTip(
            "Permanently delete the selected session from this machine"
        )
        delete_session.setAccessibleName(
            "Permanently delete selected session from this machine"
        )
        delete_session.clicked.connect(self._delete_selected_session)
        delete_session.setEnabled(False)
        self.delete_session_button = delete_session
        session_header.addWidget(session_label)
        session_header.addStretch(1)
        session_header.addWidget(add_session)
        session_header.addWidget(archive)
        session_header.addWidget(delete_session)
        layout.addLayout(session_header)
        self.session_filter = QLineEdit()
        self.session_filter.setPlaceholderText("Filter sessions")
        self.session_filter.setAccessibleName("Filter sessions by name")
        self.session_filter.setClearButtonEnabled(True)
        clear_filter = self.session_filter.findChild(QToolButton)
        if clear_filter is not None:
            clear_filter.setAccessibleName("Clear the session filter")
        self.session_filter.textChanged.connect(self._apply_session_filter)
        layout.addWidget(self.session_filter)
        self.sessions = QListWidget()
        self.sessions.setAccessibleName("Saved sessions")
        self.sessions.itemClicked.connect(
            lambda item: self.sessionSelected.emit(_as_text(item.data(Qt.ItemDataRole.UserRole)))
        )
        self.sessions.itemActivated.connect(
            lambda item: self.sessionSelected.emit(_as_text(item.data(Qt.ItemDataRole.UserRole)))
        )
        self.sessions.currentItemChanged.connect(self._session_current_changed)
        self.no_sessions = QLabel("No conversations yet. Start one with New task.")
        self.no_sessions.setObjectName("meta")
        self.no_sessions.setWordWrap(True)
        self.no_sessions.hide()
        self.empty_text_enabled = False
        self.sessions.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.sessions.customContextMenuRequested.connect(self._show_session_menu)
        self.session_menu = QMenu(self)
        self.session_add_action = _themed_icon(self.session_menu.addAction("Add session"), "new_task")
        self.session_add_action.triggered.connect(self.addSessionRequested)
        self.session_menu.addSeparator()
        self.session_rename_action = _themed_icon(self.session_menu.addAction("Rename…"), "pencil")
        self.session_rename_action.triggered.connect(self._rename_selected_session)
        self.session_rename_action.setEnabled(False)
        self.session_remove_action = _themed_icon(self.session_menu.addAction("Remove from Sessions (keep archived copy)"), "archive")
        self.session_remove_action.triggered.connect(self._archive_selected)
        self.session_remove_action.setEnabled(False)
        self.session_delete_action = _themed_icon(self.session_menu.addAction("Delete session from machine…"), "clear")
        self.session_delete_action.triggered.connect(self._delete_selected_session)
        self.session_delete_action.setEnabled(False)
        layout.addWidget(self.sessions, 1)
        layout.addWidget(self.no_sessions)
        # v2 sessionSearch: message hits from every project under the list.
        self.hits_title = QLabel("IN MESSAGES · ALL PROJECTS")
        self.hits_title.setObjectName("sectionTitle")
        self.hits_title.hide()
        layout.addWidget(self.hits_title)
        self.message_hits = QListWidget()
        self.message_hits.setAccessibleName("Message matches in every project")
        self.message_hits.setWordWrap(True)
        self.message_hits.hide()
        self.message_hits.itemActivated.connect(self._hit_chosen)
        self.message_hits.itemClicked.connect(self._hit_chosen)
        layout.addWidget(self.message_hits, 1)

        export = QPushButton("Export conversation")
        export.setObjectName("railAction")
        _themed_icon(export, "export")
        export.setToolTip("Export this conversation as Markdown (Ctrl+Shift+S)")
        export.setAccessibleName("Export conversation as Markdown")
        export.clicked.connect(self.exportRequested)
        export.setEnabled(False)
        self.export_button = export
        models = QPushButton("Models")
        models.setObjectName("railAction")
        _themed_icon(models, "model")
        models.setToolTip("Manage local models")
        models.setAccessibleName("Manage local models")
        models.clicked.connect(self.modelsRequested)
        self.models_button = models
        settings = QPushButton("Settings")
        settings.setObjectName("railAction")
        _themed_icon(settings, "settings")
        settings.setToolTip("Settings (Ctrl+,)")
        settings.setAccessibleName("Open Onionmind settings")
        settings.clicked.connect(self.settingsRequested)
        self.settings_button = settings
        layout.addWidget(export)
        layout.addWidget(models)
        layout.addWidget(settings)

    hitSelected = Signal(str)

    def _hit_chosen(self, item: QListWidgetItem) -> None:
        session_id = _as_text(item.data(Qt.ItemDataRole.UserRole))
        if session_id:
            self.hitSelected.emit(session_id)

    def set_message_hits(self, hits: Optional[list[dict[str, Any]]], project_name: Callable[[Any], str]) -> None:
        """None hides the section; a list (maybe empty) shows it."""
        self.message_hits.clear()
        visible = hits is not None
        self.hits_title.setVisible(visible)
        self.message_hits.setVisible(visible)
        if not visible:
            return
        import html as _html
        if not hits:
            empty = QListWidgetItem("No messages match.")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.message_hits.addItem(empty)
            return
        accent = theme_tokens()["accent_text"]
        for hit in hits:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, hit["session_id"])
            plain = f"{project_name(hit.get('workspace'))} · {hit['title']}\n{hit['before']}{hit['match']}{hit['after']}"
            item.setToolTip(plain)
            item.setData(Qt.ItemDataRole.AccessibleTextRole, plain)
            label = QLabel(
                f"<span>{_html.escape(project_name(hit.get('workspace')))} · {_html.escape(hit['title'])}</span><br>"
                f"<span style='color:{theme_tokens()['muted']}'>{_html.escape(hit['before'])}"
                f"<b style='color:{accent}'>{_html.escape(hit['match'])}</b>{_html.escape(hit['after'])}</span>")
            label.setTextFormat(Qt.TextFormat.RichText)
            label.setWordWrap(True)
            label.setContentsMargins(6, 4, 6, 4)
            item.setSizeHint(QSize(100, max(46, label.sizeHint().height() + 6)))
            self.message_hits.addItem(item)
            self.message_hits.setItemWidget(item, label)

    def _archive_selected(self) -> None:
        item = self.sessions.currentItem()
        if item is not None:
            self.archiveRequested.emit(_as_text(item.data(Qt.ItemDataRole.UserRole)))

    def _rename_selected_session(self) -> None:
        item = self.sessions.currentItem()
        if item is None:
            return
        session_id = _as_text(item.data(Qt.ItemDataRole.UserRole))
        current_title = item.text().split("\n", 1)[0]
        name, accepted = QInputDialog.getText(
            self, "Rename session", "Session name", text=current_title
        )
        if accepted and name.strip():
            self.renameSessionRequested.emit(session_id, name.strip())

    def _apply_session_filter(self, text: str) -> None:
        needle = text.strip().casefold()
        for row in range(self.sessions.count()):
            item = self.sessions.item(row)
            item.setHidden(bool(needle) and needle not in item.text().casefold())

    def _delete_selected_session(self) -> None:
        item = self.sessions.currentItem()
        if item is not None:
            self.deleteSessionRequested.emit(
                _as_text(item.data(Qt.ItemDataRole.UserRole))
            )

    def _remove_selected_project(self) -> None:
        item = self.projects.currentItem()
        if item is not None:
            self.removeProjectRequested.emit(
                _as_text(item.data(Qt.ItemDataRole.UserRole))
            )

    def _delete_selected_project(self) -> None:
        item = self.projects.currentItem()
        if item is not None:
            self.deleteProjectRequested.emit(
                _as_text(item.data(Qt.ItemDataRole.UserRole))
            )

    def _project_current_changed(
        self, current: Optional[QListWidgetItem], previous: Optional[QListWidgetItem]
    ) -> None:
        del previous
        available = current is not None
        self.remove_project_button.setEnabled(available)
        self.delete_project_button.setEnabled(available)
        self.project_remove_action.setEnabled(available)
        self.project_delete_action.setEnabled(available)

    def _session_current_changed(
        self, current: Optional[QListWidgetItem], previous: Optional[QListWidgetItem]
    ) -> None:
        del previous
        available = current is not None
        self.archive_button.setEnabled(available)
        self.delete_session_button.setEnabled(available)
        self.session_rename_action.setEnabled(available)
        self.session_remove_action.setEnabled(available)
        self.session_delete_action.setEnabled(available)

    def _show_project_menu(self, position: Any) -> None:
        item = self.projects.itemAt(position)
        if item is not None:
            self.projects.setCurrentItem(item)
        self.project_menu.popup(self.projects.viewport().mapToGlobal(position))

    def _show_session_menu(self, position: Any) -> None:
        item = self.sessions.itemAt(position)
        if item is not None:
            self.sessions.setCurrentItem(item)
        self.session_menu.popup(self.sessions.viewport().mapToGlobal(position))

    def set_conversation_available(self, available: bool) -> None:
        self.export_button.setEnabled(bool(available))

    def add_project(self, path: str, select: bool = True) -> None:
        if not path:
            return
        normalized = _path_key(path)
        for index in range(self.projects.count()):
            item = self.projects.item(index)
            if _path_key(item.data(Qt.ItemDataRole.UserRole)) == normalized:
                if select:
                    self.projects.setCurrentItem(item)
                return
        name = Path(path).name or path
        item = QListWidgetItem(f"{name}\n{path}")
        _themed_icon(item, "folder")
        item.setData(Qt.ItemDataRole.UserRole, path)
        item.setSizeHint(QSize(100, 47))
        self.projects.insertItem(0, item)
        if select:
            self.projects.setCurrentItem(item)

    def remove_project(self, path: str) -> bool:
        normalized = _path_key(path)
        for index in range(self.projects.count()):
            item = self.projects.item(index)
            item_path = _as_text(item.data(Qt.ItemDataRole.UserRole))
            if _path_key(item_path) == normalized:
                self.projects.takeItem(index)
                self.projects.clearSelection()
                self.projects.setCurrentRow(-1)
                return True
        return False

    def clear_projects(self) -> int:
        """Forget every remembered project. The folders themselves are not
        touched - this list is only Onionmind's memory of them."""
        removed = self.projects.count()
        self.projects.clear()
        return removed

    def clear_session_selection(self) -> None:
        self.sessions.clearSelection()
        self.sessions.setCurrentRow(-1)

    def set_session_scope(self, project: Optional[str]) -> None:
        """Name the project whose sessions are listed, so a short list reads as
        scoping rather than loss."""
        name = Path(project).name if project else ""
        self.session_label.setText(f"SESSIONS · {name}" if name else "SESSIONS")
        self.session_label.setToolTip(
            f"Sessions saved in {project}" if project else "Sessions saved with no project open"
        )

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
        self._apply_session_filter(self.session_filter.text())
        self.sync_empty_text()

    def sync_empty_text(self) -> None:
        self.no_sessions.setVisible(self.empty_text_enabled and self.sessions.count() == 0)


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
        _themed_icon(stop, "stop")
        stop.setToolTip(
            "Stop the shell command; child processes started by it may require manual termination"
        )
        stop.setAccessibleName("Stop terminal command")
        stop.clicked.connect(self.stop)
        stop.setEnabled(False)
        self.stop_button = stop
        clear = QToolButton()
        clear.setObjectName("bareButton")
        _themed_icon(clear, "clear")
        clear.setToolTip("Clear terminal")
        clear.setAccessibleName("Clear terminal output")
        clear.clicked.connect(lambda: self.output.clear())
        clear.setEnabled(False)
        self.clear_button = clear
        close = QToolButton()
        close.setObjectName("bareButton")
        _themed_icon(close, "close")
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
    revertRequested = Signal(list)
    hunksRequested = Signal(str)
    undoRequested = Signal()

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
        _themed_icon(refresh, "refresh")
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
        # v2 undo: per-file revert, Revert all, and an Undo bar - git on disk.
        self.undo_enabled = False
        self.undo_note = QLabel("Read from git on disk. Reverting restores a file to its last commit.")
        self.undo_note.setObjectName("meta")
        self.undo_note.setWordWrap(True)
        self.undo_note.hide()
        changes_layout.addWidget(self.undo_note)
        self.revert_all_button = QPushButton("Revert all changes")
        self.revert_all_button.setObjectName("dangerButton")
        self.revert_all_button.setAccessibleName("Revert every changed file to its last commit")
        self.revert_all_button.clicked.connect(lambda: self.revertRequested.emit(list(self._change_paths)))
        self.revert_all_button.hide()
        changes_layout.addWidget(self.revert_all_button)
        self.undo_bar = QFrame()
        self.undo_bar.setObjectName("banner")
        undo_layout = QHBoxLayout(self.undo_bar)
        undo_layout.setContentsMargins(9, 5, 9, 5)
        self.undo_label = QLabel("")
        self.undo_label.setWordWrap(True)
        self.undo_button = QPushButton("Undo")
        self.undo_button.setAccessibleName("Undo the revert and put the changes back on disk")
        self.undo_button.clicked.connect(self.undoRequested)
        undo_layout.addWidget(self.undo_label, 1)
        undo_layout.addWidget(self.undo_button)
        self.undo_bar.hide()
        changes_layout.addWidget(self.undo_bar)
        self._change_paths: list[str] = []
        self.empty_changes_note = ""
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
        # v2 zones: the same events in Machine / Tor / Outside lanes.
        self._activity_entries: list[tuple[str, str, str]] = []
        self.lanes = QWidget()
        lanes_layout = QVBoxLayout(self.lanes)
        lanes_layout.setContentsMargins(0, 0, 0, 0)
        lanes_layout.setSpacing(4)
        self.lane_lists: dict[str, QListWidget] = {}
        self.lane_titles: dict[str, QLabel] = {}
        for zone, title in (("machine", "MACHINE"), ("tor", "TOR"), ("outside", "OUTSIDE")):
            heading = QLabel(title)
            heading.setObjectName("sectionTitle")
            lane = QListWidget()
            lane.setAccessibleName(f"{title.title()} activity")
            lanes_layout.addWidget(heading)
            lanes_layout.addWidget(lane, 2 if zone == "machine" else 1)
            self.lane_lists[zone] = lane
            self.lane_titles[zone] = heading
        self.lanes.hide()
        activity_layout.addWidget(self.lanes, 1)
        # v2 netlog: export what left this machine.
        self.netlog_button = QPushButton("Export network log")
        self.netlog_button.setAccessibleName("Export the network log as JSON or CSV")
        self.netlog_button.hide()
        activity_layout.addWidget(self.netlog_button)

        self.tabs.addTab(self.context_tab, "Context")
        self.tabs.addTab(self.changes_tab, "Changes")
        self.tabs.addTab(self.activity_tab, "Activity")
        # v2 runPanel: built always, shown as a fourth tab only while the
        # feature is on.
        self.run_panel = RunPanel()

    def set_run_tab(self, visible: bool) -> None:
        index = self.tabs.indexOf(self.run_panel)
        if visible and index < 0:
            self.tabs.addTab(self.run_panel, "Run")
        elif not visible and index >= 0:
            self.tabs.removeTab(index)
        # Four tabs have to fit the 292px inspector; three keep the original spacing.
        self.tabs.tabBar().setStyleSheet(
            "QTabBar::tab { padding: 12px 9px 10px 9px; }" if visible else "")

    def show_run_tab(self) -> None:
        if self.tabs.indexOf(self.run_panel) >= 0:
            self.tabs.setCurrentWidget(self.run_panel)

    _OUTSIDE = re.compile(r"Tor search|search over Tor|over Tor|DuckDuckGo|direct(ly)? download|"
                          r"model download|downloaded|update check|model list|query|search allowed", re.IGNORECASE)
    _TOR = re.compile(r"\bTor\b|circuit|SOCKS|proxy", re.IGNORECASE)

    @classmethod
    def zone_of(cls, text: str) -> str:
        """Machine / Tor / Outside for an event that did not name its zone."""
        if cls._OUTSIDE.search(text):
            return "outside"
        if cls._TOR.search(text):
            return "tor"
        return "machine"

    def append_activity(self, text: str, zone: Optional[str] = None) -> None:
        stamp = datetime.now().strftime("%H:%M")
        clean = _brand_runtime_text(text)
        self.activity.insertItem(0, QListWidgetItem(f"{stamp}  {clean}"))
        while self.activity.count() > 150:
            self.activity.takeItem(self.activity.count() - 1)
        lane = zone if zone in ("machine", "tor", "outside") else self.zone_of(clean)
        self._activity_entries.insert(0, (stamp, clean, lane))
        del self._activity_entries[150:]
        target = self.lane_lists[lane]
        target.insertItem(0, QListWidgetItem(f"{stamp}  {clean}"))
        while target.count() > 150:
            target.takeItem(target.count() - 1)
        self._sync_lane_titles()

    def _sync_lane_titles(self) -> None:
        for zone, title in (("machine", "MACHINE"), ("tor", "TOR"), ("outside", "OUTSIDE")):
            self.lane_titles[zone].setText(f"{title} · {self.lane_lists[zone].count()}")

    def set_zones(self, on: bool) -> None:
        self.activity.setVisible(not on)
        self.lanes.setVisible(on)

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
        self._change_paths = [_as_text(change.get("path", "")) for change in changes]
        revertable = self.undo_enabled and bool(snapshot.get("is_git"))
        for change in changes:
            status = _as_text(change.get("status", "?"))
            path = _as_text(change.get("path", ""))
            original = change.get("original_path")
            text = f"{status:>2}  {path}" + (f"  from {original}" if original else "")
            item = QListWidgetItem(text)
            self.change_list.addItem(item)
            if revertable:
                row = QWidget()
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(4, 0, 2, 0)
                label = QLabel(text)
                label.setTextFormat(Qt.TextFormat.PlainText)
                revert = QToolButton()
                revert.setObjectName("bareButton")
                _themed_icon(revert, "revert", 15)
                revert.setToolTip(f"Revert {path} to its last commit")
                revert.setAccessibleName(f"Revert {path}")
                revert.clicked.connect(lambda _checked=False, p=path: self.revertRequested.emit([p]))
                row_layout.addWidget(label, 1)
                row_layout.addWidget(revert)
                if "M" in status and not set(status) & set("ADRCU?"):
                    # Hunks only exist for a file that is in HEAD and still on disk.
                    parts = QToolButton()
                    parts.setObjectName("bareButton")
                    parts.setText("Parts…")
                    parts.setToolTip(f"Choose which changes in {path} to revert")
                    parts.setAccessibleName(f"Revert parts of {path}")
                    parts.clicked.connect(lambda _checked=False, p=path: self.hunksRequested.emit(p))
                    row_layout.insertWidget(1, parts)    # before the revert icon

                self.change_list.setItemWidget(item, row)
        self.undo_note.setVisible(revertable)
        self.revert_all_button.setVisible(revertable and len(changes) > 1)
        if changes:
            self.change_summary.setText(snapshot.get("summary") or f"{len(changes)} observed change(s)")
        else:
            self.change_summary.setText("Working tree clean" if snapshot.get("is_git") else "Not a Git repository")
        diff = snapshot.get("diff") or "No diff to display."
        if not changes and self.empty_changes_note and snapshot.get("is_git"):
            diff = self.empty_changes_note
        self.diff_view.setPlainText(diff)

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
                        node.setData(0, _ICON_ROLE, "folder:18")
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
    catalogRequested = Signal()

    def __init__(
        self,
        models: list[str],
        current: str,
        label_for_model: Optional[Callable[[str], str]] = None,
        parent: Optional[QWidget] = None,
        reference_normalizer: Optional[Callable[[str], str]] = None,
        marker_for: Optional[Callable[[str], Optional[str]]] = None,
        catalog_entry_label: Optional[Callable[[Any], str]] = None,
    ) -> None:
        super().__init__(parent)
        self._label_for_model = label_for_model or (lambda value: "ONIONMIND MODEL")
        self._normalize = reference_normalizer or (lambda value: value)
        self._marker_for = marker_for
        self._catalog_entry_label = catalog_entry_label or (
            lambda entry: _as_text(_field(entry, "id"))
        )
        self.setWindowTitle("Onionmind models")
        self.setModal(True)
        self.resize(560, 640)
        layout = QVBoxLayout(self)
        heading = QLabel("Onionmind models")
        heading.setObjectName("brand")
        copy_label = QLabel(
            "Paste an Onionmind name (BLAZE), an Ollama name (llama3.2:3b), or a "
            "huggingface.co model link. Pulling asks the local model service to "
            "download directly; that download is not Tor-routed. The popular list "
            "is fetched over Tor and ordered by what this machine can run."
        )
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

        popular_label = QLabel("POPULAR")
        popular_label.setObjectName("sectionTitle")
        layout.addWidget(popular_label)
        browse_row = QHBoxLayout()
        self.browse_button = QPushButton("Browse popular models")
        self.browse_button.setAccessibleName("Fetch the popular model list over Tor")
        self.browse_button.setToolTip(
            "Fetches the most-downloaded GGUF models from huggingface.co through "
            "the verified Tor circuit - huggingface.co sees a Tor exit, not this "
            "machine. Needs Tor up; never falls back to a direct connection."
        )
        self.browse_button.clicked.connect(self._request_catalog)
        self.catalog_status = QLabel(
            "Not fetched yet - the list is checked live every time you browse"
        )
        self.catalog_status.setObjectName("meta")
        self.catalog_status.setWordWrap(True)
        self.catalog_add = QPushButton("Add selected")
        self.catalog_add.setAccessibleName("Add the selected popular model")
        self.catalog_add.setEnabled(False)
        self.catalog_add.clicked.connect(self._request_pull)
        browse_row.addWidget(self.browse_button)
        browse_row.addStretch(1)
        browse_row.addWidget(self.catalog_add)
        layout.addLayout(browse_row)
        layout.addWidget(self.catalog_status)
        self.catalog_list = QListWidget()
        self.catalog_list.setAccessibleName("Popular models fetched from huggingface.co")
        self.catalog_list.setToolTip(
            "Selecting a model fills the add field with its hf.co reference; "
            "Add selected downloads it"
        )
        self.catalog_list.setWordWrap(True)
        self.catalog_list.currentItemChanged.connect(self._catalog_picked)
        self.catalog_list.itemDoubleClicked.connect(lambda item: self._request_pull())
        layout.addWidget(self.catalog_list, 1)

        row = QHBoxLayout()
        self.model_name = QLineEdit()
        self.model_name.setPlaceholderText("BLAZE, llama3.2:3b, user/model-gguf, or a huggingface.co link")
        self.model_name.setAccessibleName("Onionmind model to add")
        self.pull_button = QPushButton("Add model")
        self.pull_button.setObjectName("primaryButton")
        self.pull_button.setAccessibleName("Add Onionmind model")
        self.pull_button.clicked.connect(self._request_pull)
        self.pull_button.setEnabled(False)
        self.model_name.textChanged.connect(self._sync_pull_button)
        self.model_name.textChanged.connect(self._sync_reference_hint)
        self.model_name.returnPressed.connect(self._request_pull)
        row.addWidget(self.model_name, 1)
        row.addWidget(self.pull_button)
        layout.addLayout(row)
        self.reference_hint = QLabel()
        self.reference_hint.setObjectName("meta")
        self.reference_hint.setWordWrap(True)
        layout.addWidget(self.reference_hint)
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

    def _sync_reference_hint(self) -> None:
        """Say what will actually be pulled, and flag refusal-removed names."""
        raw = self.model_name.text().strip()
        if not raw:
            self.reference_hint.setText("")
            return
        normalized = self._normalize(raw)
        parts = [f"Pulls as: {normalized}"] if normalized != raw else []
        if self._marker_for is not None:
            marker = self._marker_for(raw)
            if marker:
                parts.append(
                    f"Flagged by name as refusal-removed ({marker}) - verify before relying on it"
                )
        self.reference_hint.setText(" · ".join(parts))

    def _request_catalog(self) -> None:
        # The click is the action: every browse re-fetches the live list
        # through the same verified Tor circuit as search and updates, never
        # directly and never from a stored copy.
        self.browse_button.setEnabled(False)
        self.catalog_add.setEnabled(False)
        self.catalog_list.clear()
        self.catalog_status.setText("Fetching the current list over Tor…")
        self.catalogRequested.emit()

    def _catalog_picked(self, current: Any = None, previous: Any = None) -> None:
        del previous
        entry = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        self.catalog_add.setEnabled(entry is not None)
        if entry is None:
            return
        model_id = _as_text(_field(entry, "id"))
        if model_id:
            self.model_name.setText(f"hf.co/{model_id}")

    @staticmethod
    def _catalog_flag(entry: Any) -> str:
        """State the refusal-removal screen on every row, here rather than in
        an injected labeller, so no caller can drop it."""
        marker = _as_text(_field(entry, "uncensored"))
        if marker:
            return (
                f"UNCENSORED / ABLITERATED - flagged by name ({marker}); "
                "its refusals have been removed"
            )
        return "No refusal-removal flag in its name (not a guarantee)"

    def set_catalog(self, entries: Iterable[Any]) -> None:
        self.browse_button.setEnabled(True)
        self.browse_button.setText("Refresh popular models")
        self.catalog_list.clear()
        rows = list(entries)
        if not rows:
            self.catalog_status.setText("No models came back")
            return
        for entry in rows:
            text = f"{self._catalog_entry_label(entry)}\n{self._catalog_flag(entry)}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, entry)
            item.setToolTip(text)
            self.catalog_list.addItem(item)
        self.catalog_status.setText(
            f"{len(rows)} models · checked over Tor at {time.strftime('%H:%M')}"
        )

    def set_catalog_error(self, message: str) -> None:
        self.browse_button.setEnabled(True)
        self.catalog_add.setEnabled(False)
        self.catalog_list.clear()
        self.catalog_status.setText("Could not fetch the list")
        self.reference_hint.setText(_brand_runtime_text(message)[:120])

    def _request_pull(self) -> None:
        raw = self.model_name.text().strip()
        if not raw:
            self.progress.setFormat("Enter a model name or link")
            return
        name = self._normalize(raw)
        answer = QMessageBox.warning(
            self,
            "Direct model download",
            "The local model service will download this model directly from its "
            "configured registry. This is not Tor-routed and exposes this machine's "
            "network address. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.progress.setFormat("Download cancelled")
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


def _feature_catalog(desktop_core: Any) -> tuple[tuple[str, str, str], ...]:
    """(key, name, description) for every v2 feature, from the core. Without
    the core there are no v2 features: the workbench is exactly the original."""
    return tuple(getattr(desktop_core, "FEATURE_FLAGS", ()) or ())


class FeatureToggles(QWidget):
    """The list of v2 features with Turn all on / Turn all off.

    Used twice - in the toolbar sparkle popover and in Settings -> Features -
    and both drive the same window.features dict, so they never disagree.
    """

    INTRO = (
        "Everything from the original workbench stays where it is. These add to it, "
        "and each one stays hidden until you turn it on."
    )

    def __init__(self, window: Any, parent: Optional[QWidget] = None, *, compact: bool = False) -> None:
        super().__init__(parent)
        self.window = window
        self.boxes: dict[str, QCheckBox] = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)
        header = QHBoxLayout()
        title = QLabel("New features")
        title.setObjectName("title")
        self.count_label = QLabel()
        self.count_label.setObjectName("meta")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.count_label)
        outer.addLayout(header)
        intro = QLabel(self.INTRO)
        intro.setObjectName("meta")
        intro.setWordWrap(True)
        outer.addWidget(intro)
        rows = QWidget()
        rows_layout = QVBoxLayout(rows)
        rows_layout.setContentsMargins(0, 4, 0, 4)
        rows_layout.setSpacing(8 if compact else 10)
        for key, name, description in _feature_catalog(getattr(window, "desktop_core", None)):
            box = QCheckBox(name)
            box.setObjectName(f"feature_{key}")
            box.setAccessibleName(f"{name}. {description}")
            box.setToolTip(description)
            box.setChecked(bool(window.features.get(key)))
            box.toggled.connect(lambda checked, flag=key: self.window.set_feature(flag, checked))
            note = QLabel(description)
            note.setObjectName("meta")
            note.setWordWrap(True)
            note.setContentsMargins(24, 0, 0, 0)
            rows_layout.addWidget(box)
            rows_layout.addWidget(note)
            self.boxes[key] = box
        if compact:
            scroll = QScrollArea()
            scroll.setObjectName("settingsPage")
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidget(rows)
            scroll.setMinimumHeight(260)
            outer.addWidget(scroll, 1)
        else:
            outer.addWidget(rows)
        footer = QHBoxLayout()
        self.all_on = QPushButton("Turn all on")
        self.all_on.setAccessibleName("Turn all new features on")
        self.all_on.clicked.connect(lambda: self.window.set_all_features(True))
        self.all_off = QPushButton("Turn all off")
        self.all_off.setAccessibleName("Turn all new features off")
        self.all_off.clicked.connect(lambda: self.window.set_all_features(False))
        self.run_setup = QPushButton("Run setup")
        self.run_setup.setAccessibleName("Run first-launch setup again")
        self.run_setup.clicked.connect(lambda: self.window.open_setup())
        footer.addWidget(self.all_on)
        footer.addWidget(self.all_off)
        footer.addStretch(1)
        footer.addWidget(self.run_setup)
        outer.addLayout(footer)
        self.sync()

    def sync(self) -> None:
        features = self.window.features
        for key, box in self.boxes.items():
            if box.isChecked() != bool(features.get(key)):
                box.blockSignals(True)
                box.setChecked(bool(features.get(key)))
                box.blockSignals(False)
        on = sum(1 for key in self.boxes if features.get(key))
        self.count_label.setText(f"{on} of {len(self.boxes)} on")
        self.run_setup.setVisible(bool(features.get("setup")))


class FeaturesPopover(QFrame):
    """The toolbar sparkle popover: the same toggles as Settings -> Features."""

    def __init__(self, window: Any) -> None:
        super().__init__(window, Qt.WindowType.Popup)
        self.setObjectName("popover")
        self.setAccessibleName("New features")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        self.toggles = FeatureToggles(window, self, compact=True)
        layout.addWidget(self.toggles)
        self.resize(400, 520)

    def show_below(self, anchor: QWidget) -> None:
        self.toggles.sync()
        point = anchor.mapToGlobal(anchor.rect().bottomRight())
        screen = anchor.screen()
        height = 520
        if screen is not None:
            height = min(520, screen.availableGeometry().bottom() - point.y() - 12)
        self.resize(400, max(260, height))
        self.move(point.x() - self.width(), point.y() + 6)
        self.show()
        first = next(iter(self.toggles.boxes.values()), None)
        if first is not None:
            first.setFocus(Qt.FocusReason.PopupFocusReason)


class SettingsDialog(QDialog):
    def __init__(
        self,
        data_root: Path,
        agent_limitation: str,
        update_bridge: Optional[UpdateBridge] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.data_root = data_root
        self.update_bridge = update_bridge
        self.update_manifest: Any = None
        self.update_staging: Optional[str] = None
        self._update_stop: Optional[threading.Event] = None
        self.setWindowTitle("Onionmind settings")
        # One tab per concern, and each tab scrolls: a small laptop panel
        # cannot show the whole of any of them, so the size clamps to the
        # available geometry instead of pushing the Close row out of reach.
        screen = self.screen() or QApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else None
        self.resize(
            min(560, available.width() - 40) if available else 560,
            min(660, available.height() - 80) if available else 660,
        )
        preferences = dict(getattr(self._window(), "preferences", {}) or {})
        self._feature_sections: dict[str, QWidget] = {}
        tabs = QTabWidget(self)
        tabs.setAccessibleName("Onionmind settings sections")
        self.tabs = tabs
        tabs.addTab(self._page(self._general_page(preferences)), "General")
        tabs.addTab(self._page(self._compute_page()), "Compute")
        tabs.addTab(self._page(self._privacy_page(preferences, agent_limitation)), "Privacy")
        tabs.addTab(self._page(self._updates_page(update_bridge)), "Updates")
        tabs.addTab(self._page(self._storage_page(data_root)), "Storage")
        # v2: one tab that lists the features, then the tabs individual
        # features own, added and removed live as they are switched.
        self.feature_toggles: Optional[FeatureToggles] = None
        self._feature_tabs: dict[str, QWidget] = {}
        window = self._window()
        if window is not None and _feature_catalog(window.desktop_core):
            self.feature_toggles = FeatureToggles(window, self)
            tabs.addTab(self._page(self._padded(self.feature_toggles)), "Features")
            window.featuresChanged.connect(self._sync_features)
            self._sync_features()

        self.storage_feedback = QLabel()
        self.storage_feedback.setObjectName("meta")
        self.storage_feedback.setWordWrap(True)
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        open_folder = actions.addButton("Open storage folder", QDialogButtonBox.ButtonRole.ActionRole)
        open_folder.setAccessibleName("Open Onionmind storage folder")
        open_folder.clicked.connect(self._open_storage_folder)
        actions.rejected.connect(self.reject)
        root = QVBoxLayout(self)
        root.addWidget(tabs, 1)
        root.addWidget(self.storage_feedback)
        root.addWidget(actions)

        if update_bridge is None or not update_bridge.available:
            self.check_updates_button.setEnabled(False)
            self.update_feedback.setText("")
        pending = update_bridge.pending() if update_bridge and update_bridge.available else None
        if pending is not None:
            self._offer_restart(str(pending))

    # Feature-owned tabs, in the order they appear after Features.
    FEATURE_TABS: tuple[tuple[str, str, str], ...] = (
        ("keyboard", "Keyboard", "_keyboard_page"),
        ("rules", "Agent rules", "_rules_page"),
        ("presets", "Presets", "_presets_page"),
    )

    def _appearance_section(self) -> QWidget:
        section = QWidget()
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 8, 0, 0)
        heading = QLabel("Appearance")
        heading.setObjectName("brand")
        layout.addWidget(heading)
        form = QFormLayout()
        form.setHorizontalSpacing(18)
        window = self._window()
        values = window._appearance() if window is not None else {"theme": "dark", "ui_scale": 100, "notify": False}
        self.theme_combo = QComboBox()
        self.theme_combo.setAccessibleName("Theme")
        self.theme_combo.addItem("Dark", "dark")
        self.theme_combo.addItem("Light", "light")
        self.theme_combo.setCurrentIndex(self.theme_combo.findData(values["theme"]))
        form.addRow("Theme", self.theme_combo)
        self.ui_scale_combo = QComboBox()
        self.ui_scale_combo.setAccessibleName("Interface size")
        for size in (90, 100, 110, 125):
            self.ui_scale_combo.addItem(f"{size}%", size)
        self.ui_scale_combo.setCurrentIndex(self.ui_scale_combo.findData(values["ui_scale"]))
        form.addRow("Interface size", self.ui_scale_combo)
        self.notify_box = QCheckBox("Notify me when a reply or run finishes, or needs my approval")
        self.notify_box.setChecked(bool(values["notify"]))
        self.notify_box.setToolTip("Uses the system's own notifications, and only while Onionmind is not in front.")
        form.addRow("Notifications", self.notify_box)
        layout.addLayout(form)
        if window is not None:
            self.theme_combo.currentIndexChanged.connect(
                lambda _i: window.set_appearance("theme", self.theme_combo.currentData()))
            self.ui_scale_combo.currentIndexChanged.connect(
                lambda _i: window.set_appearance("ui_scale", self.ui_scale_combo.currentData()))
            self.notify_box.toggled.connect(lambda on: window.set_appearance("notify", on))
        self._feature_sections["appearance"] = section
        return section

    def _setup_section(self) -> QWidget:
        section = QWidget()
        layout = QHBoxLayout(section)
        layout.setContentsMargins(0, 8, 0, 0)
        note = QLabel("Walk through model, Tor and project again.")
        note.setWordWrap(True)
        self.setup_again = QPushButton("Run setup again")
        self.setup_again.setAccessibleName("Run first-launch setup again")
        self.setup_again.clicked.connect(self._run_setup_again)
        layout.addWidget(note, 1)
        layout.addWidget(self.setup_again)
        self._feature_sections["setup"] = section
        return section

    def _run_setup_again(self) -> None:
        window = self._window()
        self.done(0)
        if window is not None:
            window.open_setup()

    def _lock_section(self) -> QWidget:
        section = QWidget()
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 8, 0, 0)
        heading = QLabel("Auto-delete and lock")
        heading.setObjectName("brand")
        layout.addWidget(heading)
        window = self._window()
        settings = window._lock_settings() if window is not None else {}
        form = QFormLayout()
        form.setHorizontalSpacing(18)
        self.auto_delete_combo = QComboBox()
        self.auto_delete_combo.setAccessibleName("Delete conversations automatically")
        for label, days in (("Never", 0), ("Older than 1 day", 1), ("Older than 7 days", 7), ("Older than 30 days", 30)):
            self.auto_delete_combo.addItem(label, days)
        self.auto_delete_combo.setCurrentIndex(max(0, self.auto_delete_combo.findData(int(settings.get("auto_delete_days") or 0))))
        form.addRow("Delete conversations", self.auto_delete_combo)
        self.auto_delete_note = QLabel("")
        self.auto_delete_note.setObjectName("meta")
        self.auto_delete_note.setWordWrap(True)
        self.delete_now = QPushButton("Delete now")
        self.delete_now.setObjectName("dangerButton")
        note_row = QHBoxLayout()
        note_row.addWidget(self.auto_delete_note, 1)
        note_row.addWidget(self.delete_now)
        form.addRow("", note_row)
        self.lock_after_combo = QComboBox()
        self.lock_after_combo.setAccessibleName("Lock after idle time")
        for label, minutes in (("Never", 0), ("After 5 minutes idle", 5), ("After 15 minutes idle", 15), ("After 1 hour idle", 60)):
            self.lock_after_combo.addItem(label, minutes)
        self.lock_after_combo.setCurrentIndex(max(0, self.lock_after_combo.findData(int(settings.get("lock_after") or 0))))
        form.addRow("Lock", self.lock_after_combo)
        self.passphrase_field = QLineEdit()
        self.passphrase_field.setEchoMode(QLineEdit.EchoMode.Password)
        self.passphrase_field.setPlaceholderText("New passphrase")
        self.passphrase_field.setAccessibleName("New passphrase")
        self.passphrase_button = QPushButton("Set passphrase")
        pass_row = QHBoxLayout()
        pass_row.addWidget(self.passphrase_field, 1)
        pass_row.addWidget(self.passphrase_button)
        form.addRow("Passphrase", pass_row)
        self.lock_now_button = QPushButton("Lock now")
        form.addRow("", self.lock_now_button)
        layout.addLayout(form)
        self.lock_feedback = QLabel("Only a salted scrypt hash of the passphrase is stored.")
        self.lock_feedback.setObjectName("meta")
        self.lock_feedback.setWordWrap(True)
        layout.addWidget(self.lock_feedback)
        if window is not None:
            self.auto_delete_combo.currentIndexChanged.connect(self._auto_delete_changed)
            self.delete_now.clicked.connect(self._delete_old_now)
            self.lock_after_combo.currentIndexChanged.connect(
                lambda _i: window.set_lock_setting("lock_after", self.lock_after_combo.currentData()))
            self.passphrase_button.clicked.connect(self._set_passphrase)
            self.lock_now_button.clicked.connect(self._lock_now)
            self._describe_old()
        self._feature_sections["lock"] = section
        return section

    def _describe_old(self) -> None:
        window = self._window()
        days = int(self.auto_delete_combo.currentData() or 0)
        old = len(window.old_sessions()) if window is not None and days else 0
        self.delete_now.setVisible(bool(days))
        if not days:
            self.auto_delete_note.setText("Conversations stay until you delete them.")
            return
        span = "1 day" if days == 1 else f"{days} days"
        self.auto_delete_note.setText(
            f"{old} conversation{'s are' if old != 1 else ' is'} older than {span} and will be deleted.")

    def _auto_delete_changed(self) -> None:
        window = self._window()
        if window is not None:
            window.set_lock_setting("auto_delete_days", int(self.auto_delete_combo.currentData() or 0))
        self._describe_old()

    def _delete_old_now(self) -> None:
        window = self._window()
        if window is None:
            return
        removed = window._auto_delete_old()
        self.lock_feedback.setText(f"Deleted {removed} old conversation{'s' if removed != 1 else ''}.")
        self._describe_old()

    def _set_passphrase(self) -> None:
        window = self._window()
        text = self.passphrase_field.text()
        if window is None or len(text) < 4:
            self.lock_feedback.setText("Use a passphrase of at least four characters.")
            return
        window.set_passphrase(text)
        self.passphrase_field.clear()
        self.passphrase_button.setText("Change passphrase")
        self.lock_feedback.setText("Passphrase set. Only a salted scrypt hash of it is stored.")

    def _lock_now(self) -> None:
        window = self._window()
        if window is None:
            return
        if not window.has_passphrase():
            self.lock_feedback.setText("Set a passphrase first; without one anybody could unlock.")
            return
        self.done(0)
        window.lock_now()

    def _keyboard_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        heading = QLabel("Keyboard")
        heading.setObjectName("brand")
        outer.addWidget(heading)
        window = self._window()
        features = getattr(window, "features", {}) if window is not None else {}
        enter_sends = bool(getattr(window, "preferences", {}).get("enter_sends", True)) if window else True
        native = lambda seq: QKeySequence(seq).toString(QKeySequence.SequenceFormat.NativeText)
        rows = [
            ("Command palette", native("Ctrl+K"), features.get("palette")),
            ("New task", native("Ctrl+N"), True),
            ("Open a project folder", native("Ctrl+O"), True),
            ("Focus the composer", native("Ctrl+L"), True),
            ("Settings", native("Ctrl+,"), True),
            ("Terminal", native("Ctrl+`"), True),
            ("Inspector", native("Ctrl+Shift+I"), True),
            ("Boundary map", native("Ctrl+B"), features.get("boundary")),
            ("Export conversation", native("Ctrl+Shift+S"), True),
            ("Stop a run or close a panel", "Esc", True),
            ("Send", "Enter" if enter_sends else native("Ctrl+Return"), True),
            ("New line", "Shift+Enter" if enter_sends else "Enter", True),
            ("Keyboard shortcuts", "?", True),
        ]
        form = QFormLayout()
        form.setHorizontalSpacing(24)
        self.keyboard_rows: dict[str, str] = {}
        for label, keys, shown in rows:
            if not shown:
                continue
            chip = QLabel(keys)
            chip.setObjectName("kbd")
            chip.setAccessibleName(f"{label}: {keys}")
            form.addRow(label, chip)
            self.keyboard_rows[label] = keys
        outer.addLayout(form)
        motion = QLabel(
            "Animations stop when the system asks for reduced motion (Windows, GNOME or macOS), "
            "or when Animation is set to Reduced in General.")
        motion.setObjectName("meta")
        motion.setWordWrap(True)
        outer.addWidget(motion)
        outer.addStretch(1)
        return page

    def _rules_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        window = self._window()
        core = getattr(window, "desktop_core", None)
        workspace = getattr(window, "workspace", None)
        heading = QLabel(f"Agent rules for {Path(workspace).name}" if workspace else "Agent rules")
        heading.setObjectName("brand")
        outer.addWidget(heading)
        if not workspace or not callable(getattr(core, "load_rules", None)):
            note = QLabel("Open a project to set its Agent rules.")
            note.setObjectName("meta")
            outer.addWidget(note)
            outer.addStretch(1)
            return page
        intro = QLabel("Rules apply only to this project. Anything they don’t allow still asks you first, "
                       "or stops safely.")
        intro.setObjectName("meta")
        intro.setWordWrap(True)
        outer.addWidget(intro)
        rules = core.load_rules(window.settings_data, workspace)
        self.rule_boxes: dict[str, QCheckBox] = {}
        for key, name, description in core.RULE_FLAGS:
            box = QCheckBox(name)
            box.setChecked(bool(rules.get(key)))
            box.setAccessibleName(f"{name}. {description}")
            note = QLabel(description)
            note.setObjectName("meta")
            note.setContentsMargins(24, 0, 0, 4)
            outer.addWidget(box)
            outer.addWidget(note)
            self.rule_boxes[key] = box
        blocked_title = QLabel("Never touch these paths")
        blocked_title.setObjectName("title")
        self.rule_blocked = QLineEdit(_as_text(rules.get("blocked")))
        self.rule_blocked.setAccessibleName("Paths Agent can never read or edit in this project")
        blocked_help = QLabel("Comma separated. Agent can’t read or edit them, even in YOLO.")
        blocked_help.setObjectName("meta")
        notes_title = QLabel("Project instructions")
        notes_title.setObjectName("title")
        self.rule_notes = QPlainTextEdit(_as_text(rules.get("notes")))
        self.rule_notes.setPlaceholderText("Use minitest, not rspec. Keep changes small.")
        self.rule_notes.setAccessibleName("Project instructions for Agent")
        self.rule_notes.setMaximumHeight(110)
        notes_help = QLabel("Sent to the model with every Agent run in this project, alongside AGENTS.md.")
        notes_help.setObjectName("meta")
        notes_help.setWordWrap(True)
        for widget in (blocked_title, self.rule_blocked, blocked_help, notes_title, self.rule_notes, notes_help):
            outer.addWidget(widget)
        outer.addStretch(1)
        self._rules_timer = QTimer(self)
        self._rules_timer.setSingleShot(True)
        self._rules_timer.setInterval(350)
        self._rules_timer.timeout.connect(self._save_rules)
        for box in self.rule_boxes.values():
            box.toggled.connect(lambda _on: self._save_rules())
        self.rule_blocked.editingFinished.connect(self._save_rules)
        self.rule_notes.textChanged.connect(self._rules_timer.start)
        return page

    def _save_rules(self) -> None:
        window = self._window()
        if window is None or not hasattr(self, "rule_boxes"):
            return
        rules = {key: box.isChecked() for key, box in self.rule_boxes.items()}
        rules["blocked"] = self.rule_blocked.text()
        rules["notes"] = self.rule_notes.toPlainText()
        window.save_project_rules(rules)

    def _presets_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        heading = QLabel("Presets")
        heading.setObjectName("brand")
        outer.addWidget(heading)
        intro = QLabel("A preset is the system prompt Onionmind sends before your message. "
                       "Pick one from the composer.")
        intro.setObjectName("meta")
        intro.setWordWrap(True)
        outer.addWidget(intro)
        window = self._window()
        self.preset_list = QListWidget()
        self.preset_list.setAccessibleName("Presets")
        self.preset_list.setMaximumHeight(130)
        for preset in (window.presets() if window is not None else []):
            item = QListWidgetItem(f"{preset['name']} · {preset['short']}")
            item.setData(Qt.ItemDataRole.UserRole, preset["id"])
            self.preset_list.addItem(item)
        outer.addWidget(self.preset_list)
        self.preset_label = QLabel("")
        self.preset_label.setObjectName("title")
        outer.addWidget(self.preset_label)
        self.preset_editor = QPlainTextEdit()
        self.preset_editor.setAccessibleName("System prompt text")
        outer.addWidget(self.preset_editor, 1)
        row = QHBoxLayout()
        self.preset_use = QPushButton("Use this preset")
        self.preset_use.setObjectName("primaryButton")
        self.preset_reset = QPushButton("Reset to default text")
        row.addWidget(self.preset_use)
        row.addStretch(1)
        row.addWidget(self.preset_reset)
        outer.addLayout(row)
        self.preset_list.currentRowChanged.connect(lambda _row: self._show_preset())
        self.preset_editor.textChanged.connect(self._preset_edited)
        self.preset_use.clicked.connect(
            lambda: window.set_preset(self._current_preset_id()) if window is not None else None)
        self.preset_reset.clicked.connect(self._reset_preset)
        if self.preset_list.count():
            current = _as_text(getattr(window, "settings_data", {}).get("preset") or "default")
            ids = [self.preset_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.preset_list.count())]
            self.preset_list.setCurrentRow(ids.index(current) if current in ids else 0)
        return page

    def _current_preset_id(self) -> str:
        item = self.preset_list.currentItem()
        return _as_text(item.data(Qt.ItemDataRole.UserRole)) if item is not None else "default"

    def _show_preset(self) -> None:
        window = self._window()
        preset = next((p for p in window.presets() if p["id"] == self._current_preset_id()), None) if window else None
        if preset is None:
            return
        self.preset_label.setText(f"System prompt for {preset['name']}")
        self.preset_editor.blockSignals(True)
        self.preset_editor.setPlainText(preset["text"])
        self.preset_editor.blockSignals(False)

    def _preset_edited(self) -> None:
        window = self._window()
        if window is not None:
            window.set_preset_text(self._current_preset_id(), self.preset_editor.toPlainText())

    def _reset_preset(self) -> None:
        window = self._window()
        if window is not None:
            window.set_preset_text(self._current_preset_id(), None)
            self._show_preset()

    @staticmethod
    def _padded(content: QWidget) -> QWidget:
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(4, 8, 4, 8)
        layout.addWidget(content)
        layout.addStretch(1)
        return holder

    def _sync_features(self, *_args: Any) -> None:
        window = self._window()
        if window is None:
            return
        if self.feature_toggles is not None:
            self.feature_toggles.sync()
        for key, label, builder_name in self.FEATURE_TABS:
            on = bool(window.features.get(key))
            page = self._feature_tabs.get(key)
            builder = getattr(self, builder_name, None)
            if on and page is None and callable(builder):
                page = self._page(builder())
                self._feature_tabs[key] = page
                self.tabs.addTab(page, label)
            elif not on and page is not None:
                index = self.tabs.indexOf(page)
                if index >= 0:
                    self.tabs.removeTab(index)
                page.deleteLater()
                self._feature_tabs.pop(key, None)
        for key, section in getattr(self, "_feature_sections", {}).items():
            section.setVisible(bool(window.features.get(key)))

    def show_tab(self, label: str) -> bool:
        for index in range(self.tabs.count()):
            if self.tabs.tabText(index) == label:
                self.tabs.setCurrentIndex(index)
                return True
        return False

    def done(self, result: int) -> None:
        window = self._window()
        if window is not None and self.feature_toggles is not None:
            try:
                window.featuresChanged.disconnect(self._sync_features)
            except (RuntimeError, TypeError):
                pass
        super().done(result)

    @staticmethod
    def _page(content: QWidget) -> QScrollArea:
        """Each tab scrolls on its own, so a short laptop panel never pushes
        the Close row off the screen."""
        scroll = QScrollArea()
        # Named so the sheet can keep the page transparent: a bare QWidget in a
        # scroll area takes the platform's own background, which on a dark app
        # is a white slab.
        scroll.setObjectName("settingsPage")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        return scroll

    def _general_page(self, preferences: dict[str, Any]) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        heading = QLabel("Workbench")
        heading.setObjectName("brand")
        outer.addWidget(heading)
        form = QFormLayout()
        form.setHorizontalSpacing(18)

        self.text_size_combo = QComboBox()
        self.text_size_combo.setAccessibleName("Workbench text size")
        for label, value in (
            ("System default", "system"),
            ("Compact", "compact"),
            ("Comfortable", "comfortable"),
        ):
            self.text_size_combo.addItem(label, value)
        self.text_size_combo.setCurrentIndex(
            max(0, self.text_size_combo.findData(_as_text(preferences.get("text_scale", "system"))))
        )
        form.addRow("Text size", self.text_size_combo)

        self.enter_sends_box = QCheckBox("Enter sends the message")
        self.enter_sends_box.setToolTip(
            "On: Enter sends and Shift+Enter makes a newline. "
            "Off: Ctrl+Enter sends and Enter makes a newline."
        )
        self.enter_sends_box.setAccessibleName(
            "Enter sends the message; when off, Ctrl+Enter sends"
        )
        self.enter_sends_box.setChecked(bool(preferences.get("enter_sends", True)))
        form.addRow("Composer", self.enter_sends_box)

        self.startup_combo = QComboBox()
        self.startup_combo.setAccessibleName("Composer mode to start in")
        self.startup_combo.setToolTip("Applies the next time Onionmind opens.")
        for label, value in (
            ("Remember last", "remember"),
            ("Chat", "chat"),
            ("Agent", "agent"),
        ):
            self.startup_combo.addItem(label, value)
        self.startup_combo.setCurrentIndex(
            max(0, self.startup_combo.findData(_as_text(preferences.get("startup_mode", "remember"))))
        )
        form.addRow("Start in", self.startup_combo)

        self.terminal_box = QCheckBox("Show the terminal drawer on launch")
        self.terminal_box.setChecked(bool(preferences.get("show_terminal_on_launch", False)))
        form.addRow("Terminal", self.terminal_box)

        self.motion_combo = QComboBox()
        self.motion_combo.setAccessibleName("Workbench animation")
        self.motion_combo.setToolTip(
            "Reduced keeps pending states static; Follow the system honours the "
            "operating system's animation setting."
        )
        for label, value in (
            ("Follow the system", "system"),
            ("Reduced", "reduced"),
            ("Full", "full"),
        ):
            self.motion_combo.addItem(label, value)
        self.motion_combo.setCurrentIndex(
            max(0, self.motion_combo.findData(_as_text(preferences.get("reduce_motion", "system"))))
        )
        form.addRow("Animation", self.motion_combo)

        self.context_combo = QComboBox()
        self.context_combo.setAccessibleName("Chat context window in tokens")
        self.context_combo.setToolTip(
            "How much conversation the local model keeps in mind in Chat. Pick a "
            "preset or type any token count - 24000 and 24k both work. Bigger "
            "remembers more and costs more memory on this machine; it applies to "
            "the next turn. The agent has its own budget - the toolbar slider."
        )
        # Editable: the presets are stops worth one click, not a fence. Anything
        # that parses as a positive count is accepted and only warned about.
        self.context_combo.setEditable(True)
        self.context_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        presets = getattr(self.desktop_core(), "CONTEXT_WINDOW_PRESETS", None) or (
            4096, 8192, 16384, 32768, 65536, 131072
        )
        for value in presets:
            self.context_combo.addItem(str(value), value)
        self.context_combo.setCurrentText(
            str(self._context_tokens(preferences.get("context_window", 16384)))
        )
        form.addRow("Chat context", self.context_combo)
        self.context_hint = QLabel()
        self.context_hint.setObjectName("meta")
        self.context_hint.setWordWrap(True)
        form.addRow("", self.context_hint)
        self._describe_context(self.context_combo.currentText())
        outer.addLayout(form)

        # States are set above; wiring afterwards keeps the constructor's
        # initial values from firing preference writes of their own.
        self.text_size_combo.currentIndexChanged.connect(
            lambda _index: self._set_preference("text_scale", self.text_size_combo.currentData())
        )
        self.enter_sends_box.toggled.connect(
            lambda checked: self._set_preference("enter_sends", checked)
        )
        self.startup_combo.currentIndexChanged.connect(
            lambda _index: self._set_preference("startup_mode", self.startup_combo.currentData())
        )
        self.terminal_box.toggled.connect(
            lambda checked: self._set_preference("show_terminal_on_launch", checked)
        )
        self.motion_combo.currentIndexChanged.connect(
            lambda _index: self._set_preference("reduce_motion", self.motion_combo.currentData())
        )
        # Typing only previews; the value is stored when the field is left or a
        # preset is chosen, so a half-typed "2" never becomes the setting.
        self.context_combo.currentTextChanged.connect(self._describe_context)
        self.context_combo.activated.connect(lambda _index: self._commit_context())
        line = self.context_combo.lineEdit()
        if line is not None:
            line.editingFinished.connect(self._commit_context)

        outer.addWidget(self._appearance_section())
        outer.addWidget(self._setup_section())
        window_heading = QLabel("Window")
        window_heading.setObjectName("brand")
        outer.addWidget(window_heading)
        window_row = QHBoxLayout()
        window_note = QLabel(
            "Window size, pane widths, and pane visibility are remembered between launches."
        )
        window_note.setWordWrap(True)
        window_row.addWidget(window_note, 1)
        self.reset_layout_button = QPushButton("Reset window layout")
        self.reset_layout_button.setAccessibleName("Reset the remembered Onionmind window layout")
        self.reset_layout_button.clicked.connect(self._reset_window_layout)
        window_row.addWidget(self.reset_layout_button)
        outer.addLayout(window_row)
        self.window_feedback = QLabel()
        self.window_feedback.setObjectName("meta")
        self.window_feedback.setWordWrap(True)
        outer.addWidget(self.window_feedback)
        outer.addStretch(1)
        return page

    def _compute_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        heading = QLabel("Remote compute")
        heading.setObjectName("brand")
        outer.addWidget(heading)
        intro = QLabel(
            "Run the models on another computer you can reach over SSH - a GPU "
            "server, say. Your projects, edits, the agent, the terminal and Tor "
            "stay on this machine; only prompts and replies travel, through an "
            "encrypted SSH tunnel to that computer's Ollama."
        )
        intro.setWordWrap(True)
        outer.addWidget(intro)
        window = self._window()
        settings = window.remote_settings() if window is not None else {"host": "", "autoconnect": False}
        form = QFormLayout()
        form.setHorizontalSpacing(18)
        self.remote_host_field = QLineEdit(settings["host"])
        self.remote_host_field.setPlaceholderText("user@server, server:2222, or an ~/.ssh/config alias")
        self.remote_host_field.setAccessibleName("Remote compute server for SSH")
        self.remote_connect_button = QPushButton("Connect")
        host_row = QHBoxLayout()
        host_row.addWidget(self.remote_host_field, 1)
        host_row.addWidget(self.remote_connect_button)
        form.addRow("Server", host_row)
        self.remote_autoconnect_box = QCheckBox("Connect automatically when Onionmind opens")
        self.remote_autoconnect_box.setToolTip(
            "Off by default. While off, opening Onionmind contacts nothing; while on, "
            "it opens the SSH connection to this server at every launch."
        )
        self.remote_autoconnect_box.setChecked(bool(settings["autoconnect"]))
        form.addRow("", self.remote_autoconnect_box)
        outer.addLayout(form)
        self.remote_feedback = QLabel()
        self.remote_feedback.setWordWrap(True)
        self.remote_feedback.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self.remote_feedback)
        needs = QLabel(
            "Needs: key-based SSH login (Onionmind cannot type a password - run "
            "`ssh-copy-id user@server` once), and Ollama with your models running on "
            "the server. Keep its Ollama on 127.0.0.1 - the tunnel reaches it there, "
            "and it has no password of its own. The SSH connection goes direct, not "
            "over Tor: it is your own server, and it is opened only when you connect."
        )
        needs.setObjectName("meta")
        needs.setWordWrap(True)
        outer.addWidget(needs)
        outer.addStretch(1)
        if window is not None:
            self.remote_connect_button.clicked.connect(self._remote_connect_clicked)
            self.remote_host_field.returnPressed.connect(self._remote_connect_clicked)
            self.remote_host_field.editingFinished.connect(self._remote_host_edited)
            self.remote_autoconnect_box.toggled.connect(self._remote_autoconnect_toggled)
            window.remoteChanged.connect(self._sync_remote)
        else:
            self.remote_connect_button.setEnabled(False)
        self._sync_remote()
        return page

    def _remote_host_edited(self) -> None:
        window = self._window()
        dc = getattr(window, "desktop_core", None)
        text = self.remote_host_field.text().strip()
        if window is None or dc is None:
            return
        target = dc.parse_ssh_target(text)
        if text and target is None:
            self.remote_feedback.setText("Enter a server as user@host, host:port, or an ~/.ssh/config alias.")
            return
        window.set_remote_setting("host", target.label if target else "")
        if target is None and self.remote_autoconnect_box.isChecked():
            self.remote_autoconnect_box.setChecked(False)

    def _remote_autoconnect_toggled(self, checked: bool) -> None:
        window = self._window()
        if window is not None:
            window.set_remote_setting("autoconnect", checked)

    def _remote_connect_clicked(self) -> None:
        window = self._window()
        if window is None:
            return
        if window.remote_phase in ("on", "connecting"):
            window.disconnect_remote()
            return
        self._remote_host_edited()
        window.connect_remote(self.remote_host_field.text().strip())

    def _sync_remote(self) -> None:
        window = self._window()
        if window is None:
            self.remote_feedback.setText("Remote compute is unavailable in this view.")
            return
        phase = window.remote_phase
        self.remote_connect_button.setText(
            {"on": "Disconnect", "connecting": "Cancel"}.get(phase, "Connect"))
        self.remote_connect_button.setAccessibleName(
            {"on": "Disconnect remote compute", "connecting": "Cancel connecting"}.get(
                phase, "Connect to the remote compute server"))
        self.remote_host_field.setEnabled(phase not in ("on", "connecting"))
        text = window.remote_message or "Not connected - models run on this machine."
        prefix = {"on": "● ", "connecting": "… ", "error": "⚠ "}.get(phase, "")
        self.remote_feedback.setText(prefix + text)

    def _privacy_page(self, preferences: dict[str, Any], agent_limitation: str) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        heading = QLabel("Boundaries")
        heading.setObjectName("brand")
        outer.addWidget(heading)
        form = QFormLayout()
        form.setHorizontalSpacing(18)
        window = self._window()
        where = window.inference_location() if window is not None else "on this machine"
        form.addRow("Inference", QLabel(f"Onionmind inference {where}"))
        tor = QLabel(
            "Chat search and the coding agent both leave over Tor; a failed Tor "
            "check never falls back to a direct request."
        )
        tor.setWordWrap(True)
        form.addRow("Tor", tor)
        agent = QLabel(_brand_runtime_text(agent_limitation))
        agent.setWordWrap(True)
        form.addRow("Agent", agent)
        form.addRow("Telemetry", QLabel("No Onionmind telemetry or account"))
        outer.addLayout(form)

        on_disk_heading = QLabel("What is written to disk")
        on_disk_heading.setObjectName("brand")
        outer.addWidget(on_disk_heading)
        self.save_history_box = QCheckBox("Save conversations to this machine")
        self.save_history_box.setAccessibleName("Save conversations to this machine")
        self.save_history_box.setToolTip(
            "Off: conversations stay in memory only and are gone when the window "
            "closes. Sessions already saved are kept until you delete them."
        )
        self.save_history_box.setChecked(bool(preferences.get("save_history", True)))
        outer.addWidget(self.save_history_box)
        self.draft_box = QCheckBox("Remember an unsent message between launches")
        self.draft_box.setAccessibleName("Remember an unsent composer draft")
        self.draft_box.setToolTip(
            "Off: whatever is typed but not sent is dropped when Onionmind closes."
        )
        self.draft_box.setChecked(bool(preferences.get("remember_drafts", True)))
        outer.addWidget(self.draft_box)
        self.save_history_box.toggled.connect(
            lambda checked: self._set_preference("save_history", checked)
        )
        self.draft_box.toggled.connect(
            lambda checked: self._set_preference("remember_drafts", checked)
        )
        outer.addWidget(self._lock_section())
        outer.addStretch(1)
        return page

    def _updates_page(self, update_bridge: Optional[UpdateBridge]) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        updates_heading = QLabel("Updates")
        updates_heading.setObjectName("brand")
        outer.addWidget(updates_heading)
        updates_row = QHBoxLayout()
        version = QLabel(
            update_bridge.revision_label() if update_bridge and update_bridge.available
            else "Development copy — the updater applies to an installed Onionmind bundle"
        )
        version.setWordWrap(True)
        updates_row.addWidget(version, 1)
        self.check_updates_button = QPushButton("Check for updates")
        self.check_updates_button.setAccessibleName("Check for Onionmind updates over Tor")
        self.check_updates_button.clicked.connect(self._check_updates)
        updates_row.addWidget(self.check_updates_button)
        outer.addLayout(updates_row)
        boundary = QLabel("The check and the download both travel over Tor; there is no direct-network fallback.")
        boundary.setObjectName("meta")
        boundary.setWordWrap(True)
        outer.addWidget(boundary)
        self.autocheck_box = QCheckBox("Check automatically over Tor (at most every 12 hours)")
        self.autocheck_box.setAccessibleName("Permission for automatic Onionmind update checks over Tor")
        self.autocheck_box.setToolTip(
            "Off by default. While off, the updater contacts nothing until you press "
            "Check for updates; while on, Onionmind looks for updates over Tor for as "
            "long as it stays open."
        )
        window = self._window()
        if window is not None:
            self.autocheck_box.setChecked(window.update_permission_enabled())
        self.autocheck_box.toggled.connect(self._permission_toggled)
        outer.addWidget(self.autocheck_box)
        self.update_feedback = QLabel()
        self.update_feedback.setObjectName("meta")
        self.update_feedback.setWordWrap(True)
        outer.addWidget(self.update_feedback)
        self.update_progress = QProgressBar()
        self.update_progress.setTextVisible(False)
        self.update_progress.setFixedHeight(6)
        self.update_progress.hide()
        outer.addWidget(self.update_progress)
        outer.addStretch(1)
        return page

    def _storage_page(self, data_root: Path) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        heading = QLabel("Storage")
        heading.setObjectName("brand")
        outer.addWidget(heading)
        form = QFormLayout()
        form.setHorizontalSpacing(18)
        storage = QLabel(str(data_root))
        storage.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        storage.setWordWrap(True)
        form.addRow("Session storage", storage)
        outer.addLayout(form)
        note = QLabel(
            "Conversations, settings, and the saved window layout live in that "
            "folder and nowhere else."
        )
        note.setObjectName("meta")
        note.setWordWrap(True)
        outer.addWidget(note)
        wipe_row = QHBoxLayout()
        wipe_note = QLabel("Remove every saved and archived conversation from this machine.")
        wipe_note.setWordWrap(True)
        wipe_row.addWidget(wipe_note, 1)
        self.wipe_button = QPushButton("Delete all conversations")
        self.wipe_button.setAccessibleName("Delete every saved Onionmind conversation")
        self.wipe_button.clicked.connect(self._delete_all_sessions)
        wipe_row.addWidget(self.wipe_button)
        outer.addLayout(wipe_row)

        wipe_heading = QLabel("Wipe this machine")
        wipe_heading.setObjectName("brand")
        outer.addWidget(wipe_heading)
        clear_row = QHBoxLayout()
        clear_note = QLabel(
            "Destroys your work on this machine: every conversation, the project "
            "list, and the project folders with every file inside them, plus the "
            "last opened project, any unsent message, the agent's network log, and "
            "Tor's state, caches and logs. Files are overwritten before they are "
            "removed. This cannot be undone."
        )
        clear_note.setWordWrap(True)
        clear_row.addWidget(clear_note, 1)
        self.clear_button = QPushButton("Wipe everything")
        self.clear_button.setAccessibleName(
            "Wipe sessions, projects and their files from this machine"
        )
        self.clear_button.clicked.connect(self._wipe_machine_data)
        clear_row.addWidget(self.clear_button)
        outer.addLayout(clear_row)
        self.clear_on_exit_box = QCheckBox(
            "Wipe automatically every time Onionmind closes"
        )
        self.clear_on_exit_box.setAccessibleName(
            "Wipe sessions, projects and their files automatically when Onionmind closes"
        )
        self.clear_on_exit_box.setToolTip(
            "On: every close destroys the conversations, the project list, and the "
            "project folders themselves - without asking again. Off: nothing is "
            "removed until you press the button above."
        )
        window = self._window()
        self.clear_on_exit_box.setChecked(
            bool(getattr(window, "preferences", {}).get("clear_on_exit", False))
            if window is not None
            else False
        )
        self.clear_on_exit_box.toggled.connect(self._clear_on_exit_toggled)
        outer.addWidget(self.clear_on_exit_box)
        limit = QLabel(
            "Overwriting is best effort: on an SSD or a copy-on-write filesystem "
            "the old blocks can survive. Full-disk encryption, or the Matchstick "
            "RAM-only image, is the guarantee."
        )
        limit.setObjectName("meta")
        limit.setWordWrap(True)
        outer.addWidget(limit)
        outer.addStretch(1)
        return page

    _WIPE_DETAIL = (
        "Removes every saved and archived conversation, the remembered projects, "
        "and each project folder with all of its files - overwritten first, then "
        "deleted. The last opened project, any unsent message, and the agent's "
        "network log go too, along with Tor's state file, cached consensus and "
        "descriptors, and any Tor log on this machine.\n\n"
        "A drive root, your home folder, Onionmind's own data or program folder, "
        "and linked folders are skipped.\n\n"
        "Overwriting is best effort: on an SSD or a copy-on-write filesystem the "
        "original blocks can survive."
    )

    def _wipe_machine_data(self) -> None:
        window = self._window()
        if window is None:
            self.storage_feedback.setText(
                "The workbench window is unavailable; nothing was wiped."
            )
            return
        if not window._confirm_permanent_deletion(
            title="Wipe everything from this machine",
            text="Permanently destroy every conversation and every project folder?",
            detail=self._WIPE_DETAIL,
            confirm_label="Wipe everything",
        ):
            self.storage_feedback.setText("Nothing was wiped.")
            return
        result = window.wipe_machine_data()
        skipped = result.get("skipped", 0)
        self.storage_feedback.setText(
            f"Wiped {result.get('sessions', 0)} conversation(s), "
            f"{result.get('projects', 0)} project folder(s) and "
            f"{result.get('tor', 0)} Tor state file(s) from this machine."
            + (f" {skipped} protected path(s) were skipped." if skipped else "")
        )

    def _clear_on_exit_toggled(self, checked: bool) -> None:
        """Arming an unattended folder-destroying wipe is itself confirmed once;
        after that every close runs it without asking, which is the point."""
        window = self._window()
        if checked and window is not None:
            if not window._confirm_permanent_deletion(
                title="Wipe on every close",
                text="Destroy every conversation and every project folder each time Onionmind closes?",
                detail=self._WIPE_DETAIL,
                confirm_label="Wipe on close",
            ):
                self.clear_on_exit_box.setChecked(False)
                self.storage_feedback.setText("Automatic wipe stays off.")
                return
        self._set_preference("clear_on_exit", checked)
        self.storage_feedback.setText(
            "Every close will wipe conversations and project folders."
            if checked
            else "Automatic wipe is off."
        )

    def _delete_all_sessions(self) -> None:
        window = self._window()
        if window is None:
            self.storage_feedback.setText(
                "The workbench window is unavailable; nothing was deleted."
            )
            return
        removed = window.delete_all_sessions()
        self.storage_feedback.setText(
            f"Deleted {removed} saved conversation(s) from this machine."
            if removed
            else "There were no saved conversations to delete."
        )


    def _window(self) -> Any:
        parent = self.parent()
        return parent if isinstance(parent, OnionmindWindow) else None

    def desktop_core(self) -> Any:
        window = self._window()
        return getattr(window, "desktop_core", None)

    def _context_tokens(self, value: Any) -> int:
        parser = _optional_callable(self.desktop_core(), "parse_context_window")
        parsed = parser(value) if callable(parser) else None
        if parsed is None:
            try:
                parsed = int(_as_text(value).strip() or 0)
            except ValueError:
                parsed = 0
        return parsed if parsed and parsed > 0 else 16384

    def _describe_context(self, text: str) -> None:
        """Say what the typed value means, and warn when it is out of range -
        the field itself refuses nothing."""
        parser = _optional_callable(self.desktop_core(), "parse_context_window")
        tokens = parser(text) if callable(parser) else self._context_tokens(text)
        if tokens is None:
            self.context_hint.setStyleSheet(f"color: {theme_tokens()['bad']};")
            self.context_hint.setText("Enter a token count, for example 24000 or 24k.")
            return
        warner = _optional_callable(self.desktop_core(), "context_window_warning")
        warning = warner(tokens) if callable(warner) else ""
        self.context_hint.setStyleSheet(f"color: {theme_tokens()['bad']};" if warning else "")
        self.context_hint.setText(
            warning or f"{tokens:,} tokens · type any count, or pick a preset from the list"
        )

    def _commit_context(self) -> None:
        parser = _optional_callable(self.desktop_core(), "parse_context_window")
        tokens = parser(self.context_combo.currentText()) if callable(parser) else None
        if tokens is None:
            self._describe_context(self.context_combo.currentText())
            return
        self._set_preference("context_window", tokens)
        self.context_combo.setCurrentText(str(tokens))
        self._describe_context(str(tokens))

    def _set_preference(self, key: str, value: Any) -> None:
        window = self._window()
        if window is not None:
            window.set_preference(key, value)

    def _reset_window_layout(self) -> None:
        window = self._window()
        if window is None:
            self.window_feedback.setText(
                "The workbench window is unavailable; the layout was not reset."
            )
            return
        window.reset_window_layout()
        self.window_feedback.setText(
            "Window layout reset: size, panes, and pane widths are back to the default workbench."
        )

    def _permission_toggled(self, checked: bool) -> None:
        window = self._window()
        if window is None:
            return
        window.set_update_permission(checked)
        if checked:
            self.update_feedback.setText(
                "Automatic checks are on: Onionmind will look for updates over Tor "
                "while it runs - never over a direct connection."
            )
        else:
            self.update_feedback.setText(
                "Automatic checks are off: nothing is contacted until you press Check for updates."
            )

    def _check_updates(self) -> None:
        bridge = self.update_bridge
        window = self._window()
        if bridge is None or not bridge.available or window is None:
            return
        # The pill can read "Running" on a Tor that has never been verified as
        # Tor - a listening SOCKS port is not proof. The check needs a verified
        # circuit, so ask for one here rather than refusing and pointing the
        # user at a control that no longer exists.
        probe = getattr(window.core, "tor_proxy_port", None)
        listening = probe() if callable(probe) else None
        if not listening and bridge.tor_port() is None:
            self.update_feedback.setText(
                "Tor is not up. Allow Tor search on a chat turn to start it, then check "
                "again - updates never use a direct connection."
            )
            return
        self.check_updates_button.setEnabled(False)
        self.update_feedback.setText("Checking for updates through Tor…")
        window._egress("Tor", "Update check · github.com")

        def check_job(signals: WorkerSignals) -> Any:
            del signals
            if bridge.tor_port() is None:
                verify = getattr(window.core, "tor_check", None)
                if not callable(verify):
                    raise RuntimeError("This Onionmind core cannot verify a Tor circuit.")
                try:
                    verify()
                except SystemExit as exc:
                    # tor_check() exits the process on the CLI; in the desktop
                    # app that would kill a worker thread without a word.
                    raise RuntimeError(_as_text(exc) or "Tor could not be verified.") from None
                if bridge.tor_port() is None:
                    raise RuntimeError(
                        "The local proxy did not verify as Tor; refusing a direct update check."
                    )
            return bridge.check()

        def wire_check(worker: SafeWorker) -> None:
            worker.signals.result.connect(self._update_check_done)
            worker.signals.error.connect(self._update_check_failed)

        window._start_worker(check_job, wire_check)

    def _update_check_done(self, manifest: Any) -> None:
        self.check_updates_button.setEnabled(True)
        bridge = self.update_bridge
        window = self._window()
        if window is not None:
            window.note_update_check(manifest)
        self.update_manifest = manifest
        state = bridge.desktop_core.update_state(bridge.revision(), manifest)
        short = bridge.desktop_core.short_revision(manifest.revision)
        if state == "available":
            self.update_feedback.setText(
                f"Version {manifest.version} (revision {short}) is available. "
                "The download runs through Tor and is verified against its SHA-256."
            )
            self._reveal_download()
        elif state == "current":
            self.update_feedback.setText(f"Onionmind is up to date (revision {short}).")
        else:
            self.update_feedback.setText("The feed could not be compared with this installation.")

    def _update_check_failed(self, message: str) -> None:
        self.check_updates_button.setEnabled(True)
        self.update_feedback.setText(message)

    def _reveal_download(self) -> None:
        box = self.findChild(QDialogButtonBox)
        if box is None or getattr(self, "_download_button", None) is not None:
            return
        self._download_button = box.addButton(
            "Download and install", QDialogButtonBox.ButtonRole.ActionRole
        )
        self._download_button.setAccessibleName("Download the Onionmind update over Tor")
        self._download_button.clicked.connect(self._download_update)

    def _download_update(self) -> None:
        bridge = self.update_bridge
        window = self._window()
        manifest = self.update_manifest
        if bridge is None or window is None or manifest is None:
            return
        self._download_button.setEnabled(False)
        self.check_updates_button.setEnabled(False)
        self.update_progress.setRange(0, 100)
        self.update_progress.setValue(0)
        self.update_progress.show()
        self.update_feedback.setText("Downloading the update through Tor…")
        self._update_stop = threading.Event()
        manifest_ref = manifest

        def download_job(signals: WorkerSignals) -> str:
            def progress(fraction: Optional[float], note: str) -> None:
                if fraction is None:
                    signals.text.emit(note)
                else:
                    signals.progress.emit(float(fraction), note)

            return str(bridge.download(manifest_ref, progress, self._update_stop))

        worker = window._start_worker(download_job)
        worker.signals.progress.connect(self._update_download_progress)
        worker.signals.text.connect(self._update_download_note)
        worker.signals.result.connect(self._update_download_done)
        worker.signals.error.connect(self._update_download_failed)

    def _update_download_progress(self, fraction: float, note: str) -> None:
        self.update_progress.setValue(int(max(0.0, min(1.0, fraction)) * 100))
        self.update_feedback.setText(note)

    def _update_download_note(self, note: str) -> None:
        self.update_feedback.setText(note)

    def _update_download_done(self, staging: str) -> None:
        self.update_progress.hide()
        self.update_staging = staging
        self._offer_restart(staging)
        window = self._window()
        if window is not None:
            window.show_update_ready()

    def _update_download_failed(self, message: str) -> None:
        self.update_progress.hide()
        self.update_feedback.setText(message)
        if getattr(self, "_download_button", None) is not None:
            self._download_button.setEnabled(True)
        self.check_updates_button.setEnabled(True)

    def _offer_restart(self, staging: str) -> None:
        self.update_staging = staging
        self.update_feedback.setText(
            "The update is downloaded, verified, and staged. Restart Onionmind to finish installing it."
        )
        box = self.findChild(QDialogButtonBox)
        if box is None or getattr(self, "_restart_button", None) is not None:
            return
        self._restart_button = box.addButton(
            "Restart and update", QDialogButtonBox.ButtonRole.ActionRole
        )
        self._restart_button.setAccessibleName("Restart Onionmind to install the update")
        self._restart_button.clicked.connect(self._restart_for_update)

    def _restart_for_update(self) -> None:
        bridge = self.update_bridge
        window = self._window()
        if bridge is None or window is None or not self.update_staging:
            return
        window.restart_for_update(self.update_staging)

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
    featuresChanged = Signal(object)
    remoteChanged = Signal()

    def __init__(self, core: Any, desktop_core: Any = None, demo: bool = False) -> None:
        super().__init__()
        self.core = core
        self.desktop_core = desktop_core
        self.demo = demo
        self._workers: set[SafeWorker] = set()
        data_location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
        self.data_root = Path(data_location or (Path.home() / ".onionmind")) / "desktop"
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.settings_bridge = SettingsBridge(desktop_core, self.data_root)
        self.session_bridge = SessionBridge(desktop_core, self.data_root / "sessions")
        self.workspace_bridge = WorkspaceBridge(desktop_core)
        self.harness_bridge = HarnessBridge(desktop_core, core)
        self.update_bridge = UpdateBridge(core, desktop_core)
        self.settings_data = {} if demo else self.settings_bridge.load()
        self.features: dict[str, bool] = self._load_features()
        self._features_popover: Optional[FeaturesPopover] = None
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
        self.tor_probe_generation = 0
        self.tor_phase = "off"
        self.tor_stop_event: Optional[threading.Event] = None
        self.tor_in_use = False
        # Remote compute: the live `ssh -L` tunnel, and its phase for the UI
        # ("off" | "connecting" | "on" | "error").
        self.remote_tunnel: Any = None
        self._remote_pending: Any = None
        self.remote_phase = "off"
        self.remote_message = ""
        self._project_delete_pending: Optional[str] = None
        self._pending_redirect = False
        self._catalog_specs: tuple[Optional[int], Optional[int]] = (None, None)
        self._rail_requested = True
        self._inspector_requested = True
        self._model_dialog: Optional[ModelManagerDialog] = None
        self._update_timer: Optional[QTimer] = None
        # v2 run state: the webAsk question, the structured agent stream, and
        # open timeline steps. All empty while the features are off.
        self._web_ask_generation = 0
        self._web_ask_card: Optional[QWidget] = None
        self._web_ask_task = ""
        self._agent_stream = False
        self._agent_buffer = b""
        self._agent_rules: Optional[dict[str, Any]] = None
        self._agent_steps: dict[str, Any] = {}
        self._agent_permission: Optional[dict[str, Any]] = None
        self._agent_cards: list[QWidget] = []
        self._agent_outcome: Optional[str] = None
        self._agent_session_id: Optional[str] = None
        self._resume_cards: list[Any] = []
        self._run_allow_all = False
        self._open_steps: dict[str, Any] = {}
        # v2 model power and live numbers - only ever filled from real probes.
        self._model_power: dict[str, Any] = {}
        self._model_loading: Optional[str] = None
        self._live_tps: Optional[float] = None
        self._turn_reasoning = 0.0
        self._last_stats: dict[str, Any] = {}
        self._context_used_real: Optional[int] = None
        self.egress_path = self.data_root / "network-log.jsonl"
        self._egress_counts: Optional[dict[str, int]] = None
        self._turn_searches: list[dict[str, Any]] = []
        self._searching = False
        self._pulling: Optional[str] = None
        self._edit_from: Optional[int] = None
        self._revert_record: Any = None
        self._fault: Optional[str] = None
        self._fault_card: Optional[NoticeCard] = None
        self._no_models = False
        self._starters: Optional[QWidget] = None
        self._last_agent_task: Optional[str] = None
        self._last_input = time.monotonic()
        self._tray: Any = None
        self.last_notification: Optional[tuple[str, str]] = None
        self._build_window()
        self.preferences = self._load_preferences()
        self._apply_preferences()
        self._install_shortcuts()
        self.lock_overlay = LockOverlay(self, self._verify_passphrase)
        self.lock_overlay.unlocked.connect(lambda: self.set_status("Unlocked."))
        self.setup_overlay = SetupOverlay(self)
        self.idle_timer = QTimer(self)
        self.idle_timer.setInterval(15_000)
        self.idle_timer.timeout.connect(self._check_idle)
        self.housekeeping_timer = QTimer(self)
        self.housekeeping_timer.setInterval(3600 * 1000)
        self.housekeeping_timer.timeout.connect(self._auto_delete_old)
        self._idle_tracking = False
        self._apply_features()
        if not demo and self.feature("setup") and not self.settings_data.get("setup_done"):
            QTimer.singleShot(0, self.open_setup)
        if demo:
            self._populate_demo()
        else:
            self._restore_state()
            # A staged update is local state, not network: surface it without
            # requiring the automatic-check permission.
            if self.update_bridge.available and self.update_bridge.pending() is not None:
                self.show_update_ready()
            if self.update_permission_enabled():
                self._start_update_timer()
            # Connecting at launch is an opt-in the user ticked in Settings;
            # without it, opening Onionmind still contacts nothing.
            autoconnect = self.remote_settings()["autoconnect"]
            self._probe_services(models=not autoconnect)
            if autoconnect:
                self.connect_remote()
            self.tor_liveness_timer = QTimer(self)
            self.tor_liveness_timer.setInterval(2500)
            self.tor_liveness_timer.timeout.connect(self._poll_tor_liveness)
            self.tor_liveness_timer.timeout.connect(self._poll_remote_liveness)
            self.tor_liveness_timer.start()

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
        self.toolbar_widget = toolbar
        toolbar_layout = QHBoxLayout(toolbar)
        self.toolbar_layout = toolbar_layout
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
        _themed_icon(rail_toggle, "rail")
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
        self.boundary_button = BoundaryButton()
        self.boundary_button.clicked.connect(lambda: self.toggle_boundary())
        self.boundary_button.hide()
        toolbar_layout.addWidget(self.boundary_button)

        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(190)
        self.model_combo.setMaximumWidth(260)
        self.model_combo.setAccessibleName("Onionmind model")
        self.model_combo.setToolTip("Choose the Onionmind model for the next run")
        self.model_combo.currentIndexChanged.connect(self._model_changed)
        self.model_combo.activated.connect(lambda _index: self._model_picked())
        toolbar_layout.addWidget(self.model_combo)
        toolbar_layout.addWidget(self._build_context_slider())
        self.model_status = StatusPill("Model", "Checking", "busy")
        toolbar_layout.addWidget(self.model_status)
        self.tor_status = StatusActionButton("Tor", "Off", "idle", "Turn on")
        self.tor_status.setToolTip(
            "Turn Onionmind's Tor proxy on or off. The same button always shows its current state."
        )
        self.tor_status.clicked.connect(self._toggle_tor)
        toolbar_layout.addWidget(self.tor_status)

        self.lock_button = QToolButton()
        self.lock_button.setObjectName("bareButton")
        _themed_icon(self.lock_button, "lock")
        self.lock_button.setToolTip("Lock Onionmind")
        self.lock_button.setAccessibleName("Lock Onionmind")
        self.lock_button.clicked.connect(lambda: self.lock_now())
        self.lock_button.hide()
        toolbar_layout.addWidget(self.lock_button)

        self.palette_button = QPushButton("⌘K" if sys.platform == "darwin" else "Ctrl K")
        self.palette_button.setObjectName("chipButton")
        self.palette_button.setToolTip("Command palette (Ctrl+K)")
        self.palette_button.setAccessibleName("Open the command palette")
        self.palette_button.clicked.connect(self.open_palette)
        self.palette_button.hide()
        toolbar_layout.addWidget(self.palette_button)

        self.features_button = QToolButton()
        self.features_button.setObjectName("bareButton")
        _themed_icon(self.features_button, "sparkle")
        self.features_button.setToolTip("New features. Each stays hidden until you turn it on.")
        self.features_button.setAccessibleName("New features")
        self.features_button.clicked.connect(self.open_features_popover)
        self.features_button.setVisible(bool(_feature_catalog(self.desktop_core)))
        toolbar_layout.addWidget(self.features_button)

        terminal_toggle = QToolButton()
        terminal_toggle.setObjectName("bareButton")
        terminal_toggle.setCheckable(True)
        terminal_toggle.setChecked(True)
        _themed_icon(terminal_toggle, "terminal")
        terminal_toggle.setToolTip("Toggle terminal drawer (Ctrl+`)")
        terminal_toggle.setAccessibleName("Toggle terminal drawer")
        terminal_toggle.clicked.connect(self.toggle_terminal)
        self.terminal_toggle = terminal_toggle
        toolbar_layout.addWidget(terminal_toggle)
        inspector_toggle = QToolButton()
        inspector_toggle.setObjectName("bareButton")
        inspector_toggle.setCheckable(True)
        inspector_toggle.setChecked(True)
        _themed_icon(inspector_toggle, "inspector")
        inspector_toggle.setToolTip("Toggle inspector (Ctrl+Shift+I)")
        inspector_toggle.setAccessibleName("Toggle context, changes, and activity inspector")
        inspector_toggle.clicked.connect(self.toggle_inspector)
        self.inspector_toggle = inspector_toggle
        toolbar_layout.addWidget(inspector_toggle)
        root_layout.addWidget(toolbar)
        self.boundary_panel = BoundaryPanel()
        self.boundary_panel.close_button.clicked.connect(lambda: self.toggle_boundary(False))
        root_layout.addWidget(self.boundary_panel)

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setHandleWidth(1)
        self.left_rail = LeftRail()
        self.left_rail.newTaskRequested.connect(self.new_task)
        self.left_rail.addSessionRequested.connect(self.add_session)
        self.left_rail.openFolderRequested.connect(self.open_folder)
        self.left_rail.projectSelected.connect(self.select_workspace)
        self.left_rail.removeProjectRequested.connect(self.remove_project_from_menu)
        self.left_rail.deleteProjectRequested.connect(self.delete_project_from_machine)
        self.left_rail.sessionSelected.connect(self.load_session)
        self.left_rail.modelsRequested.connect(self.open_model_manager)
        self.left_rail.settingsRequested.connect(self.open_settings)
        self.left_rail.exportRequested.connect(self.export_conversation)
        self.left_rail.archiveRequested.connect(self.archive_session)
        self.left_rail.deleteSessionRequested.connect(self.delete_session_from_machine)
        self.left_rail.renameSessionRequested.connect(self.rename_session)
        self.left_rail.hitSelected.connect(self.load_session)
        self.left_rail.session_filter.textChanged.connect(self._session_search)
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
        # v2 errors / empty: a red fault card or the no-model banner sits here.
        self.fault_host = QWidget()
        self.fault_host.setObjectName("faultHost")
        self.fault_layout = QVBoxLayout(self.fault_host)
        self.fault_layout.setContentsMargins(12, 8, 12, 0)
        self.fault_host.hide()
        center_layout.addWidget(self.fault_host)
        self.composer_frame = self._build_composer()
        center_layout.addWidget(self.composer_frame)
        self.retry_button = QPushButton("Retry last turn")
        self.retry_button.setToolTip("Ask the local model again with the same conversation")
        self.retry_button.setAccessibleName("Retry the failed chat turn")
        self.retry_button.clicked.connect(self._retry_last_turn)
        self.retry_button.hide()
        center_layout.addWidget(self.retry_button)
        self.main_splitter.addWidget(center)

        self.inspector = InspectorPane()
        self.inspector.refreshRequested.connect(self.refresh_workspace)
        self.inspector.run_panel.stopRequested.connect(self.stop_active)
        self.inspector.netlog_button.clicked.connect(self.open_network_log)
        self.inspector.revertRequested.connect(self._revert_paths)
        self.inspector.hunksRequested.connect(self._choose_hunks)
        self.inspector.undoRequested.connect(self._undo_revert)
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
        self.update_status = QPushButton("Updates…")
        self.update_status.setObjectName("updateStatus")
        self.update_status.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_status.setFlat(True)
        self.update_status.clicked.connect(self.open_settings)
        self._set_update_notice(None)
        # v2 modelPlus: live speed, graphics memory and context, hidden until on.
        self.speed_status = QLabel("Idle")
        self.vram_status = QLabel("")
        self.ctx_status = QLabel("")
        for label, name in ((self.speed_status, "Generation speed"), (self.vram_status, "Graphics memory"),
                            (self.ctx_status, "Context in use")):
            label.setObjectName("meta")
            label.setAccessibleName(name)
            label.hide()
            self.statusBar().addPermanentWidget(label)
        self.statusBar().addPermanentWidget(self.update_status)
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
        # v2 msgActions: editing an earlier message.
        self.edit_banner = QFrame()
        self.edit_banner.setObjectName("banner")
        edit_layout = QHBoxLayout(self.edit_banner)
        edit_layout.setContentsMargins(9, 4, 6, 4)
        edit_text = QLabel("Editing an earlier message. Sending replaces everything after it in this session.")
        edit_text.setWordWrap(True)
        edit_cancel = QPushButton("Cancel")
        edit_cancel.setAccessibleName("Cancel editing the earlier message")
        edit_cancel.clicked.connect(lambda: self._cancel_edit())
        edit_layout.addWidget(edit_text, 1)
        edit_layout.addWidget(edit_cancel)
        self.edit_banner.hide()
        outer.addWidget(self.edit_banner)
        outer.addWidget(self.composer, 1)
        self.attachment_row = QWidget()
        attachment_layout = QHBoxLayout(self.attachment_row)
        attachment_layout.setContentsMargins(0, 0, 0, 0)
        self.attachment_label = QLabel()
        self.attachment_label.setObjectName("attachmentLabel")
        clear = QToolButton()
        clear.setObjectName("bareButton")
        _themed_icon(clear, "close")
        clear.setToolTip("Remove all attachments")
        clear.setAccessibleName("Remove all attachments")
        clear.clicked.connect(self.clear_attachments)
        attachment_layout.addWidget(self.attachment_label)
        attachment_layout.addWidget(clear)
        attachment_layout.addStretch(1)
        self.attachment_row.hide()
        outer.addWidget(self.attachment_row)

        # v2 contextMeter warning row: hidden until the session nears its limit.
        self.context_warning = QWidget()
        warning_layout = QHBoxLayout(self.context_warning)
        warning_layout.setContentsMargins(0, 0, 0, 0)
        self.context_warning_label = QLabel()
        self.context_warning_label.setObjectName("warning")
        self.context_warning_label.setWordWrap(True)
        context_new_task = QPushButton("New task")
        context_new_task.setAccessibleName("Start a new task with an empty context")
        context_new_task.clicked.connect(lambda: self.new_task())
        warning_layout.addWidget(self.context_warning_label, 1)
        warning_layout.addWidget(context_new_task)
        self.context_warning.hide()
        outer.addWidget(self.context_warning)
        self.composer_outer = outer

        controls = QHBoxLayout()
        controls.setSpacing(7)
        self.composer_controls = controls
        attach = QToolButton()
        _themed_icon(attach, "attach")
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
            "Approvals are on: the agent asks before a protected action, and where there is nobody to ask it stops instead of continuing"
        )
        self.approval_state.setAccessibleName(
            "Onionmind Agent protected actions stop safely"
        )
        controls.addWidget(self.approval_state)
        self.yolo_consent = QCheckBox("YOLO: run without asking")
        self.yolo_consent.setChecked(False)
        self.yolo_consent.setToolTip(
            "Auto-approve file edits and shell commands for this run. The network "
            "boundary does not move: commands that cannot be proxied stay refused, "
            "and everything still leaves through Tor or not at all."
        )
        self.yolo_consent.setAccessibleName(
            "Run Onionmind Agent without approval prompts"
        )
        self.yolo_consent.toggled.connect(self._yolo_toggled)
        controls.addWidget(self.yolo_consent)
        self.search_consent = QCheckBox("Allow Tor search this turn")
        self.search_consent.setChecked(False)
        self.search_consent.setToolTip(
            "One-turn permission. If needed, Onionmind starts Tor in the background without opening Tor Browser."
        )
        self.search_consent.setAccessibleName("Allow Tor web search for the next Chat turn only")
        controls.addWidget(self.search_consent)
        # v2 presets: the system prompt chip.
        self.preset_chip = QPushButton("Default")
        self.preset_chip.setObjectName("chipButton")
        self.preset_chip.setToolTip("Preset: changes how Onionmind answers")
        self.preset_chip.setAccessibleName("Preset")
        self.preset_menu = QMenu(self)
        self.preset_chip.setMenu(self.preset_menu)
        self.preset_chip.hide()
        controls.addWidget(self.preset_chip)
        self.disclosure = QLabel()
        self.disclosure.setObjectName("disclosure")
        self.disclosure.setWordWrap(True)
        controls.addWidget(self.disclosure, 1)
        # v2 ladder: the model is loading; v2 contextMeter: the 44x4 bar.
        self.model_loading = QProgressBar()
        self.model_loading.setObjectName("meter")
        self.model_loading.setRange(0, 0)
        self.model_loading.setFixedWidth(56)
        self.model_loading.setAccessibleName("Model loading")
        self.model_loading.hide()
        controls.addWidget(self.model_loading)
        self.context_box = QWidget()
        context_layout = QHBoxLayout(self.context_box)
        context_layout.setContentsMargins(0, 0, 0, 0)
        context_layout.setSpacing(6)
        self.context_meter = QProgressBar()
        self.context_meter.setObjectName("meter")
        self.context_meter.setFixedSize(44, 4)
        self.context_meter.setTextVisible(False)
        self.context_meter.setRange(0, 1000)
        self.context_meter.setAccessibleName("Context window in use")
        self.context_meter_label = QLabel("")
        self.context_meter_label.setObjectName("meta")
        context_layout.addWidget(self.context_meter, 0, Qt.AlignmentFlag.AlignVCenter)
        context_layout.addWidget(self.context_meter_label)
        self.context_box.hide()
        controls.addWidget(self.context_box)
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
            ("Ctrl+,", self.open_settings),
            ("Ctrl+`", lambda: self.toggle_terminal(not self.terminal.isVisible())),
            ("Ctrl+Shift+I", lambda: self.toggle_inspector(not self.inspector.isVisible())),
            ("Escape", self.stop_active),
            # v2: each acts only while its feature is on.
            ("Ctrl+K", lambda: self.open_palette() if self.feature("palette") else None),
            ("Ctrl+B", lambda: self.toggle_boundary() if self.feature("boundary") else None),
            ("?", self._question_shortcut),
        ]
        self.shortcuts: list[QShortcut] = []
        for sequence, callback in shortcuts:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
            shortcut.activated.connect(callback)
            self.shortcuts.append(shortcut)

    def _restore_state(self) -> None:
        self._restore_window_layout()
        self._apply_startup_preferences()
        model = _as_text(self.settings_data.get("model") or getattr(self.core, "MODEL", "inferno"))
        self.set_model_options([model], model)
        recent = self.settings_data.get("recent_projects") or []
        for path in reversed(recent[:8]):
            if path:
                self.left_rail.add_project(_as_text(path), select=False)
        sessions = self.session_bridge.list()
        self.session_objects = {_as_text(_field(session, "id")): session for session in sessions}
        self.left_rail.set_sessions(self._project_sessions(sessions))
        workspace = _as_text(self.settings_data.get("workspace"))
        if workspace and Path(workspace).is_dir():
            self.select_workspace(workspace)
        else:
            self.new_task(save_current=False)

    def _probe_services(self, models: bool = True) -> None:
        if models:
            self._probe_models()
        self._probe_tor()

    def _probe_models(self) -> None:
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

    def _probe_tor(self) -> None:
        checker = getattr(self.core, "tor_proxy_port", None)
        if not callable(checker):
            self.tor_phase = "error"
            self.tor_status.set_status("Unavailable", "bad")
            _set_tor_action(
                self,
                "Unavailable",
                enabled=False,
                tooltip="This Onionmind build cannot manage a Tor proxy.",
            )
            return
        if not _tor_is_enabled(self.core):
            self._show_local_tor_state(None)
            return
        self.tor_probe_generation += 1
        generation = self.tor_probe_generation
        self.tor_phase = "probing"
        self.tor_status.set_status("Checking…", "warn")
        _set_tor_action(
            self,
            "Turn off",
            tooltip="Stop checking and turn Tor off for Onionmind.",
        )

        def tor_probe(signals: WorkerSignals) -> Any:
            del signals
            port = checker()
            if not port:
                return None
            return self._verify_tor()

        tor_worker = self._start_worker(tor_probe)
        tor_worker.signals.result.connect(
            lambda port, value=generation: self._tor_probe_complete(port, value)
        )
        tor_worker.signals.error.connect(
            lambda message, value=generation: self._tor_probe_failed(message, value)
        )

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
        thread = threading.Thread(
            target=worker.run,
            name="onionmind-desktop-worker",
            daemon=True,
        )
        # Deferred to the next event-loop turn, not started here. Thread.start()
        # returns only once the thread is already running, so a job that finishes
        # without blocking - check() returning "Tor is not up" with no subprocess
        # to spawn - can emit result BEFORE the caller connects to it, and the
        # signal goes nowhere: the UI sits on "Preparing..." with no worker alive
        # and no error. singleShot(0) lets the calling slot finish its connects
        # first, so every call site is safe whether or not it passes setup.
        QTimer.singleShot(0, thread.start)
        return worker

    def _model_probe_complete(self, payload: tuple[str, list[str]]) -> None:
        _backend, models = payload
        self._no_models = not models
        self._sync_no_model_banner()
        if self._fault == "server":
            self._clear_fault()
        current = self.current_model_id()
        self.set_model_options(models or [current], current)
        self.model_status.set_status("Ready", "good")
        where = self.inference_location()
        self.model_status.setToolTip(f"Onionmind inference is ready {where}")
        self.inspector.append_activity(f"Onionmind inference ready {where}")

    def _model_probe_failed(self, message: str) -> None:
        self._report_error(message, "server")
        self.model_status.set_status("Unavailable", "bad")
        self.set_status(message)
        self.inspector.append_activity(f"Onionmind inference unavailable: {message}")

    def _tor_probe_failed(self, message: str, generation: Optional[int] = None) -> None:
        if generation is not None and (
            generation != self.tor_probe_generation or self.tor_phase != "probing"
        ):
            return
        self.tor_phase = "error"
        self.tor_status.set_status("Unavailable", "bad")
        message = _brand_runtime_text(message)
        _set_tor_action(
            self,
            "Retry",
            tooltip=f"Tor could not be verified: {message} Click to try again.",
        )
        self.set_status(f"Tor could not be verified: {message}")
        self.inspector.append_activity("Tor unavailable; protected features remain offline")

    def _tor_probe_complete(self, port: Any, generation: Optional[int] = None) -> None:
        if generation is not None and (
            generation != self.tor_probe_generation or self.tor_phase != "probing"
        ):
            return
        self._show_local_tor_state(port)

    def _show_local_tor_state(self, port: Any) -> None:
        if not _tor_is_enabled(self.core):
            self.tor_phase = "off"
            self.tor_status.set_status("Off", "idle")
            note = (
                "Tor is off for Onionmind. A local proxy is still running elsewhere, but "
                "Onionmind will not use it until you turn Tor on."
                if port
                else "Tor is off for Onionmind. Protected features stay offline until you turn it on."
            )
            self.tor_status.setToolTip(note)
            _set_tor_action(
                self,
                "Turn on",
                tooltip="Allow Onionmind to use Tor and start or reuse a local proxy.",
            )
            return
        managed = getattr(self.core, "_managed_tor_process", None)
        try:
            managed_running = managed is not None and managed.poll() is None
        except Exception:
            managed_running = False
        if managed_running and not port:
            stop = getattr(self.core, "stop_managed_tor", None)
            if callable(stop):
                try:
                    stop()
                except Exception:
                    pass
            managed_running = False
        verified = bool(port and getattr(self.core, "_port", None) == port)
        if verified:
            self.tor_phase = "running"
            self.tor_status.set_status("Ready", "good")
            _set_tor_action(
                self,
                "Turn off",
                tooltip=(
                    "Stop Onionmind's background Tor process."
                    if managed_running
                    else "Disconnect Onionmind from this external Tor proxy; the external process stays running."
                ),
            )
            # Tor being verified is also the self-updater's only window to look
            # for updates, so piggyback the permissioned autocheck on it.
            self._maybe_autocheck_updates()
            if managed_running:
                self.tor_status.setToolTip(
                    "Tor is verified and ready. Onionmind's background "
                    "process is running without a browser or console window. Click to turn it off."
                )
                self.inspector.append_activity("Onionmind-owned background Tor is ready")
            else:
                self.tor_status.setToolTip(
                    "Tor is verified and ready. The proxy was already "
                    "running, so turning Onionmind off will leave that external process alone."
                )
                self.inspector.append_activity("Pre-existing local Tor proxy verified")
        elif port:
            self.tor_phase = "error"
            self.tor_status.set_status("Unavailable", "bad")
            if callable(getattr(self.core, "set_tor_enabled", None)):
                _set_tor_action(
                    self,
                    "Retry",
                    tooltip="A local proxy exists but is not verified as Tor. Click to verify it again.",
                )
            else:
                _set_tor_action(
                    self,
                    "External",
                    enabled=False,
                    tooltip="This proxy is managed outside Onionmind and cannot be stopped here.",
                )
            self.tor_status.setToolTip(
                "A local proxy connection exists, but Onionmind has not verified it as Tor. "
                "Protected features remain offline. Click to try verification again."
            )
            self.inspector.append_activity("Unverified local proxy connection detected")
        else:
            self.tor_phase = "off"
            self.tor_status.set_status("Off", "idle")
            self.tor_status.setToolTip(
                "Tor is off. Use Turn on to start it; a permitted search may also start it "
                "unless you explicitly turned it off. No browser window is opened."
            )
            _set_tor_action(
                self,
                "Turn on",
                tooltip="Start Onionmind's background Tor proxy without opening a browser window.",
            )
            self.inspector.append_activity("Background Tor is off; Chat remains local-only")

    def ensure_tor(self, stop_event=None):
        """Bring Tor up if it is not already, and return the port. Worker-side.

        Honours the core contract underneath: an existing listener is reused
        and never adopted, and no browser window is opened.
        """
        port = getattr(self.core, "_port", None)
        if port:
            return port
        starter = getattr(self.core, "start_tor_hidden", None)
        if not callable(starter):
            raise RuntimeError("This Onionmind build cannot start background Tor.")
        return starter(stop_event=stop_event)

    def _verify_tor(self, stop_event=None) -> Any:
        """Verify and return the core's pinned Tor port. Worker-side."""
        if stop_event is not None and stop_event.is_set():
            raise RuntimeError("Tor check was cancelled.")
        verifier = getattr(self.core, "tor_check", None)
        if not callable(verifier):
            raise RuntimeError("This Onionmind build cannot verify a Tor circuit.")
        verifier()
        verified_port = getattr(self.core, "_port", None)
        if (
            not verified_port
            or not _tor_is_enabled(self.core)
            or (stop_event is not None and stop_event.is_set())
        ):
            raise RuntimeError("Tor check was cancelled or did not verify a circuit.")
        return verified_port

    def announce_tor_starting(self, note: str = "") -> "threading.Event":
        """Flip the control to Checking and return the event that cancels it.

        Called on the GUI thread before the worker that does the starting, so
        the button never sits on Off while Tor is coming up and being verified.
        """
        self.tor_probe_generation += 1
        self.tor_phase = "starting"
        self.tor_stop_event = threading.Event()
        self.tor_status.set_status("Checking…", "warn")
        self.tor_status.setToolTip(
            "Tor is starting or being verified. Click to cancel; no browser window is opened."
        )
        _set_tor_action(
            self,
            "Cancel",
            tooltip="Cancel background Tor startup.",
        )
        self.set_status(note or "Starting background Tor without opening a browser window…")
        self.inspector.append_activity(note or "Background Tor start requested")
        return self.tor_stop_event

    def _toggle_tor(self) -> None:
        """The explicit toolbar action starts Tor, cancels startup, or turns it off.

        Stop is offered in every phase, mid-start included - start_tor_hidden()
        polls the stop event while it waits for the SOCKS port, so cancelling a
        slow bootstrap does not mean waiting out its timeout.
        """
        if self.tor_phase in ("starting", "probing"):
            self.stop_tor("Background Tor start cancelled.")
            return
        if self.tor_phase == "running":
            self.stop_tor()
            return
        self._start_tor_from_toolbar()

    def stop_tor(self, note: str = "") -> None:
        """Turn Tor off for Onionmind, at any point in its life.

        Only ever terminate ours. A listener Onionmind found rather than
        started stays alive, while the core gate prevents Onionmind from using
        it until the user explicitly turns Tor on again.
        """
        if self.tor_in_use:
            self.stop_active()
        event = getattr(self, "tor_stop_event", None)
        if event is not None:
            event.set()                          # unblocks a start still waiting
        managed = getattr(self.core, "_managed_tor_process", None)
        stopper = getattr(self.core, "stop_managed_tor", None)
        external = managed is None and self.tor_phase == "running"
        self.tor_probe_generation += 1
        disabled_for_onionmind = _set_tor_enabled(self.core, False)
        if managed is None and self.tor_phase == "running" and not disabled_for_onionmind:
            self.tor_status.setToolTip(
                "This Tor was already running when Onionmind found it. Onionmind did "
                "not start it and will not stop it; stop it where you started it."
            )
            _set_tor_action(
                self,
                "External",
                enabled=False,
                tooltip="This proxy is managed outside Onionmind and cannot be stopped here.",
            )
            self.set_status("That Tor proxy is not Onionmind's to stop.")
            self.inspector.append_activity("Stop refused: the Tor proxy was not started by Onionmind")
            return
        if managed is not None and callable(stopper):
            try:
                stopper()
            except Exception as exc:             # a dead process is still stopped
                self.inspector.append_activity(f"Stopping background Tor reported: {exc}")
        self.tor_stop_event = None
        self.tor_phase = "off"
        self.tor_status.set_status("Off", "idle")
        self.tor_status.setToolTip(
            "Tor is off for Onionmind. The external proxy remains running."
            if external
            else "Tor is off. Turn it on again without opening a browser window."
        )
        _set_tor_action(
            self,
            "Turn on",
            tooltip="Allow Onionmind to use Tor and start or reuse a local proxy.",
        )
        outcome = note or (
            "Tor turned off for Onionmind; the external proxy is still running."
            if external
            else "Background Tor stopped."
        )
        self.set_status(outcome)
        self.inspector.append_activity(outcome)

    def _start_tor_from_toolbar(self) -> None:
        """Start background Tor on demand, from the one control that shows it."""
        if not callable(getattr(self.core, "start_tor_hidden", None)):
            self.set_status("This Onionmind build cannot start background Tor.")
            _set_tor_action(
                self,
                "Unavailable",
                enabled=False,
                tooltip="This Onionmind build cannot manage a Tor proxy.",
            )
            return
        _set_tor_enabled(self.core, True)
        stop_event = self.announce_tor_starting("Starting background Tor from the toolbar…")
        generation = self.tor_probe_generation

        def start_job(signals: WorkerSignals) -> Any:
            del signals
            self.ensure_tor(stop_event)
            return self._verify_tor(stop_event)

        worker = self._start_worker(start_job)
        worker.signals.result.connect(
            lambda port, value=generation: self._toolbar_tor_started(port, value)
        )
        worker.signals.error.connect(
            lambda message, value=generation: self._toolbar_tor_failed(message, value)
        )

    def _toolbar_tor_started(self, port: Any, generation: int) -> None:
        if generation != self.tor_probe_generation or self.tor_phase != "starting":
            return
        self.tor_stop_event = None
        self._show_local_tor_state(port)
        if self.tor_phase == "running":
            _v2(self, "_egress", "Tor", "Tor circuit check · check.torproject.org")
            self.set_status("Tor is ready.")
            if getattr(self, "_awaiting_tor_fix", False):
                self._awaiting_tor_fix = False
                self.set_status(self.FAULTS["tor"]["done"])
        else:
            self.set_status("Tor is unavailable.")

    def _toolbar_tor_failed(self, message: str, generation: int) -> None:
        if generation != self.tor_probe_generation or self.tor_phase != "starting":
            return
        self.tor_stop_event = None
        self.tor_phase = "error"
        _set_tor_enabled(self.core, False)
        self.tor_status.set_status("Unavailable", "bad")
        self._report_error(message, "tor")
        message = _brand_runtime_text(message)
        self.tor_status.setToolTip(message)
        _set_tor_action(
            self,
            "Retry",
            tooltip=f"Tor did not become ready: {message} Click to try again.",
        )
        self.set_status(f"Tor did not start: {message}")
        self.inspector.append_activity(f"Background Tor failed to start: {message}")

    def _poll_tor_liveness(self) -> None:
        """Keep the only Tor indicator honest using local process/socket state."""
        if self.tor_phase != "running":
            return
        managed = getattr(self.core, "_managed_tor_process", None)
        managed_exited = False
        if managed is not None:
            try:
                managed_exited = managed.poll() is not None
            except Exception:
                managed_exited = True
            if managed_exited:
                stop = getattr(self.core, "stop_managed_tor", None)
                if callable(stop):
                    try:
                        stop()
                    except Exception:
                        pass
        probe = getattr(self.core, "tor_proxy_port", None)
        try:
            port = probe() if callable(probe) else None
        except Exception:
            port = None
        if managed_exited:
            self._show_local_tor_state(port)
            return
        if not port:
            try:
                setattr(self.core, "_port", None)
            except Exception:
                pass
            self._show_local_tor_state(None)
        elif getattr(self.core, "_port", None) not in (None, port):
            try:
                setattr(self.core, "_port", None)
            except Exception:
                pass
            self._show_local_tor_state(port)

    def _describe_model(self, raw_id: str) -> str:
        helper = getattr(self.desktop_core, "describe_model", None) if self.desktop_core else None
        if callable(helper):
            try:
                display = helper(raw_id)
                described = _as_text(_field(display, "display_name", ""))
                if described:
                    return described
            except Exception:
                pass
        lower = raw_id.lower()
        # Fallback for a core too old to describe models: the shipped tiers, with
        # what they actually are and what they cost to run.
        for token, described in (
            ("spark", "LFM2.5 2.6B · very light - ~2 GB, fine on CPU"),
            ("ember", "Qwen3.5 4B · light - ~3 GB VRAM"),
            ("blaze", "Qwen3.5 9B · moderate - ~7 GB VRAM"),
            ("inferno", "Qwen3.8 27B · heavy - ~12-16 GB VRAM"),
        ):
            if token in lower:
                return described
        return raw_id

    def set_model_options(self, models: Iterable[str], current: str = "") -> None:
        values: list[str] = []
        for model in [current, *models]:
            model = _as_text(model).strip()
            if model and model not in values:
                values.append(model)
        self.installed_model_ids = values
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for header in self._model_menu_headers():
            self.model_combo.addItem(header)
            item = self.model_combo.model().item(self.model_combo.count() - 1)
            if item is not None:
                item.setFlags(Qt.ItemFlag.NoItemFlags)
        counts: dict[str, int] = {}
        for model in values:
            base = self._describe_model(model)
            counts[base] = counts.get(base, 0) + 1
            label = base if counts[base] == 1 else f"{base} {counts[base]}"
            side = self._model_side_label(model)
            self.model_combo.addItem(f"{label}  ·  {side}" if side else label, model)
        index = self.model_combo.findData(current)
        if index < 0:
            index = next((i for i in range(self.model_combo.count())
                          if self.model_combo.itemData(i) is not None), 0)
        self.model_combo.setCurrentIndex(index)
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
        if self.feature("modelPlus"):
            per_mode = dict(self.settings_data.get("model_by_mode") or {})
            per_mode[getattr(self, "mode", "agent")] = model
            self.settings_data["model_by_mode"] = per_mode
        if not self.demo:
            self.settings_bridge.save(self.settings_data)
        self.set_status(f"Model set to {self._describe_model(model)}")
        self._refresh_model_headers()
        self._update_context_meter()

    def _approval_text(self, yolo: bool) -> str:
        if yolo:
            return "YOLO - no approval prompts"
        if self.feature("approvals"):
            return "Asks before protected actions"
        return "Protected actions stop safely"

    def _yolo_toggled(self, on: bool) -> None:
        """The label beside the box states what is actually armed, not a default."""
        self.approval_state.setText(self._approval_text(on))
        self.inspector.append_activity(
            "YOLO armed: edits and commands run unattended; network boundary unchanged"
            if on else (
                "Approvals on: protected actions wait for your answer"
                if self.feature("approvals") else "Approvals on: protected actions stop safely"
            )
        )

    def set_mode(self, mode: str) -> None:
        mode = "chat" if mode.lower() == "chat" else "agent"
        self.chat_button.setChecked(mode == "chat")
        self.agent_button.setChecked(mode == "agent")
        self.mode = mode
        if mode == "chat":
            self.approval_state.hide()
            self.yolo_consent.hide()
            self.search_consent.show()
            self.disclosure.setText("Private local chat · Tor search needs one-turn permission")
            self.composer.setPlaceholderText("Ask Onionmind anything…")
        else:
            self.approval_state.show()
            self.yolo_consent.show()
            self.search_consent.setChecked(False)
            self.search_consent.hide()
            self.disclosure.setText("Early access · Agent network access is separate from Tor search")
            self.composer.setPlaceholderText("Describe what you want Onionmind Agent to change…")
        self.settings_data["mode"] = mode
        if not self.demo:
            self.settings_bridge.save(self.settings_data)
        if hasattr(self, "preset_chip") and getattr(self, "features", None):
            self.preset_chip.setVisible(self.feature("presets") and mode == "chat")
        if getattr(self, "features", None) and self.feature("modelPlus"):
            wanted = _as_text((self.settings_data.get("model_by_mode") or {}).get(mode))
            if wanted and wanted in self.installed_model_ids and wanted != self.current_model_id():
                self.model_combo.setCurrentIndex(self.model_combo.findData(wanted))
                self._switch_loaded_model(wanted)
                label = "Chat" if mode == "chat" else "Agent"
                self.set_status(f"{label} uses {self._describe_model(wanted)}")
        if hasattr(self, "context_box"):
            self._update_context_meter()

    def focus_composer(self) -> None:
        self.composer.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def set_status(self, text: str) -> None:
        text = _brand_runtime_text(text)
        self.status_label.setText(text)
        self.status_label.setAccessibleName(f"Application status: {text}")
        if getattr(self, "features", None) and self.feature("boundary") and hasattr(self, "boundary_panel"):
            self._refresh_boundary()

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

    # --- Remembered window layout --------------------------------------
    #
    # Size, pane widths, and the *requested* pane visibility persist between
    # launches; the responsive rules in resizeEvent still win at narrow widths,
    # so a remembered layout can never force an unusable window.

    def _save_window_layout(self) -> None:
        if self.demo:
            # Demo windows run on empty settings; saving them would clobber a
            # real settings file with layout keys alone.
            return
        self.settings_data["window_geometry"] = bytes(self.saveGeometry().toBase64()).decode("ascii")
        self.settings_data["splitter_state"] = bytes(self.main_splitter.saveState().toBase64()).decode("ascii")
        self.settings_data["rail_visible"] = bool(self._rail_requested)
        self.settings_data["inspector_visible"] = bool(self._inspector_requested)
        self.settings_bridge.save(self.settings_data)

    def _restore_window_layout(self) -> None:
        geometry = _as_text(self.settings_data.get("window_geometry") or "")
        if geometry:
            try:
                self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii")))
            except (ValueError, RuntimeError):
                pass  # an unreadable blob falls back to the default workbench
        splitter_state = _as_text(self.settings_data.get("splitter_state") or "")
        if splitter_state:
            try:
                self.main_splitter.restoreState(QByteArray.fromBase64(splitter_state.encode("ascii")))
            except (ValueError, RuntimeError):
                pass
        self._rail_requested = bool(self.settings_data.get("rail_visible", True))
        self._inspector_requested = bool(self.settings_data.get("inspector_visible", True))
        self.toggle_rail(self._rail_requested)
        self.toggle_inspector(self._inspector_requested)
        draft = _as_text(self.settings_data.get("composer_draft"))
        if draft:
            self.composer.setPlainText(draft)

    def reset_window_layout(self) -> None:
        """Put the workbench back to the shipped layout and forget the rest."""
        for key in ("window_geometry", "splitter_state", "rail_visible", "inspector_visible"):
            self.settings_data.pop(key, None)
        if not self.demo:
            self.settings_bridge.save(self.settings_data)
        self.showNormal()
        self.resize(1420, 900)
        screen = self.screen()
        if screen is not None:
            available = screen.availableGeometry()
            self.move(
                available.center().x() - self.width() // 2,
                max(available.top(), available.center().y() - self.height() // 2),
            )
        self._rail_requested = True
        self._inspector_requested = True
        self.toggle_rail(True)
        self.toggle_inspector(True)
        self.main_splitter.setSizes([224, 860, 292])

    # --- Workbench preferences -----------------------------------------
    #
    # Presentation-only knobs: they apply live, persist through the settings
    # bridge, and default to the shipped workbench. The Tor boundary and the
    # updater permission live elsewhere on purpose - privacy is not a theme.

    def _load_preferences(self) -> dict[str, Any]:
        loader = getattr(self.desktop_core, "load_preferences", None)
        if callable(loader):
            return {**_FALLBACK_PREFERENCES, **dict(loader(self.settings_data))}
        return dict(_FALLBACK_PREFERENCES)

    def _apply_preferences(self) -> None:
        preferences = self.preferences
        self.composer.enterSends = bool(preferences.get("enter_sends", True))
        set_motion_override(_as_text(preferences.get("reduce_motion", "system")))
        factor = getattr(self.desktop_core, "text_scale_factor", None)
        scale = (
            factor(_as_text(preferences.get("text_scale", "system")))
            if callable(factor)
            else 1.0
        )
        apply_text_scale(scale * self._ui_scale_factor())
        tokens = getattr(self.desktop_core, "context_window_tokens", None)
        if callable(tokens) and hasattr(self.core, "NUM_CTX"):
            # The adapters read NUM_CTX per request, so this lands on the next
            # turn without restarting anything.
            self.core.NUM_CTX = tokens(preferences.get("context_window", 16384))

    def set_preference(self, key: str, value: Any) -> None:
        """Validate, persist, and live-apply one workbench preference."""
        loader = getattr(self.desktop_core, "load_preferences", None)
        if callable(loader):
            self.preferences = {
                **_FALLBACK_PREFERENCES,
                **dict(loader({**self.settings_data, key: value})),
            }
        else:
            self.preferences = {**_FALLBACK_PREFERENCES, key: value}
        for name, pref in self.preferences.items():
            if name in _FALLBACK_PREFERENCES:
                self.settings_data[name] = pref
        if not self.demo:
            self.settings_bridge.save(self.settings_data)
        self._apply_preferences()

    def _apply_startup_preferences(self) -> None:
        """The composer mode and terminal state to open with."""
        preferences = self.preferences
        resolver = getattr(self.desktop_core, "resolve_startup_mode", None)
        last = _as_text(self.settings_data.get("mode", ""))
        if callable(resolver):
            mode = resolver(_as_text(preferences.get("startup_mode", "remember")), last)
        else:
            mode = last if last in {"chat", "agent"} else "agent"
        self.set_mode(mode)
        if bool(preferences.get("show_terminal_on_launch", False)):
            self.toggle_terminal(True)

    # --- Session care: rename, retry, drafts, attention -----------------

    def rename_session(self, session_id: str, title: str) -> None:
        session = self.session_objects.get(session_id)
        store = getattr(self.session_bridge, "store", None)
        if session is None or store is None:
            self.set_status("That session could not be renamed.")
            return
        session.title = title
        try:
            store.save(session)
        except (TypeError, ValueError) as exc:
            self.set_status(f"Could not rename the session: {exc}")
            return
        self._refresh_session_rows()
        self.set_status(f"Session renamed to {title}.")

    def _refresh_session_rows(self) -> None:
        sessions = self.session_bridge.list()
        self.session_objects = {
            _as_text(_field(session, "id")): session for session in sessions
        }
        current_id = (
            _as_text(_field(self.current_session, "id"))
            if self.current_session is not None
            else None
        )
        self.left_rail.set_session_scope(self.workspace)
        self.left_rail.set_sessions(self._project_sessions(sessions), current_id)

    def _retry_last_turn(self) -> None:
        """Ask again after a failed chat turn: the error bubble comes off the
        history and the same conversation is re-sent locally."""
        self.retry_button.hide()
        if self.active_kind:
            return
        while (
            self.chat_messages
            and self.chat_messages[-1].get("role") == "assistant"
            and _as_text(self.chat_messages[-1].get("content")).startswith(
                ("Local inference failed", "Local inference could not continue")
            )
        ):
            self.chat_messages.pop()
        if not self.chat_messages or self.chat_messages[-1].get("role") != "user":
            self.set_status("There is no failed chat turn to retry.")
            return
        self._set_active("chat")
        self._start_chat()

    def _alert_if_unfocused(self) -> None:
        """The native attention nudge for a run that ended while another
        window was in front."""
        app = QApplication.instance()
        if app is not None and not self.isActiveWindow():
            QApplication.alert(self)

    def _remember_draft(self) -> None:
        draft = self.composer.toPlainText()
        if draft.strip() and self.preferences.get("remember_drafts", True):
            self.settings_data["composer_draft"] = draft
        else:
            self.settings_data.pop("composer_draft", None)
        if not self.demo:
            self.settings_bridge.save(self.settings_data)

    def _forget_draft(self) -> None:
        if self.settings_data.pop("composer_draft", None) is not None and not self.demo:
            self.settings_bridge.save(self.settings_data)

    def _sync_action_states(self) -> None:
        has_draft = bool(self.composer.toPlainText().strip() or self.attachments)
        loading = bool(getattr(self, "_model_loading", None))
        self.send_button.setEnabled(bool(self.active_kind) or (has_draft and not loading))
        self.left_rail.set_conversation_available(bool(self.chat_messages))
        if hasattr(self, "context_box"):
            self._update_context_meter()

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        for overlay in (getattr(self, "lock_overlay", None), getattr(self, "setup_overlay", None)):
            if overlay is not None and overlay.isVisible():
                overlay.setGeometry(self.rect())
        width = self.width()
        compact_toolbar = width < 1100
        self.brand_box.setFixedWidth(165 if width < 900 else 205)
        self.repo_label.setVisible(width >= 900)
        self.branch_label.setVisible(not compact_toolbar)
        self.toolbar_separator.setVisible(not compact_toolbar)
        self.model_status.setVisible(width >= 1280 or bool(getattr(self, "_model_loading", None)))
        self.tor_status.setVisible(True)
        if hasattr(self, "palette_button"):
            self._sync_toolbar_features()
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

    def _confirm_permanent_deletion(
        self,
        *,
        title: str,
        text: str,
        detail: str,
        confirm_label: str,
    ) -> bool:
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle(title)
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText(text)
        dialog.setInformativeText(detail)
        confirm = dialog.addButton(
            confirm_label, QMessageBox.ButtonRole.DestructiveRole
        )
        confirm.setAccessibleName(confirm_label)
        cancel = dialog.addButton(QMessageBox.StandardButton.Cancel)
        dialog.setDefaultButton(cancel)
        dialog.setEscapeButton(cancel)
        dialog.exec()
        return dialog.clickedButton() is confirm

    def _validated_project_delete_target(self, path: str) -> Path:
        candidate = Path(path).expanduser()
        is_junction = getattr(candidate, "is_junction", None)
        if candidate.is_symlink() or (callable(is_junction) and is_junction()):
            raise ValueError(
                "Linked project folders cannot be deleted here. Remove the project "
                "from the list, then manage the link in your file manager."
            )
        if not candidate.exists():
            raise ValueError("The project folder no longer exists on this machine.")
        if not candidate.is_dir():
            raise ValueError("The selected project path is not a folder.")

        target = candidate.resolve(strict=True)
        if target.parent == target or target.is_mount():
            raise ValueError("A drive or filesystem root cannot be deleted as a project.")

        home = Path.home().resolve()
        if target == home or target in home.parents:
            raise ValueError("Your home folder or one of its parents cannot be deleted here.")

        for protected, label in (
            (self.data_root.resolve(), "Onionmind's local data"),
            (MODULE_DIR.resolve(), "the running Onionmind application"),
        ):
            if (
                target == protected
                or target in protected.parents
                or protected in target.parents
            ):
                raise ValueError(f"This folder overlaps {label} and cannot be deleted here.")
        return target

    def _forget_project_reference(self, path: str) -> bool:
        target_key = _path_key(path)
        recent = [
            _as_text(item)
            for item in self.settings_data.get("recent_projects", [])
            if _as_text(item)
        ]
        remaining = [item for item in recent if _path_key(item) != target_key]
        removed = self.left_rail.remove_project(path) or len(remaining) != len(recent)
        self.settings_data["recent_projects"] = remaining
        if _path_key(self.settings_data.get("workspace")) == target_key:
            self.settings_data["workspace"] = ""
        if not self.demo:
            self.settings_bridge.save(self.settings_data)
        return removed

    def remove_project_from_menu(self, path: str) -> None:
        if not path:
            self.set_status("Select a project to remove from the list.")
            return
        if self._project_delete_pending == _path_key(path):
            self.set_status("That project folder is already being deleted.")
            return
        if not self._forget_project_reference(path):
            self.set_status("That project is no longer in the Projects list.")
            return
        name = Path(path).name or path
        still_open = _path_key(self.workspace) == _path_key(path)
        suffix = " It remains open." if still_open else ""
        self.set_status(
            f"Removed {name} from Projects; its folder remains on this machine.{suffix}"
        )
        self.inspector.append_activity(
            f"Project removed from the list; folder kept: {name}"
        )

    def delete_project_from_machine(self, path: str) -> None:
        if self.active_kind:
            self.set_status("Stop the active run before deleting a project folder.")
            return
        if self._project_delete_pending is not None:
            self.set_status("Wait for the current project deletion to finish.")
            return
        if not path:
            self.set_status("Select a project folder to delete.")
            return
        if (
            _path_is_within(self.workspace, path)
            and self.terminal.process.state() != QProcess.ProcessState.NotRunning
        ):
            self.set_status(
                "Stop the terminal command before deleting its project folder."
            )
            return
        try:
            target = self._validated_project_delete_target(path)
        except (OSError, ValueError) as exc:
            message = _as_text(exc)
            self.set_status(message)
            QMessageBox.warning(self, "Project folder not deleted", message)
            return

        name = target.name or str(target)
        if not self._confirm_permanent_deletion(
            title="Delete project folder from machine",
            text=f"Permanently delete “{name}” and everything inside it?",
            detail=(
                "This removes the folder from this machine and cannot be undone.\n\n"
                f"{target}"
            ),
            confirm_label="Delete folder",
        ):
            return

        expected = target
        self._project_delete_pending = _path_key(expected)
        self.set_status(f"Deleting project folder from this machine: {name}…")

        def delete_job(signals: WorkerSignals) -> str:
            del signals
            checked = self._validated_project_delete_target(str(expected))
            if _path_key(checked) != _path_key(expected):
                raise RuntimeError(
                    "The project path changed before deletion, so Onionmind stopped safely."
                )
            shutil.rmtree(checked)
            return str(checked)

        def setup(worker: SafeWorker) -> None:
            worker.signals.result.connect(self._project_delete_complete)
            worker.signals.error.connect(
                lambda message: self._project_delete_failed(str(expected), message)
            )

        self._start_worker(delete_job, setup)

    def _project_delete_complete(self, path: Any) -> None:
        deleted_path = _as_text(path)
        self._project_delete_pending = None
        was_open = _path_is_within(self.workspace, deleted_path)
        self._forget_project_reference(deleted_path)
        if was_open:
            self.close_project()
        name = Path(deleted_path).name or deleted_path
        self.set_status(f"Deleted project folder from this machine: {name}")
        self.inspector.append_activity(
            f"Project folder permanently deleted from this machine: {name}"
        )

    def _project_delete_failed(self, path: str, message: str) -> None:
        self._project_delete_pending = None
        name = Path(path).name or path
        text = f"Could not delete {name}: {message}"
        self.set_status(text)
        self.inspector.append_activity(text)
        QMessageBox.warning(
            self,
            "Project folder not deleted",
            text + "\n\nClose programs using the folder, check permissions, then try again.",
        )

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
        recent = [p for p in recent if _path_key(p) != _path_key(self.workspace)]
        recent.insert(0, self.workspace)
        self.settings_data.update(workspace=self.workspace, recent_projects=recent[:10])
        if not self.demo:
            self.settings_bridge.save(self.settings_data)
        self._refresh_session_rows()
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
        if not self.workspace or _path_key(snapshot.get("root")) != _path_key(self.workspace):
            return
        self.current_snapshot = snapshot
        branch = snapshot.get("branch") or ("No repository" if not snapshot.get("is_git") else "detached HEAD")
        self.branch_label.setText(_as_text(branch))
        self.inspector.update_snapshot(snapshot)
        count = len(snapshot.get("changes") or [])
        self.set_status(f"Project inspected · {count} observed change(s)")
        self.inspector.append_activity(f"Project refreshed; {count} observed change(s)")

    def _workspace_failed(self, message: str) -> None:
        if not self.workspace:
            return
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
        self.left_rail.clear_session_selection()
        self._starters = None
        self._sync_starters()
        self.set_status("New task ready")
        self.focus_composer()

    def add_session(self) -> None:
        if self.active_kind:
            self.set_status("Stop the active run before adding a session.")
            return
        if not self.preferences.get("save_history", True):
            self.set_status(
                "Saving conversations to this machine is off in settings; nothing was written."
            )
            return
        if not self.save_current_session():
            self.set_status(
                "A new session was not added because the current history could not be saved."
            )
            return
        self.new_task(save_current=False)
        try:
            model = self.current_model_id()
            session = self.session_bridge.create(
                "New session", model, self.workspace, ()
            )
            session = self.session_bridge.save(
                session,
                title="New session",
                model=model,
                workspace=self.workspace,
                messages=[],
            )
        except Exception as exc:
            message = f"Could not add a saved session: {exc}"
            self.set_status(message)
            self.inspector.append_activity(message)
            return
        self.current_session = session
        session_id = _as_text(_field(session, "id"))
        self.session_objects[session_id] = session
        sessions = self.session_bridge.list()
        self.session_objects = {
            _as_text(_field(item, "id")): item for item in sessions
        }
        self.left_rail.set_sessions(self._project_sessions(sessions), session_id)
        self.set_status("Added saved session")
        self.inspector.append_activity("Saved session added locally")

    def _session_title(self) -> str:
        first = next((_as_text(m.get("content")) for m in self.chat_messages if m.get("role") == "user"), "New session")
        first = re.sub(r"\s+", " ", first).strip()
        return first[:48] + ("…" if len(first) > 48 else "")

    def _session_is_unchanged(self, model: str) -> bool:
        """Opening or leaving a session must not reorder the list; only new
        history may. The list sorts on updated_at and every save stamps it,
        so an unchanged session is not written back at all. The title is not
        compared: it is derived from the first message, and a renamed session
        must not be re-saved (and re-titled) just for being opened."""
        session = self.current_session
        return (
            session is not None
            and _as_text(_field(session, "model")) == model
            and _as_text(_field(session, "workspace")) == _as_text(self.workspace)
            and list(_field(session, "messages", ()) or ()) == self.chat_messages
        )

    def save_current_session(self) -> bool:
        if not self.chat_messages:
            return True
        if not self.preferences.get("save_history", True):
            # Nothing to report: the user turned off writing conversations to
            # disk, so an unwritten history is the expected outcome.
            return True
        try:
            # This is the final persistence boundary. A live tool round keeps
            # its raw structure in the worker's private history until it has
            # completed; only the copy owned by the UI is cleaned here.
            self.chat_messages = _sanitize_assistant_messages(self.chat_messages)
            title = self._session_title()
            kept = _as_text(_field(self.current_session, "title", "")) if self.current_session is not None else ""
            if kept.startswith("Branch of "):
                title = kept                     # a branch keeps the name it was given
            model = self.current_model_id()
            if self._session_is_unchanged(model):
                return True
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
            self.left_rail.set_sessions(
                self._project_sessions(self.session_bridge.list()), session_id
            )
            return True
        except Exception as exc:
            message = f"Session history could not be saved: {exc}"
            self.set_status(message)
            self.inspector.append_activity(message)
            return False

    def _await_workers(self, timeout_ms: int = 3000) -> None:
        """Wait, bounded, for background jobs to finish. Used before a wipe:
        a worker reading the project folder keeps it undeletable."""
        deadline = time.monotonic() + timeout_ms / 1000
        while self._workers and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.02)

    def close_project(self) -> None:
        """Leave the workbench with no project open."""
        self.workspace = None
        self.current_snapshot = {}
        self.repo_label.setText("No project")
        self.repo_label.setToolTip("")
        self.branch_label.setText("Open a folder")
        self.scope_status.setText("No project selected")
        self.terminal.set_workspace(str(Path.home()))
        self.inspector.update_snapshot({})
        self._refresh_session_rows()

    def wipe_machine_data(self) -> dict[str, int]:
        """Destroy this user's work on this machine: every saved and archived
        session, the project list, **the project folders and everything in
        them**, the last opened project, any unsent draft, and the agent's
        network log. Files are overwritten before they are unlinked.

        A project that fails the same safety check the per-project delete uses
        (a drive root, your home folder, Onionmind's own data or program
        folder, a symlinked folder) is skipped and counted, not obeyed - the
        wipe is for your work, not for the machine.

        ponytail: the overwrite is best-effort user space. On an SSD or a
        copy-on-write filesystem the old blocks can survive; full-disk
        encryption or the Matchstick RAM-only image is the real guarantee.
        """
        targets: list[str] = []
        for index in range(self.left_rail.projects.count()):
            item = self.left_rail.projects.item(index)
            targets.append(_as_text(item.data(Qt.ItemDataRole.UserRole)))
        targets.extend(
            _as_text(item) for item in self.settings_data.get("recent_projects", [])
        )
        targets.append(_as_text(self.workspace))

        # Release the folders first: a running shell keeps its working
        # directory open, and Windows will not remove a directory in use.
        self.terminal.stop()
        terminal_process = getattr(self.terminal, "process", None)
        if terminal_process is not None and (
            terminal_process.state() != QProcess.ProcessState.NotRunning
        ):
            terminal_process.kill()
            terminal_process.waitForFinished(2000)
        self.close_project()
        # A workspace/Git snapshot runs inside the project folder; a folder
        # being read cannot be removed, so let in-flight jobs land first.
        self._await_workers()

        sessions = self.delete_all_sessions()
        shred_tree = _optional_callable(self.desktop_core, "shred_tree")
        shred_file = _optional_callable(self.desktop_core, "shred_file")
        store_root = getattr(getattr(self.session_bridge, "store", None), "root", None)
        if callable(shred_tree) and store_root is not None:
            # Sweeps the archive and any quarantined leftovers with it.
            shred_tree(store_root)

        wiped, skipped, seen = 0, 0, set()
        for raw in targets:
            key = _path_key(raw)
            if not key or key in seen:
                continue
            seen.add(key)
            try:
                target = self._validated_project_delete_target(raw)
            except (ValueError, OSError):
                skipped += 1
                continue
            for attempt in range(3):
                if callable(shred_tree):
                    shred_tree(target)
                if target.exists():
                    # Something still held a handle while the files went; take
                    # the empty shell of the folder with the blunt tool.
                    shutil.rmtree(target, ignore_errors=True)
                if not target.exists():
                    break
                time.sleep(0.15)      # Windows releases handles a beat later
            wiped += 0 if target.exists() else 1

        net_log = _as_text(getattr(self.core, "NET_LOG", ""))
        if net_log and callable(shred_file) and Path(net_log).is_file():
            shred_file(net_log)
        if callable(shred_file) and Path(self.egress_path).is_file():
            shred_file(self.egress_path)
            self._egress_counts = None
        reverts = self.data_root / "reverts"
        if callable(shred_tree) and reverts.is_dir():
            shred_tree(reverts)
            self._revert_record = None
        tor = self.wipe_tor_state()

        self.left_rail.clear_projects()
        for key in ("recent_projects", "workspace", "composer_draft"):
            self.settings_data.pop(key, None)
        if not self.demo:
            self.settings_bridge.save(self.settings_data)
        self.close_project()
        self.inspector.append_activity(
            f"Wiped from this machine: {sessions} session(s), {wiped} project folder(s), "
            f"{tor} Tor state file(s)"
            + (f", {skipped} protected path(s) skipped" if skipped else "")
        )
        return {"sessions": sessions, "projects": wiped, "skipped": skipped, "tor": tor}

    # Tor's own footprint: the state file naming this machine's guard relays,
    # the cached consensus and descriptors, and any log it wrote.
    _TOR_TRACE_PREFIXES = ("state", "cached-", "diff-cache", "lock", "unverified-")

    def wipe_tor_state(self) -> int:
        """Destroy Tor's history on this machine. Onionmind's own data
        directory goes whole; a Tor Browser one keeps the torrc that makes it
        runnable and loses every trace of what it did."""
        shred_tree = _optional_callable(self.desktop_core, "shred_tree")
        shred_file = _optional_callable(self.desktop_core, "shred_file")
        if not callable(shred_file):
            return 0
        stop_tor = getattr(self.core, "stop_managed_tor", None)
        if callable(stop_tor):
            # Tor holds its own files open; it has to be down first.
            try:
                stop_tor()
            except Exception:
                pass
        directories = getattr(self.core, "tor_data_dirs", None)
        try:
            candidates = list(directories()) if callable(directories) else []
        except Exception:
            candidates = []
        # Discovery is the core's job (tor_data_dirs); this only classifies
        # what comes back, so a core that names nothing wipes nothing.
        own = Path.home() / ".onionmind" / "tor"
        removed = 0
        for raw in candidates:
            directory = Path(_as_text(raw))
            if not directory.is_dir():
                continue
            if _path_key(directory) == _path_key(own):
                removed += shred_tree(directory) if callable(shred_tree) else 0
                continue
            try:
                entries = list(directory.iterdir())
            except OSError:
                continue
            for entry in entries:
                name = entry.name.casefold()
                traces = name.endswith(".log") or name.startswith(self._TOR_TRACE_PREFIXES)
                if traces and entry.is_file() and shred_file(entry):
                    removed += 1
        return removed

    def _project_sessions(self, sessions: Iterable[Any]) -> list[Any]:
        """The saved sessions belonging to the open project.

        Every session records the project it ran in, so the rail can show that
        project's work only and switching projects switches the list. Sessions
        saved with no project open belong to no project and show only then.
        """
        current = _path_key(self.workspace)
        return [
            item for item in sessions if _path_key(_field(item, "workspace")) == current
        ]

    def delete_all_sessions(self) -> int:
        """Permanently remove every saved and archived conversation."""
        removed = 0
        for archived in (False, True):
            for item in self.session_bridge.list(archived=archived):
                if self.session_bridge.delete(_as_text(_field(item, "id"))):
                    removed += 1
        self.current_session = None
        self.session_objects = {}
        self.left_rail.set_sessions([])
        if removed:
            self.inspector.append_activity(
                f"Deleted {removed} saved conversation(s) from local storage"
            )
        return removed

    def archive_session(self, session_id: str) -> None:
        if self.active_kind:
            self.set_status("Stop the active run before removing a session.")
            return
        if not session_id:
            self.set_status("Select a saved session to remove from the list.")
            return
        session = self.session_objects.get(session_id)
        title = _as_text(_field(session, "title", "this session"))
        answer = QMessageBox.question(
            self,
            "Remove session from Sessions",
            f"Remove “{title}” from Sessions? It will remain on this machine in local archive storage.",
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
            self.set_status("The selected session could not be removed from the list.")
            return
        self.session_objects.pop(session_id, None)
        if self.current_session is not None and _as_text(_field(self.current_session, "id")) == session_id:
            self.new_task(save_current=False)
        sessions = self.session_bridge.list()
        self.session_objects = {_as_text(_field(item, "id")): item for item in sessions}
        self.left_rail.set_sessions(
            self._project_sessions(sessions),
            _as_text(_field(self.current_session, "id")) if self.current_session else None,
        )
        self.set_status(f"Removed session from Sessions: {title}")
        self.inspector.append_activity(
            f"Session removed from the list and kept in local archive storage: {title}"
        )

    def delete_session_from_machine(self, session_id: str) -> None:
        if self.active_kind:
            self.set_status("Stop the active run before deleting a session.")
            return
        if not session_id:
            self.set_status("Select a saved session to delete from this machine.")
            return
        session = self.session_objects.get(session_id)
        if session is None:
            session = next(
                (
                    item
                    for item in self.session_bridge.list()
                    if _as_text(_field(item, "id")) == session_id
                ),
                None,
            )
        if session is None:
            self.set_status("That saved session is no longer available.")
            self.left_rail.set_sessions(self._project_sessions(self.session_bridge.list()))
            return
        title = _as_text(_field(session, "title", "this session"))
        if not self._confirm_permanent_deletion(
            title="Delete session from machine",
            text=f"Permanently delete “{title}”?",
            detail=(
                "This removes the session history from this machine and cannot be undone."
            ),
            confirm_label="Delete session",
        ):
            return
        if not self.session_bridge.delete(session_id):
            self.set_status("The selected session could not be deleted from this machine.")
            QMessageBox.warning(
                self,
                "Session not deleted",
                "Onionmind could not remove the local session file. Check local storage permissions, then try again.",
            )
            return
        self.session_objects.pop(session_id, None)
        if (
            self.current_session is not None
            and _as_text(_field(self.current_session, "id")) == session_id
        ):
            self.new_task(save_current=False)
        sessions = self.session_bridge.list()
        self.session_objects = {
            _as_text(_field(item, "id")): item for item in sessions
        }
        current_id = (
            _as_text(_field(self.current_session, "id"))
            if self.current_session
            else None
        )
        self.left_rail.set_sessions(self._project_sessions(sessions), current_id)
        self.set_status(f"Deleted session from this machine: {title}")
        self.inspector.append_activity(
            f"Session permanently deleted from this machine: {title}"
        )

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
        document = _conversation_markdown(
            self._session_title(),
            self._describe_model(self.current_model_id()),
            self.workspace,
            self.chat_messages,
        )
        try:
            destination.write_text(document, encoding="utf-8")
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
        self.chat_messages = _sanitize_assistant_messages(
            _field(session, "messages", ()) or ()
        )
        self._render_messages()
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
            # A send during a live run is a redirect: stop the run, keep the
            # partial answer in the transcript, and send the typed direction
            # as the next turn the moment the run finishes unwinding. An empty
            # composer keeps the old meaning: plain stop.
            if self.composer.toPlainText().strip() or self.attachments:
                self._pending_redirect = True
                self.stop_active()
                if self._pending_redirect:
                    self.set_status("Stopping the current run; your direction is sent next.")
            else:
                self.stop_active()
            return
        task = self.composer.toPlainText().strip()
        if not task and not self.attachments:
            self.set_status("Describe a task or attach a local file first.")
            self.focus_composer()
            return
        if self._model_loading:
            self.set_status(f"{self._describe_model(self._model_loading)} is still loading. Sending waits until it is ready.")
            return
        if self._fault is not None:
            self.set_status(self.FAULTS[self._fault]["status"])
            return
        if self.feature("empty") and self._no_models:
            self.set_status("No model is installed yet. Choose one in Models.")
            return
        if self._edit_from is not None:
            # Sending an edit replaces that message and everything after it.
            self.chat_messages = self.chat_messages[:self._edit_from]
            self._render_messages()
            self._cancel_edit()
        if not task:
            task = "Review the attached local files."
        search_allowed = self.mode == "chat" and self.search_consent.isChecked()
        if (self.mode == "agent" or search_allowed) and not _tor_is_enabled(self.core):
            self.set_status("Tor proxy is off. Turn it on in the toolbar before using a protected feature.")
            self.inspector.append_activity(
                "Protected run blocked because Tor was explicitly turned off; no network request was made"
            )
            return
        if self.mode == "agent":
            answer = QMessageBox.warning(
                self,
                "Run Onionmind Agent?",
                "Onionmind Agent sends proxy-aware traffic through Tor and blocks direct "
                "Python and Node connections, but it is not fully confined to Tor: a "
                "command that ignores proxy settings can contact hosts directly and expose "
                "this machine's network address. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.set_status("Agent run cancelled; no process was started.")
                return
        self.search_consent.setChecked(False)
        attachment_names = [Path(path).name for path in self.attachments]
        visible_task = task + ("\n\nAttached locally: " + ", ".join(attachment_names) if attachment_names else "")
        message, agent_task = self._build_user_payload(task)
        self.chat_messages.append(message)
        self._index_block(self.transcript.add_message("user", visible_task), len(self.chat_messages) - 1)
        self.composer.clear()
        self._forget_draft()
        self.clear_attachments()
        self._set_active(self.mode)
        if not self.save_current_session():
            self.transcript.add_tool_card(
                "Session storage",
                [("Not saved", "The run will continue; check local disk access")],
            )
        if self.mode == "chat":
            if search_allowed:
                self._start_chat(True)
            elif self._web_ask_wanted(task):
                self._ask_before_search(task)
            else:
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
        was_running = bool(self.active_kind)
        self.active_kind = kind
        running = bool(kind)
        self.retry_button.hide()
        self.send_button.setText("Stop" if running else "Send")
        self.send_button.setAccessibleName("Stop active run" if running else "Send task")
        if kind == "agent":
            self.send_button.setToolTip(
                "Stop Onionmind Agent; child processes it started may require manual termination. "
                "A typed direction is sent when it stops."
            )
        elif kind == "chat":
            self.send_button.setToolTip(
                "Stop local generation after the current read. A typed direction is sent when it stops."
            )
        else:
            self.send_button.setToolTip("Send task (Enter)")
        self.chat_button.setEnabled(not running)
        self.agent_button.setEnabled(not running)
        self.model_combo.setEnabled(not running)
        self.search_consent.setEnabled(not running)
        if running:
            self.search_consent.setChecked(False)
        self.left_rail.projects.setEnabled(not running)
        self.left_rail.sessions.setEnabled(not running)
        if not running:
            self.stop_event = None
            self.harness_process = None
        if kind is None and self._pending_redirect:
            self._pending_redirect = False
            if self.composer.toPlainText().strip() or self.attachments:
                # Deferred through the event loop: the finishing run's own
                # handlers are still unwinding on this stack.
                QTimer.singleShot(0, self.submit)
        if kind is None and was_running:
            self._alert_if_unfocused()
        self._sync_action_states()
        if hasattr(self, "speed_status"):
            self._update_live_status()           # "Idle" again once nothing runs

    def _start_chat(self, allow_search: bool = False) -> None:
        self.tor_in_use = allow_search
        self._turn_reasoning = 0.0
        self._live_tps = None
        self._turn_searches = []
        self.stop_event = threading.Event()
        stop_event = self.stop_event
        model = self.current_model_id()
        history = copy.deepcopy(self.chat_messages)
        preset = _v2(self, "_preset_text", default="")
        if preset and callable(getattr(self.desktop_core, "with_system_prompt", None)):
            history = self.desktop_core.with_system_prompt(history, preset)
        block = self.transcript.add_message("assistant", "")
        block.start_thinking("Starting Tor" if allow_search else "Thinking")
        self.stream_block = block
        if allow_search:
            self.tor_probe_generation += 1
            self.tor_phase = "starting"
            self.tor_stop_event = stop_event
            self.tor_status.set_status("Checking…", "warn")
            _set_tor_action(self, "Cancel", tooltip="Cancel this Tor startup and Chat turn.")
            self.set_status("Starting background Tor without opening a browser window…")
            self.inspector.append_activity("One-turn Tor search permission granted")
        else:
            self.set_status(f"Thinking with {self._describe_model(model)}…")
            self.inspector.append_activity("Chat turn started on the local-only inference path")

        def chat_job(signals: WorkerSignals) -> dict[str, Any]:
            setattr(self.core, "MODEL", model)
            if allow_search:
                starter = getattr(self.core, "start_tor_hidden", None)
                if not callable(starter):
                    raise RuntimeError("This Onionmind core cannot start background Tor.")
                port = starter(stop_event=stop_event)
                managed = getattr(self.core, "_managed_tor_process", None)
                signals.event.emit({
                    "kind": "tor_ready",
                    "port": port,
                    "managed": managed is not None,
                })
                verified_port = self._verify_tor(stop_event)
                signals.event.emit({"kind": "tor_verified", "port": verified_port})
            if not getattr(self.core, "BACKEND", None):
                detector = getattr(self.core, "detect_backend", None)
                if callable(detector):
                    detector()
            turn_stream = getattr(self.core, "turn_stream", None)
            if not callable(turn_stream):
                raise RuntimeError("The Onionmind core does not expose streaming chat.")
            stream_filter = ThinkingStreamFilter()

            def on_text(chunk: str) -> None:
                if stop_event.is_set():
                    stream_filter.abort()
                    return
                stream_filter.feed(chunk)

            def on_event(event: dict[str, Any]) -> None:
                signals.event.emit(dict(event))

            try:
                try:
                    answer = turn_stream(
                        history,
                        on_text,
                        stop_event=stop_event,
                        on_event=on_event,
                        allow_search=allow_search,
                    )
                except TypeError as exc:
                    if "on_event" not in _as_text(exc):
                        raise
                    try:
                        answer = turn_stream(
                            history,
                            on_text,
                            stop_event=stop_event,
                            allow_search=allow_search,
                        )
                    except TypeError as compatibility_error:
                        if "allow_search" in _as_text(compatibility_error):
                            raise RuntimeError(
                                "The installed Onionmind core is too old to enforce per-turn search permission."
                            ) from compatibility_error
                        raise
            except BaseException:
                if stop_event.is_set():
                    # Cancellation is a normal completion path. Keep the
                    # privacy-filtered text received before Stop was clicked;
                    # do not turn a partial answer into an error message.
                    return {
                        "answer": stream_filter.finish(),
                        "history": history,
                        "stopped": True,
                    }
                stream_filter.abort()
                raise
            if stop_event.is_set():
                # Stop must halt further generation without erasing the
                # already-buffered, sanitized response.
                safe_answer = stream_filter.finish()
                stopped = True
            else:
                buffered_answer = stream_filter.finish()
                returned_answer = _strip_thinking(_as_text(answer))
                safe_answer = returned_answer or buffered_answer
                stopped = False
            return {"answer": safe_answer, "history": history, "stopped": stopped}

        worker = self._start_worker(chat_job)
        worker.signals.event.connect(self._chat_event)
        worker.signals.result.connect(self._chat_complete)
        worker.signals.error.connect(self._chat_failed)

    def _chat_event(self, event: dict[str, Any]) -> None:
        kind = _as_text(event.get("kind"))
        if kind in ("generating", "stats"):
            self._generation_event(kind, event)
            return
        name = _as_text(event.get("name", "local tool"))
        display_name = name.replace("_", " ").strip().title()
        if kind == "tor_ready":
            port = event.get("port")
            self.tor_phase = "starting"
            self.tor_status.set_status("Checking…", "warn")
            _set_tor_action(
                self,
                "Cancel",
                tooltip="Cancel this Tor verification and Chat turn.",
            )
            self.tor_status.setToolTip(
                "A local proxy connection is available; Onionmind is verifying it as Tor. "
                "Click to cancel."
            )
            self.set_status("Verifying the Tor circuit before protected work begins…")
            self.inspector.append_activity("Local SOCKS listener ready; verifying Tor circuit")
        elif kind == "tor_verified":
            port = event.get("port")
            self.tor_phase = "running"
            self.tor_status.set_status("Ready", "good")
            _set_tor_action(
                self,
                "Turn off",
                tooltip=(
                    "Stop Onionmind's background Tor process."
                    if getattr(self.core, "_managed_tor_process", None) is not None
                    else "Disconnect Onionmind from this external proxy; the external process stays running."
                ),
            )
            self.tor_status.setToolTip(
                "Tor is verified and ready. Click to turn it off."
            )
            if self.stream_block is not None:
                self.stream_block.set_pending_label("Thinking")
            self.set_status(f"Tor ready · thinking with {self._describe_model(self.current_model_id())}…")
            self.inspector.append_activity("Background Tor path verified")
            self._egress("Tor", "Tor circuit check · check.torproject.org")
        elif kind == "tool_started":
            arguments = event.get("arguments") or {}
            detail = _as_text(arguments.get("query")) if isinstance(arguments, dict) else ""
            state = "Running through Tor · fails closed" if name == "web_search" else "Running locally"
            if self.stream_block is not None:
                self.stream_block.set_pending_label("Searching via Tor" if name == "web_search" else "Using local tool")
            self._tool_started(display_name, detail or "Tool request", state)
            activity = "Tor search started" if name == "web_search" else f"Local tool started: {display_name}"
            self.inspector.append_activity(activity, "tor" if name == "web_search" else "machine")
            if name == "web_search":
                self._searching = True
                self._refresh_boundary()
        elif kind == "tool_finished":
            if self.stream_block is not None:
                self.stream_block.set_pending_label("Thinking")
            state = "Tor result returned" if name == "web_search" else "Finished"
            if name == "web_search" and event.get("results") is not None and self.feature("timeline"):
                state = f"{event.get('results')} results over Tor"
            self._tool_finished(display_name, "Tool result", state)
            activity = "Tor search finished" if name == "web_search" else f"Local tool finished: {display_name}"
            self.inspector.append_activity(activity, "outside" if name == "web_search" else "machine")
            if name == "web_search":
                self._searching = False
                self._turn_searches.append({"results": event.get("results"),
                                            "domains": list(event.get("domains") or [])})
                self._egress("Tor", "Chat search · DuckDuckGo onion service")
                self._refresh_boundary()
        elif kind == "tool_refused":
            self.transcript.add_tool_card(display_name, [("Not run", "No one-turn search permission")])
            self.inspector.append_activity("Tor search request refused; no network request was made")

    def _chat_complete(self, payload: dict[str, Any]) -> None:
        self.tor_in_use = False
        self.tor_stop_event = None
        answer = _strip_thinking(payload.get("answer"))
        stopped = bool(payload.get("stopped"))
        if not answer and stopped:
            answer = "Generation stopped before an answer was written."
        elif not answer:
            answer = "The local model returned no answer."
        if self.stream_block is not None:
            self.stream_block.set_text(answer)
        _v2(self, "_after_reply", stopped)
        raw_history = payload.get("history") or [
            *self.chat_messages,
            {"role": "assistant", "content": answer},
        ]
        # A preset's system prompt is sent, never stored with the conversation.
        raw_history = [m for m in raw_history if m.get("role") != "system"]
        self.chat_messages = _sanitize_assistant_messages(raw_history)
        if self.chat_messages and self.chat_messages[-1].get("role") == "assistant":
            self.chat_messages[-1]["content"] = answer
        if not self.chat_messages or self.chat_messages[-1].get("role") != "assistant":
            self.chat_messages.append({"role": "assistant", "content": answer})
        _v2(self, "_index_block", self.stream_block, len(self.chat_messages) - 1)
        local_probe = getattr(self.core, "tor_proxy_port", None)
        try:
            port = local_probe() if callable(local_probe) else None
        except Exception:
            port = None
        self._show_local_tor_state(port)
        self.set_status("Chat stopped" if stopped else "Chat turn complete")
        self.inspector.append_activity(
            "Chat stopped; partial answer preserved" if stopped else "Chat turn completed locally"
        )
        self._set_active(None)
        self.save_current_session()

    def _chat_failed(self, message: str) -> None:
        searched = self.tor_in_use
        self.tor_in_use = False
        self.tor_stop_event = None
        self._report_error(message, "tor" if searched and "tor" in message.casefold() else None)
        message = _brand_runtime_text(_strip_thinking(message))
        if self.stream_block is not None:
            self.stream_block.set_text(f"Local inference could not continue. {message}")
        self.chat_messages.append({"role": "assistant", "content": f"Local inference failed: {message}"})
        self._index_block(self.stream_block, len(self.chat_messages) - 1)
        local_probe = getattr(self.core, "tor_proxy_port", None)
        try:
            port = local_probe() if callable(local_probe) else None
        except Exception:
            port = None
        self._show_local_tor_state(port)
        self.set_status(f"Local inference failed: {message}")
        self.inspector.append_activity(f"Chat turn failed: {message}")
        self._set_active(None)
        self.retry_button.setVisible(self._fault is None)
        self.save_current_session()

    def _start_harness(self, task: str, resume: str = "", allow_all: bool = False) -> None:
        if not self.workspace:
            block = self.transcript.add_message(
                "assistant", "Agent mode needs a project folder. Open one with Ctrl+O, then send the task again."
            )
            self.stream_block = block
            self.set_status("Agent mode needs a project folder")
            self._set_active(None)
            return
        self.tor_in_use = True
        model = self.current_model_id()
        workspace = self.workspace
        yolo = self.yolo_consent.isChecked()
        # v2: the structured stream when a feature needs it, and this
        # project's rules - which only ever narrow what the agent may do.
        stream = self._uses_agent_stream()
        rules = self._project_rules() if self.feature("rules") else None
        deny: list[str] = []
        no_network = False
        if rules is not None:
            if rules.get("readOnly"):
                yolo = False                     # every edit must reach the host to be refused
            denials = getattr(self.desktop_core, "agent_rule_denials", None)
            deny = list(denials(rules)) if callable(denials) else []
            no_network = bool(rules.get("noNetwork"))
            notes = _as_text(rules.get("notes")).strip()
            if notes:
                task += "\n\nProject instructions for this repository:\n" + notes
        self._agent_stream = stream
        self._agent_rules = rules
        self._agent_task_text = task
        self._agent_steps = {}
        self._agent_permission = None
        self._agent_outcome = None
        self._run_allow_all = allow_all
        self._agent_session_id = resume or None
        self._clear_resume_cards()
        self._agent_buffer = b""
        self._agent_yolo = yolo
        self._last_agent_task = task
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
            argv, cwd = self.harness_bridge.build(model=model, task=task,
                                                  cwd=workspace, yolo=yolo,
                                                  stream=stream, deny=deny,
                                                  no_network=no_network,
                                                  resume=resume if stream else "")
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
            self.tor_in_use = False
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
        if self._agent_stream:
            # JSON lines on stdout must not interleave with stderr chatter.
            process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
            process.readyReadStandardError.connect(self._read_harness_errors)
        process.setWorkingDirectory(cwd)
        process.readyReadStandardOutput.connect(self._read_harness_output)
        process.finished.connect(self._harness_finished)
        process.errorOccurred.connect(self._harness_process_error)
        process.start(argv[0], argv[1:])
        if self._agent_stream:
            # The handshake first - qwen never reads the task without it - then
            # the task itself, on stdin rather than in the process list.
            self._agent_write(
                self.desktop_core.agent_initialize_message()
                + self.desktop_core.agent_task_message(self._agent_task_text)
            )
        if self.feature("runPanel"):
            self.inspector.run_panel.start(self._run_meta())
            self.toggle_inspector(True)
            self.inspector.show_run_tab()
        self.set_status("Onionmind Agent is working…")
        self.inspector.append_activity("Agent run started; output is not yet proof of disk changes")

    def _read_harness_output(self) -> None:
        if self.harness_process is None:
            return
        if self._agent_stream:
            self._agent_buffer += bytes(self.harness_process.readAllStandardOutput())
            while b"\n" in self._agent_buffer:
                line, self._agent_buffer = self._agent_buffer.split(b"\n", 1)
                self._handle_agent_line(line.decode("utf-8", errors="replace"))
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
        self.tor_in_use = False
        normal = status == QProcess.ExitStatus.NormalExit
        if not normal and self._agent_outcome != "stopped":
            self._report_error("Agent runtime crashed", "node")
        if self._agent_stream:
            rest = self._agent_buffer.decode("utf-8", errors="replace")
            self._agent_buffer = b""
            if rest.strip():
                self._handle_agent_line(rest)
            self._clear_agent_cards()
            self._finish_run_panel(normal and exit_code == 0)
        suffix = f"\n\nAgent {'finished' if normal else 'crashed'} with exit code {exit_code}. Refreshing observed disk state."
        if self.stream_block is not None:
            self.stream_block.append_text(suffix)
        content = (self.stream_block.text if self.stream_block is not None else self.harness_output + suffix)
        self.chat_messages.append({"role": "assistant", "content": content})
        self._index_block(self.stream_block, len(self.chat_messages) - 1)
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
        self.tor_in_use = False
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
        if self.active_kind == "chat" and self.stop_event is None and self._web_ask_task:
            self._cancel_web_ask()
            return
        if self.active_kind == "chat" and self.stop_event is not None:
            self.stop_event.set()
            if self.stream_block is not None:
                self.stream_block.set_pending_label("Stopping")
            self.set_status("Stopping the local model after the current read…")
        elif self.active_kind == "agent":
            if self.harness_process is None:
                self.tor_in_use = False
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
                if self._agent_outcome is None:
                    self._agent_outcome = "stopped"
                self._clear_agent_cards()
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
            reference_normalizer=_optional_callable(self.desktop_core, "normalize_model_reference"),
            marker_for=_optional_callable(self.desktop_core, "uncensored_marker"),
            catalog_entry_label=self._catalog_entry_label,
        )
        self._model_dialog = dialog
        dialog.pullRequested.connect(lambda name: self.pull_model(name, dialog))
        dialog.catalogRequested.connect(lambda: self._fetch_model_catalog(dialog))
        dialog.exec()
        self._model_dialog = None

    def _catalog_entry_label(self, entry: Any) -> str:
        """Name on the first line, a plain description on the second. The
        refusal-removal flag is the dialog's own line, not this one."""
        fit = _optional_callable(self.desktop_core, "catalog_fit")
        describe = _optional_callable(self.desktop_core, "catalog_description")
        model_id = _as_text(_field(entry, "id"))
        fit_label = ""
        if callable(fit):
            vram, ram = self._catalog_specs
            fit_label = fit(model_id, vram, ram)[1]
        if callable(describe):
            return f"{model_id}\n{describe(entry, fit_label)}"
        downloads = _field(entry, "downloads", 0) or 0
        return f"{model_id}\n{downloads} downloads · {fit_label}".rstrip(" ·")

    def _fetch_model_catalog(self, dialog: ModelManagerDialog) -> None:
        """Fetch the popular GGUF list over Tor - fail closed, ordered by what
        this machine's RAM/VRAM can actually run."""
        fetcher = getattr(self.desktop_core, "fetch_hf_catalog", None)
        if not callable(fetcher):
            dialog.set_catalog_error("Model discovery needs the Onionmind desktop core.")
            return
        probe = getattr(self.core, "tor_proxy_port", None)
        try:
            listening = probe() if callable(probe) else None
        except Exception:
            listening = None
        port = self.update_bridge.tor_port()
        if not listening and port is None:
            dialog.set_catalog_error(
                "Tor is not up. Allow Tor search on a chat turn to start it, then "
                "try again - never over a direct connection."
            )
            return

        def job(signals: WorkerSignals) -> dict[str, Any]:
            del signals
            if port is None:
                # A listening SOCKS port is not proof of Tor; verify a circuit
                # before trusting it, exactly like the update check.
                verify = getattr(self.core, "tor_check", None)
                if callable(verify):
                    try:
                        verify()
                    except SystemExit as exc:
                        raise RuntimeError(
                            _as_text(exc) or "Tor could not be verified."
                        ) from None
            port_now = port or self.update_bridge.tor_port()
            if port_now is None:
                raise RuntimeError(
                    "The local proxy did not verify as Tor; refusing a direct model-list fetch."
                )
            memory = getattr(self.desktop_core, "machine_memory_mb", None)
            vram_probe = getattr(self.desktop_core, "gpu_vram_mb", None)
            ram = int(memory()) if callable(memory) else None
            vram = int(vram_probe()) if callable(vram_probe) else None
            return {"entries": fetcher(port_now), "vram": vram, "ram": ram}

        self._egress("Tor", "Model list · huggingface.co")

        def done(payload: dict[str, Any]) -> None:
            self._catalog_specs = (payload.get("vram"), payload.get("ram"))
            fit = _optional_callable(self.desktop_core, "catalog_fit")
            entries = list(payload.get("entries") or [])
            if callable(fit):
                vram, ram = self._catalog_specs
                entries.sort(
                    key=lambda entry: fit(_as_text(_field(entry, "id")), vram, ram)[0]
                )
            dialog.set_catalog(entries)

        worker = self._start_worker(job)
        worker.signals.result.connect(done)
        worker.signals.error.connect(dialog.set_catalog_error)

    def pull_model(self, name: str, dialog: ModelManagerDialog) -> None:
        pull = getattr(self.core, "pull_model", None)
        if not callable(pull):
            dialog.set_error("This Onionmind installation cannot add models yet")
            return
        stop_event = threading.Event()
        registry = "huggingface.co" if name.lower().startswith(("hf.co/", "huggingface.co/")) else "registry.ollama.ai"
        self._egress("Direct", f"Model download · {registry}")
        self._pulling = name
        self._refresh_boundary()

        def pull_job(signals: WorkerSignals) -> bool:
            def progress(fraction: float, status: str) -> None:
                signals.progress.emit(float(fraction), _as_text(status))

            return bool(pull(name, on_progress=progress, stop_event=stop_event))

        worker = self._start_worker(pull_job)
        worker.signals.progress.connect(dialog.set_progress)
        worker.signals.error.connect(dialog.set_error)
        worker.signals.result.connect(lambda ok: self._model_pull_complete(name, dialog, ok))
        worker.signals.finished.connect(self._pull_finished)

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
        SettingsDialog(
            self.data_root, self.harness_bridge.limitation, self.update_bridge, self
        ).exec()

    # --- Tor-routed self-update -----------------------------------------
    #
    # Permission first: the updater never opens a network connection on its
    # own. Automatic checks run only while the user has granted standing
    # permission in Settings (off by default); otherwise the network is
    # touched exclusively by pressing Check for updates or Download and
    # install. With permission granted, checks repeat for as long as the app
    # stays open - not just at startup - and always over a verified circuit.

    UPDATE_CHECK_INTERVAL_HOURS = 12

    def update_permission_enabled(self) -> bool:
        return bool(self.settings_data.get("updates_autocheck_enabled"))

    def set_update_permission(self, enabled: bool) -> None:
        self.settings_data["updates_autocheck_enabled"] = bool(enabled)
        self.settings_bridge.save(self.settings_data)
        if enabled:
            self._start_update_timer()
            # Granting permission is itself permission: check right away when
            # Tor is already verified, instead of waiting out the interval.
            self._maybe_autocheck_updates()
        elif self._update_timer is not None:
            self._update_timer.stop()

    def _start_update_timer(self) -> None:
        if self._update_timer is None:
            self._update_timer = QTimer(self)
            self._update_timer.setInterval(self.UPDATE_CHECK_INTERVAL_HOURS * 3600 * 1000)
            self._update_timer.timeout.connect(self._maybe_autocheck_updates)
        self._update_timer.start()

    def _set_update_notice(self, text: Optional[str]) -> None:
        self.update_status.setText(text or "Updates…")
        self.update_status.setProperty("attention", text is not None)
        style = self.update_status.style()
        style.unpolish(self.update_status)
        style.polish(self.update_status)
        self.update_status.setToolTip(
            (text + " Click to open Settings.") if text
            else "Check for a newer Onionmind build over Tor. Nothing is contacted until you ask."
        )

    def _maybe_autocheck_updates(self) -> None:
        if self.demo or not self.update_bridge.available:
            return
        if not self.update_permission_enabled():
            return  # No standing permission means no network, ever.
        if self.update_bridge.tor_port() is None:
            return
        pending = self.update_bridge.pending()
        if pending is not None:
            self.show_update_ready()
            return
        last_check = _as_text(self.settings_data.get("updates_last_check", ""))
        if last_check:
            try:
                checked_at = datetime.fromisoformat(last_check)
                age_hours = (datetime.now() - checked_at).total_seconds() / 3600
                if age_hours < self.UPDATE_CHECK_INTERVAL_HOURS:
                    return
            except ValueError:
                pass  # an unreadable timestamp means "check again", not "never check"
        bridge = self.update_bridge
        self._egress("Tor", "Update check · github.com")

        def autocheck_job(signals: WorkerSignals) -> Any:
            del signals
            bridge.housekeep()
            return bridge.check()

        def wire_autocheck(worker: SafeWorker) -> None:
            worker.signals.result.connect(self._autocheck_updates_done)
            worker.signals.error.connect(self._autocheck_updates_failed)

        self._start_worker(autocheck_job, wire_autocheck)

    def _autocheck_updates_done(self, manifest: Any) -> None:
        self.note_update_check(manifest)
        helper_state = getattr(self.desktop_core, "update_state", None)
        state = helper_state(self.update_bridge.revision(), manifest) if callable(helper_state) else "unavailable"
        if state != "available":
            self._set_update_notice(None)
            return
        short = getattr(self.desktop_core, "short_revision", lambda value: str(value)[:7])
        label = short(manifest.revision)
        self._set_update_notice(f"Update available · {label}")
        self.inspector.append_activity(
            f"Onionmind update available: revision {label}. The check ran through Tor; install from Settings."
        )

    def _autocheck_updates_failed(self, message: str) -> None:
        # Silent on the surface - a failed background check must not nag - but
        # it stays visible in the activity inspector, which is the honest log.
        self.inspector.append_activity(f"Background update check over Tor failed: {message}")

    def note_update_check(self, manifest: Any) -> None:
        revision = _field(manifest, "revision", None) if manifest is not None else None
        self.settings_data["updates_last_check"] = datetime.now().isoformat(timespec="seconds")
        if revision:
            self.settings_data["updates_seen_revision"] = _as_text(revision)
        self.settings_bridge.save(self.settings_data)

    def show_update_ready(self) -> None:
        self._set_update_notice("Update ready — restart to install")
        self.inspector.append_activity(
            "Onionmind update downloaded, verified, and staged. Restart from Settings to install it."
        )

    def restart_for_update(self, staging: str) -> None:
        try:
            command = self.update_bridge.apply_command(staging)
            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: the swap helper must
            # outlive this process without holding on to its console.
            creationflags = (0x00000008 | 0x00000200) if os.name == "nt" else 0
            subprocess.Popen(command, creationflags=creationflags, close_fds=True)
        except OSError as exc:
            self.set_status(f"Could not start the update installer: {exc}")
            return
        self.inspector.append_activity("Restarting Onionmind to apply the staged update")
        QApplication.instance().quit()

    # --- v2: webAsk -------------------------------------------------------

    def _web_ask_wanted(self, task: str) -> bool:
        matcher = getattr(self.desktop_core, "needs_web", None)
        return self.feature("webAsk") and callable(matcher) and bool(matcher(task))

    def _ask_before_search(self, task: str) -> None:
        """Pause a Chat turn that looks like it needs the web, and ask."""
        self._web_ask_generation += 1
        generation = self._web_ask_generation
        self._web_ask_task = task
        block = self.transcript.add_message("assistant", "")
        block.start_thinking("Thinking")
        self.stream_block = block
        QTimer.singleShot(900, lambda: self._show_web_ask(generation))

    def _show_web_ask(self, generation: int) -> None:
        if generation != self._web_ask_generation or not self._web_ask_task or self.active_kind != "chat":
            return
        self.transcript.remove_message(self.stream_block)
        self.stream_block = None
        tor_on = self.tor_phase == "running" and _tor_is_enabled(self.core)
        why = (
            "Tor search is off for this turn. If you allow it, this one query goes over Tor to "
            "DuckDuckGo’s onion service. Your prompt, files and the rest of the conversation stay "
            "on this machine."
            if tor_on else
            "Tor is off, so nothing can go out right now. If you allow it, Onionmind starts Tor, "
            "sends this one query to DuckDuckGo’s onion service, and leaves Tor on afterwards."
        )
        card = NoticeCard("ask", "Onionmind wants to search the web to answer this", why)
        query = getattr(self.desktop_core, "web_query", lambda text: text[:70])(self._web_ask_task)
        card.add_row("Query", query)
        card.add_button("Answer without searching", lambda: self._resolve_web_ask(False))
        allow = card.add_button(
            "Search once over Tor" if tor_on else "Turn on Tor and search once",
            lambda: self._resolve_web_ask(True), primary=True,
        )
        self._web_ask_card = self.transcript.add_widget(card)
        allow.setFocus(Qt.FocusReason.OtherFocusReason)
        self.set_status("Onionmind wants to search the web. Waiting for your permission.")
        self.inspector.append_activity("Asked permission to search the web")
        self._notify("Onionmind needs your approval", "It wants to search the web for this answer.")

    def _resolve_web_ask(self, allow: bool) -> None:
        if not self._web_ask_task or self.active_kind != "chat":
            return
        self.transcript.remove_widget(self._web_ask_card)
        self._web_ask_card = None
        self._web_ask_task = ""
        if allow:
            starting = self.tor_phase != "running"
            if not _tor_is_enabled(self.core):
                _set_tor_enabled(self.core, True)
            self.inspector.append_activity("Search allowed once over Tor")
            self._start_chat(True)
            self.set_status("Starting Tor for this search…" if starting else "Searching over Tor…")
        else:
            self.inspector.append_activity("Answered without searching; nothing left this machine")
            self._start_chat(False)
            self.set_status("Answering from the model alone, without the web.")

    def _cancel_web_ask(self) -> None:
        self._web_ask_generation += 1
        self._web_ask_task = ""
        self.transcript.remove_widget(self._web_ask_card)
        self._web_ask_card = None
        self.transcript.remove_message(self.stream_block)
        self.stream_block = None
        self.set_status("Stopped before anything was sent.")
        self.inspector.append_activity("Run stopped by you before a search was allowed")
        self._set_active(None)
        self.save_current_session()

    # --- v2: tool steps (cards, or the timeline) ---------------------------

    def _tool_started(self, title: str, detail: str, state_text: str, key: Optional[str] = None) -> None:
        if self.feature("timeline"):
            self._open_steps[key or title] = self.transcript.add_timeline_step(title, detail, "running")
        else:
            self.transcript.add_tool_card(title, [(detail, state_text)])

    def _tool_finished(self, title: str, row_label: str, state_text: str,
                       ok: bool = True, key: Optional[str] = None, state: Optional[str] = None) -> None:
        step = self._open_steps.pop(key or title, None)
        final = state or ("done" if ok else "error")
        if self.feature("timeline"):
            if step is not None:
                step.set_state(final, state_text)
            else:
                self.transcript.add_timeline_step(title, state_text, final)
        else:
            self.transcript.add_tool_card(title, [(row_label, state_text)])

    # --- v2: structured Agent runs (approvals, rules, Run tab, timeline) ---

    AGENT_TOOL_LABELS = {
        "read_file": "Read file", "read_many_files": "Read files", "list_directory": "List folder",
        "glob": "Find files", "grep_search": "Search project", "search_file_content": "Search project",
        "edit": "Edit file", "write_file": "Edit file", "replace": "Edit file",
        "notebook_edit": "Edit file", "run_shell_command": "Shell command",
        "web_fetch": "Fetch over Tor", "web_search": "Search over Tor",
        "mcp__onionmind__web_search": "Search over Tor", "mcp__onionmind__web_fetch": "Fetch over Tor",
    }

    def _uses_agent_stream(self) -> bool:
        if not callable(getattr(self.desktop_core, "parse_agent_line", None)):
            return False
        return any(self.feature(key) for key in ("approvals", "runPanel", "timeline", "rules"))

    def _project_rules(self) -> Optional[dict[str, Any]]:
        loader = getattr(self.desktop_core, "load_rules", None)
        if not callable(loader) or not self.workspace:
            return None
        return dict(loader(self.settings_data, self.workspace))

    def _run_meta(self) -> str:
        display = getattr(self.desktop_core, "describe_model", None)
        tier = ""
        if callable(display):
            try:
                tier = _as_text(_field(display(self.current_model_id()), "tier", "")) or ""
            except Exception:
                tier = ""
        network = "no network (project rule)" if self._agent_rules and self._agent_rules.get("noNetwork") else "Tor for proxy-aware traffic"
        approvals = (
            "YOLO" if getattr(self, "_agent_yolo", False)
            else "asks before edits" if self.feature("approvals")
            else "protected actions stop safely"
        )
        return f"Agent · {tier or self.current_model_id()} · {network} · {approvals}"

    def _agent_write(self, text: str) -> None:
        process = self.harness_process
        if process is not None and process.state() != QProcess.ProcessState.NotRunning:
            process.write(text.encode("utf-8"))

    def _agent_log(self, text: str) -> None:
        """One readable line to the terminal and the Run tab output."""
        clean = _brand_runtime_text(ANSI_ESCAPE.sub("", text)).rstrip()
        if not clean:
            return
        self.terminal.append(clean + "\n")
        self.inspector.run_panel.append_output(clean)

    def _read_harness_errors(self) -> None:
        if self.harness_process is None:
            return
        chunk = bytes(self.harness_process.readAllStandardError()).decode("utf-8", errors="replace")
        for line in chunk.splitlines():
            self._agent_log(line)

    def _handle_agent_line(self, line: str) -> None:
        parse = getattr(self.desktop_core, "parse_agent_line", None)
        event = parse(line) if callable(parse) else {"kind": "text", "text": line}
        kind = event.get("kind")
        if kind == "text":
            text = _brand_runtime_text(event.get("text", ""))
            self._agent_log(text)
            if self.stream_block is not None:
                self.stream_block.append_text(text + "\n")
        elif kind == "batch":
            for item in event.get("events", []):
                sub = item.get("kind")
                if sub == "say":
                    if self.stream_block is not None:
                        self.stream_block.append_text("\n" + item.get("text", "") + "\n")
                    self._agent_log(item.get("text", ""))
                elif sub == "tool_use":
                    self._agent_tool_use(item)
                elif sub == "tool_result":
                    self._agent_tool_result(item)
        elif kind == "permission":
            self._agent_permission_requested(event)
        elif kind == "system" and event.get("session_id"):
            # qwen's own session id: what "Continue this run" resumes.
            self._agent_session_id = _as_text(event.get("session_id"))
        elif kind == "result":
            # The turn is over; closing stdin lets qwen exit on its own.
            if self.harness_process is not None:
                self.harness_process.closeWriteChannel()
        self.transcript._scroll_later()

    def _agent_tool_use(self, item: dict[str, Any]) -> None:
        tool = _as_text(item.get("tool"))
        tool_input = item.get("input") or {}
        if tool in ("todo_write", "TodoWrite"):
            todos = tool_input.get("todos") if isinstance(tool_input, dict) else None
            if isinstance(todos, list):
                self.inspector.run_panel.set_todos(todos)
            return
        describe = getattr(self.desktop_core, "describe_tool_request", None)
        detail = describe(tool, tool_input, self.workspace) if callable(describe) else tool
        label = self.AGENT_TOOL_LABELS.get(tool, tool.replace("_", " ").strip().title() or "Tool")
        key = _as_text(item.get("id")) or detail
        self._agent_steps[key] = {"label": label, "detail": detail, "tool": tool}
        self.inspector.run_panel.set_step(key, "running", detail)
        self._agent_log(f"› {detail}")
        if self.feature("timeline"):
            self._tool_started(label, detail, "Running", key=key)

    def _agent_tool_result(self, item: dict[str, Any]) -> None:
        key = _as_text(item.get("id"))
        step = self._agent_steps.get(key, {"label": "Tool", "detail": key})
        text = _as_text(item.get("text"))
        lowered = text.casefold()
        denied = "denied by permission rules" in lowered or "operation cancelled" in lowered
        state = "denied" if denied else ("error" if item.get("error") else "done")
        summary = {"denied": "Refused · nothing written", "error": "Failed", "done": "OK"}[state]
        if state == "done" and step.get("tool") in ("edit", "write_file", "replace"):
            summary = "Written · check Changes for what is on disk"
        self.inspector.run_panel.set_step(key, state, None, summary)
        first = text.strip().splitlines()[0][:160] if text.strip() else summary
        self._agent_log(f"  {first}")
        self._tool_finished(step["label"], step["detail"], summary, ok=state == "done", key=key, state=state)

    def _agent_permission_requested(self, event: dict[str, Any]) -> None:
        core = self.desktop_core
        tool = _as_text(event.get("tool"))
        tool_input = event.get("input") or {}
        request_id = _as_text(event.get("request_id"))
        describe = getattr(core, "describe_tool_request", None)
        what = describe(tool, tool_input, self.workspace) if callable(describe) else tool
        decision, reason = "ask", ""
        if self._agent_rules is not None and callable(getattr(core, "rule_decision", None)):
            decision, reason = core.rule_decision(self._agent_rules, tool, tool_input, self.workspace)
        project = Path(self.workspace).name if self.workspace else "this project"
        if decision == "allow":
            self._agent_write(core.agent_permission_response(request_id, True, tool_input))
            self.inspector.append_activity(f"Project rule: {reason} ({project}) - {what}")
            return
        if decision == "deny":
            self._agent_write(core.agent_permission_response(request_id, False, message=f"Refused: {reason}."))
            self.inspector.append_activity(f"Refused by a project rule: {reason}")
            self._agent_log(f"  refused: {reason}")
            return
        if self._run_allow_all:
            self._agent_write(core.agent_permission_response(request_id, True, tool_input))
            self.inspector.append_activity(f"Allowed for this run: {what}")
            return
        key = _as_text(event.get("tool_use_id")) or what
        if not self.feature("approvals"):
            # Today's contract: where nobody is asked, a protected action stops
            # the run safely instead of continuing.
            self._agent_write(core.agent_permission_response(
                request_id, False,
                message="Headless runs can't ask for approval, so Onionmind stopped instead of continuing."))
            self._agent_write(core.agent_interrupt_message())
            self._agent_outcome = f"halted:{what}"
            self.inspector.run_panel.set_step(key, "stopped", None,
                                              "Stopped before the edit: a headless run has no one to ask")
            self.inspector.append_activity(f"Stopped before a protected action: {what}")
            return
        self._agent_permission = {"request_id": request_id, "input": tool_input, "what": what, "key": key}
        added, removed = getattr(core, "edit_size", lambda *_a: (0, 0))(tool, tool_input)
        is_edit = tool in getattr(core, "AGENT_EDIT_TOOLS", ())
        size = f" · +{added} / −{removed}" if is_edit and (added or removed) else ""
        title = "Onionmind Agent wants to edit a file" if is_edit else (
            "Onionmind Agent wants to run a command" if tool in getattr(core, "AGENT_SHELL_TOOLS", ())
            else "Onionmind Agent wants to use a protected tool")
        target = what.split(" ", 1)[1] if " " in what else what
        consequence = "Nothing is written until you allow it." if is_edit else "Nothing runs until you allow it."
        card = NoticeCard("ask", title, f"{target}{size} · inside {project}. {consequence}")
        self._approval_buttons(card)
        self._agent_cards.append(self.transcript.add_widget(card))
        if self.feature("runPanel"):
            run_card = NoticeCard("ask", f"{what[0].upper() + what[1:]}?",
                                  (f"+{added} / −{removed} in one file inside this project. " if is_edit else "")
                                  + consequence)
            self._approval_buttons(run_card)
            self.inspector.run_panel.show_approval(run_card)
            self.inspector.run_panel.set_status("Waiting for you")
        self.inspector.run_panel.set_step(key, "waiting", None)
        if self.stream_block is not None:
            self.stream_block.set_pending_label("Waiting for your approval")
        self.set_status(f"Waiting for your approval: {what}")
        self.inspector.append_activity(f"Waiting for approval: {what}")
        self._notify("Onionmind needs your approval", f"Agent wants to {what}.")

    def _approval_buttons(self, card: "NoticeCard") -> None:
        card.add_button("Deny", lambda: self._answer_permission("deny"))
        card.add_button("Allow for this run", lambda: self._answer_permission("run"))
        allow = card.add_button("Allow once", lambda: self._answer_permission("once"), primary=True)
        QTimer.singleShot(0, lambda: allow.setFocus(Qt.FocusReason.OtherFocusReason))

    def _answer_permission(self, answer: str) -> None:
        pending = self._agent_permission
        if pending is None:
            return
        self._agent_permission = None
        self._clear_agent_cards()
        core = self.desktop_core
        what = pending["what"]
        if answer == "deny":
            self._agent_write(core.agent_permission_response(
                pending["request_id"], False, message="Denied by the person running Onionmind."))
            # Deny stops the run there: qwen ends the turn without another
            # model call (checked against qwen-code 0.25.0).
            self._agent_write(core.agent_interrupt_message())
            self._agent_outcome = f"denied:{what}"
            self.inspector.run_panel.set_step(pending["key"], "denied", None)
            self.inspector.run_panel.set_status("Edit denied")
            self.set_status("Edit denied. Nothing was written for it.")
            self.inspector.append_activity(f"Denied: {what}")
            return
        self._agent_write(core.agent_permission_response(pending["request_id"], True, pending["input"]))
        if answer == "run":
            self._run_allow_all = True
            self.inspector.append_activity("Edits allowed for the rest of this run")
        else:
            self.inspector.append_activity("Edit allowed once")
        self.inspector.run_panel.set_step(pending["key"], "running", None)
        self.inspector.run_panel.set_status("Running")
        self.set_status("Onionmind Agent is working…")

    def _clear_agent_cards(self) -> None:
        for holder in self._agent_cards:
            self.transcript.remove_widget(holder)
        self._agent_cards = []
        self.inspector.run_panel.clear_approval()

    def _finish_run_panel(self, clean_exit: bool) -> None:
        outcome = self._agent_outcome or ""
        what = outcome.split(":", 1)[1] if ":" in outcome else ""
        if outcome.startswith("denied"):
            status = "Edit denied"
            note = f"You denied {what}, so Onionmind stopped there. Nothing was written for it."
        elif outcome.startswith("halted"):
            status = "Stopped before an edit"
            note = (f"Stopped before a protected action: {what}. Headless runs can’t ask for approval, "
                    "so Onionmind stopped instead of continuing. That action was not run.")
        elif outcome == "stopped":
            status, note = "Stopped", "Stopped. Onionmind ended the run and cancelled the process it had started."
        else:
            status, note = ("Done" if clean_exit else "Stopped"), ""
        self.inspector.run_panel.finish(status)
        if note and self.stream_block is not None:
            self.stream_block.append_text("\n" + note)
        if note and self._agent_stream and self._agent_session_id:
            self._offer_resume(halted=outcome.startswith("halted"))
        if status == "Done":
            self._notify("Agent run finished", f"{self._session_title()} · {Path(self.workspace or '').name}")

    def _offer_resume(self, halted: bool) -> None:
        """A stopped run keeps its qwen session, so it can carry on from there
        with its context intact instead of being sent again from scratch."""
        if halted:
            card = NoticeCard("info", "Continue this run?",
                              "The agent picks up where it stopped, with what it already read and "
                              "planned. It stopped because a headless run can't ask, so continuing "
                              "allows its edits and commands for the rest of this run.")
            card.add_button("Continue, allowing edits", lambda: self._continue_run(True), primary=True)
        else:
            card = NoticeCard("info", "Continue this run?",
                              "The agent picks up where it stopped, with what it already read and planned. "
                              "Protected actions still ask first.")
            card.add_button("Continue this run", lambda: self._continue_run(False), primary=True)
        self._resume_cards.append(self.transcript.add_widget(card))

    def _clear_resume_cards(self) -> None:
        for holder in getattr(self, "_resume_cards", []):
            self.transcript.remove_widget(holder)
        self._resume_cards = []

    def _continue_run(self, allow_all: bool) -> None:
        session = self._agent_session_id
        if self.active_kind or not session:
            return
        if not _tor_is_enabled(self.core):
            self.set_status("Tor proxy is off. Turn it on in the toolbar before using a protected feature.")
            return
        text = "Continue where you stopped."
        self.set_mode("agent")
        self.chat_messages.append({"role": "user", "content": text})
        self._index_block(self.transcript.add_message("user", text), len(self.chat_messages) - 1)
        self._set_active("agent")
        self.inspector.append_activity("Continuing the stopped Agent run in the same session")
        self._start_harness(text, resume=session, allow_all=allow_all)

    # --- v2: model power (ladder), model per mode and live stats ----------

    def _model_menu_headers(self) -> list[str]:
        headers: list[str] = []
        if self.feature("ladder"):
            power = self._model_power
            name, total = power.get("gpu_name"), power.get("vram_total")
            if total:
                machine = f"This machine: {total / 1024:.0f} GB GPU" + (f" ({name})" if name else "")
            elif power.get("ram"):
                machine = f"This machine: no GPU reported · {power['ram'] / 1024:.0f} GB RAM"
            else:
                machine = "This machine: checking memory…" if not power else "This machine: memory unknown"
            headers.append(f"{machine}. Switching unloads the current tier first.")
        if self.feature("modelPlus"):
            per_mode = self.settings_data.get("model_by_mode") or {}
            chat = per_mode.get("chat") or self.current_model_id()
            agent = per_mode.get("agent") or self.current_model_id()
            headers.append(f"Chat uses {self._tier_name(chat)} · Agent uses {self._tier_name(agent)}"
                           " · picking a tier sets it for the current mode")
        return headers

    def _refresh_model_headers(self) -> None:
        """Rewrite the menu's header rows in place (a rebuild would re-enter
        the combo's own change signal)."""
        headers = iter(self._model_menu_headers())
        for index in range(self.model_combo.count()):
            if self.model_combo.itemData(index) is None:
                text = next(headers, None)
                if text is not None:
                    self.model_combo.setItemText(index, text)

    def _tier_name(self, model: str) -> str:
        helper = getattr(self.desktop_core, "describe_model", None)
        if callable(helper):
            try:
                tier = _as_text(_field(helper(model), "tier", ""))
                if tier:
                    return tier
            except Exception:
                pass
        return model

    def _model_side_label(self, model: str) -> str:
        if not self.feature("ladder") or not self._model_power:
            return ""
        power = self._model_power
        loaded = power.get("loaded") or []
        if any(_as_text(item.get("name")).split(":")[0] == model.split(":")[0] or item.get("name") == model
               for item in loaded):
            return "In use"
        sizes = power.get("sizes") or {}
        size = sizes.get(model) or sizes.get(f"{model}:latest")
        fit = getattr(self.desktop_core, "model_fit", None)
        return fit(size, power.get("vram_total"), power.get("ram")) if callable(fit) else ""

    def _probe_model_power(self) -> None:
        """Real numbers for the ladder and the status bar, read off the UI thread."""
        core, desktop_core = self.core, self.desktop_core

        def job(signals: WorkerSignals) -> dict[str, Any]:
            del signals
            details = getattr(core, "installed_model_details", None)
            loaded = getattr(core, "loaded_models", None)
            memory = getattr(desktop_core, "gpu_memory_mb", None)
            name = getattr(desktop_core, "gpu_name", None)
            ram = getattr(desktop_core, "machine_memory_mb", None)
            used, total = memory() if callable(memory) else (None, None)
            return {
                "sizes": {item["name"]: item.get("size") for item in (details() if callable(details) else [])},
                "loaded": list(loaded()) if callable(loaded) else [],
                "vram_used": used, "vram_total": total,
                "gpu_name": name() if callable(name) and total else None,
                "ram": ram() if callable(ram) else None,
            }

        def done(power: dict[str, Any]) -> None:
            self._model_power = power
            if self.feature("ladder") or self.feature("modelPlus"):
                self.set_model_options(self.installed_model_ids, self.current_model_id())
            self._update_live_status()

        worker = self._start_worker(job)
        worker.signals.result.connect(done)

    def _model_picked(self) -> None:
        """A person chose a tier in the menu: with ladder on, make it the one in memory."""
        self._switch_loaded_model(self.current_model_id())

    def _switch_loaded_model(self, model: str) -> None:
        if not self.feature("ladder") or self.active_kind or not model:
            return
        load = getattr(self.core, "load_model", None)
        unload = getattr(self.core, "unload_model", None)
        loaded_now = getattr(self.core, "loaded_models", None)
        if not callable(load) or getattr(self.core, "BACKEND", "ollama") not in ("ollama", None):
            return
        previous = [item.get("name") for item in (self._model_power.get("loaded") or [])
                    if item.get("name") and item.get("name") != model]
        self._model_loading = model
        label = self._describe_model(model)
        old = ", ".join(self._tier_name(name) for name in previous)
        self.set_status(f"Unloading {old} and loading {self._tier_name(model)} into memory…" if old
                        else f"Loading {self._tier_name(model)} into memory…")
        self.model_status.set_status(f"Loading {self._tier_name(model)}…", "busy")
        self.model_status.setVisible(True)
        self.model_loading.show()
        self.disclosure.setText(f"Loading {self._tier_name(model)}… sending waits until it is ready")
        self.model_combo.setEnabled(False)
        self._sync_action_states()

        def job(signals: WorkerSignals) -> dict[str, Any]:
            del signals
            current = list(loaded_now()) if callable(loaded_now) else []
            for item in current:
                name = item.get("name")
                if name and name != model and callable(unload):
                    unload(name)
            load(model)
            return {"loaded": list(loaded_now()) if callable(loaded_now) else []}

        def done(payload: dict[str, Any]) -> None:
            self._model_loading = None
            self._model_power["loaded"] = payload.get("loaded") or []
            self._model_load_finished(True, f"{self._tier_name(model)} · {label} is loaded and ready")
            self.inspector.append_activity(f"{self._tier_name(model)} loaded into memory")

        def failed(message: str) -> None:
            self._model_loading = None
            self._model_load_finished(False, f"Could not load {self._tier_name(model)}: {message}")
            self._report_error(message)

        worker = self._start_worker(job)
        worker.signals.result.connect(done)
        worker.signals.error.connect(failed)

    def _model_load_finished(self, ok: bool, message: str) -> None:
        self.model_loading.hide()
        self.model_combo.setEnabled(not self.active_kind)
        self.model_status.set_status("Ready" if ok else "Unavailable", "good" if ok else "bad")
        self.model_status.setVisible(self.width() >= 1280)
        self.disclosure.setText(
            "Private local chat · Tor search needs one-turn permission" if self.mode == "chat"
            else "Early access · Agent network access is separate from Tor search"
        )
        self.set_status(message)
        self.set_model_options(self.installed_model_ids, self.current_model_id())
        self._sync_action_states()
        self._update_live_status()

    def _report_error(self, message: str, fallback: Optional[str] = None) -> None:
        """Turn a real failure into a fault card when the errors feature is on."""
        if not self.feature("errors"):
            return
        classify = getattr(self.desktop_core, "classify_error", None)
        kind = (classify(message) if callable(classify) else None) or fallback
        if kind in self.FAULTS:
            self._show_fault(kind, message)

    def _generation_event(self, kind: str, event: dict[str, Any]) -> None:
        if kind == "generating":
            seconds = float(event.get("seconds") or 0)
            if seconds > 0:
                self._live_tps = float(event.get("chunks") or 0) / seconds
            if self.feature("thinking") and self.stream_block is not None:
                self.stream_block.set_pending_label(
                    "Thinking" if event.get("reasoning") else "Filtering private reasoning")
        else:
            self._last_stats = dict(event)
            if event.get("tokens_per_second"):
                self._live_tps = float(event["tokens_per_second"])
            self._turn_reasoning += float(event.get("reasoning_seconds") or 0)
            prompt = event.get("prompt_eval_count")
            if isinstance(prompt, (int, float)):
                self._context_used_real = int(prompt) + int(event.get("eval_count") or 0)
            self._update_context_meter()
        self._update_live_status()

    def _update_live_status(self) -> None:
        if not self.feature("modelPlus"):
            return
        running = self.active_kind == "chat"
        self.speed_status.setText(
            f"{self._live_tps:.0f} tok/s" if running and self._live_tps else "Idle")
        used, total = self._model_power.get("vram_used"), self._model_power.get("vram_total")
        in_vram = sum(int(item.get("size_vram") or 0) for item in self._model_power.get("loaded") or [])
        if total and used is not None:
            self.vram_status.setText(f"VRAM {used / 1024:.1f} / {total / 1024:.0f} GB")
        elif total:
            # Unified memory (Apple): the GPU reports no used figure of its own,
            # so show what the model server has placed in GPU memory.
            self.vram_status.setText(f"VRAM {in_vram / 1024 ** 3:.1f} / {total / 1024:.0f} GB")
        else:
            self.vram_status.setText(f"{in_vram / 1024 ** 3:.1f} GB in VRAM" if in_vram else "VRAM unknown")
        used_ctx, limit = self._context_numbers()
        fmt = getattr(self.desktop_core, "format_tokens", lambda n: str(n))
        self.ctx_status.setText(f"ctx {fmt(used_ctx)} / {fmt(limit)}")

    def _after_reply(self, stopped: bool) -> None:
        """What v2 adds under a finished Chat reply."""
        if self.feature("provenance") and self.stream_block is not None and not stopped:
            chips = [(f"Answered on this machine · {self._tier_name(self.current_model_id())}",
                      "Inference ran on this computer; the reply never left it.")]
            if self._turn_searches:
                results = sum(int(item.get("results") or 0) for item in self._turn_searches)
                domains = sorted({d for item in self._turn_searches for d in item.get("domains", [])})
                count = len(self._turn_searches)
                chips.append((f"{count} Tor search{'es' if count != 1 else ''} · {results} result{'s' if results != 1 else ''}",
                              "Results from: " + (", ".join(domains) if domains else "no sites")))
            self.transcript.add_widget(ProvenanceChips(chips))
        if self.feature("thinking") and self._turn_reasoning > 0 and self.stream_block is not None:
            self.transcript.add_widget(ThoughtLine(self._turn_reasoning))
        self._live_tps = self._live_tps if not stopped else None
        if self.feature("modelPlus"):
            self._probe_model_power()
        self._update_live_status()
        if not stopped:
            self._notify("Onionmind replied",
                         f"{self._session_title()} · {Path(self.workspace).name if self.workspace else 'no project'}")

    # --- v2: context meter -------------------------------------------------

    def _context_numbers(self) -> tuple[int, int]:
        """(estimated tokens the next turn sends, the window it must fit)."""
        estimate = getattr(self.desktop_core, "estimate_tokens", None)
        count = estimate if callable(estimate) else (lambda *parts: sum(len(str(p)) for p in parts) // 4)
        if getattr(self, "mode", "agent") == "agent":
            limit = int(self.context_steps[self.context_slider.value()]) if hasattr(self, "context_slider") else 32768
        else:
            limit = int(getattr(self.core, "NUM_CTX", 0) or self.preferences.get("context_window", 16384))
        parts: list[Any] = []
        if self._context_used_real is not None and self.mode == "chat":
            used = self._context_used_real
        else:
            used = 0
            parts.append(self.chat_messages if self.mode == "chat" else "")
        if self.feature("presets") and self.mode == "chat":
            parts.append(getattr(self, "_preset_text", lambda: "")())
        if self.mode == "agent":
            parts.append(self._agents_md_text())
        parts.append(self.composer.toPlainText())
        files_chars = 0
        if self.workspace:
            root = Path(self.workspace)
            budget = 256 * 1024                  # the same caps _build_user_payload applies
            for relative in self.inspector.selected_context_files()[:12]:
                try:
                    size = min((root / relative).stat().st_size, MAX_TEXT_FILE_BYTES, budget)
                except OSError:
                    continue
                files_chars += size
                budget -= size
        return used + count(*parts) + (files_chars + 3) // 4, max(1, limit)

    def _agents_md_text(self) -> str:
        agents = (self.current_snapshot or {}).get("agents_files") or []
        if not agents or not self.workspace:
            return ""
        try:
            return (Path(self.workspace) / agents[0]).read_text(encoding="utf-8", errors="replace")[:65536]
        except OSError:
            return ""

    def _update_context_meter(self) -> None:
        # The composer is built before the inspector and the preferences;
        # nothing to measure until the whole window exists.
        if not hasattr(self, "context_box") or not hasattr(self, "inspector") or not hasattr(self, "preferences"):
            return
        on = self.feature("contextMeter")
        self.context_box.setVisible(on)
        if not on:
            self.context_warning.hide()
            return
        used, limit = self._context_numbers()
        fmt = getattr(self.desktop_core, "format_tokens", lambda n: str(n))
        share = used / limit
        self.context_meter.setValue(int(min(1.0, share) * 1000))
        level = "warn" if share > 0.8 else ""
        if self.context_meter.property("level") != level:
            self.context_meter.setProperty("level", level)
            self.context_meter.style().unpolish(self.context_meter)
            self.context_meter.style().polish(self.context_meter)
        self.context_meter_label.setText(f"{fmt(used)} / {fmt(limit)}")
        files = len(self.inspector.selected_context_files())
        agent_note = " The agent adds its own instructions on top of this." if self.mode == "agent" else ""
        self.context_box.setToolTip(
            f"Context: {used:,} of {limit:,} tokens. "
            + ("System prompt, " if self.feature("presets") and self.mode == "chat" else "")
            + ("AGENTS.md, " if self.mode == "agent" else "")
            + f"{files} selected file(s) and this session’s messages."
            + (" Measured by the model after the last reply." if self._context_used_real and self.mode == "chat"
               else " Estimated at four characters per token.")
            + agent_note
        )
        if share >= 1.0:
            self.context_warning_label.setText(
                "This session is over the model’s context. The oldest messages are left out of the next reply.")
        elif share > 0.8:
            self.context_warning_label.setText(
                f"This session uses {share:.0%} of the context. Older messages will be left out soon. "
                "Deselect files or start a new task.")
        self.context_warning.setVisible(share > 0.8)

    def _apply_feature_ladder(self, on: bool) -> None:
        if on and not self._model_power:
            self._probe_model_power()
        self.set_model_options(self.installed_model_ids, self.current_model_id())

    def _apply_feature_modelPlus(self, on: bool) -> None:
        for label in (self.speed_status, self.vram_status, self.ctx_status):
            label.setVisible(on)
        if on and not self._model_power:
            self._probe_model_power()
        self.set_model_options(self.installed_model_ids, self.current_model_id())
        self._update_live_status()

    def _apply_feature_contextMeter(self, on: bool) -> None:
        del on
        self._update_context_meter()

    # --- v2: egress log, boundary map, palette, keyboard, zones, netlog ----

    def _egress(self, route: str, what: str) -> None:
        """Record one connection that left this machine - times and
        destinations only - while the netlog or boundary feature is on."""
        if not (self.feature("netlog") or self.feature("boundary")):
            return
        log_type = getattr(self.desktop_core, "EgressLog", None)
        if log_type is None:
            return
        log_type(self.egress_path).append(route, what)
        counts = self._today_counts()
        counts["tor" if route == "Tor" else "direct"] = counts.get("tor" if route == "Tor" else "direct", 0) + 1
        if what.startswith("Chat search"):
            counts["search"] = counts.get("search", 0) + 1
        self._refresh_boundary()

    def _today_counts(self) -> dict[str, int]:
        today = datetime.now().strftime("%Y-%m-%d")
        if self._egress_counts is None or getattr(self, "_egress_counts_day", "") != today:
            log_type = getattr(self.desktop_core, "EgressLog", None)
            self._egress_counts = log_type(self.egress_path).counts(today) if log_type else {}
            self._egress_counts_day = today
        return self._egress_counts

    def toggle_boundary(self, visible: Optional[bool] = None) -> None:
        if not self.feature("boundary"):
            return
        target = self.boundary_panel.isHidden() if visible is None else bool(visible)
        self.boundary_panel.setVisible(target)
        self.boundary_button.setChecked(target)
        self._refresh_boundary()

    def _refresh_boundary(self) -> None:
        if not hasattr(self, "boundary_panel") or not self.feature("boundary"):
            return
        counts = self._today_counts()
        searches, direct = counts.get("search", 0), counts.get("direct", 0)
        open_path = self.tor_phase == "running" and _tor_is_enabled(self.core)
        label = f"{searches} search{'es' if searches != 1 else ''} today" if searches else "Nothing left today"
        self.boundary_button.set_state(open_path, self._searching, label)
        if self.boundary_panel.isHidden():
            return
        panel = self.boundary_panel
        panel.receipt.setText(
            f"Today: {searches} search quer{'y' if searches == 1 else 'ies'} left over Tor · "
            f"{direct} direct download{'s' if direct != 1 else ''} · no telemetry")
        model = self.current_model_id()
        if self._model_loading:
            model_text = f"Loading {self._tier_name(self._model_loading)}…"
        else:
            model_text = f"{self._tier_name(model)} · {self._describe_model(model)} · ready"
        rows = panel.machine_rows
        rows["Model"].setText(model_text)
        rows["Project"].setText(f"{Path(self.workspace).name} · {self.branch_label.text()}" if self.workspace
                                else "No project open")
        changes = len((self.current_snapshot or {}).get("changes") or [])
        rows["Disk"].setText(f"{changes} observed change{'s' if changes != 1 else ''}")
        rows["Sessions"].setText("Saved on this machine" if getattr(self, "preferences", {}).get("save_history", True)
                                 else "Not saved")
        panel.node_state.setText({
            "running": "Circuit verified", "starting": "Checking the circuit…", "probing": "Checking the circuit…",
        }.get(self.tor_phase, "Nothing can leave over Tor"))
        out = panel.outside_rows
        if not open_path and self.tor_phase not in ("starting", "probing"):
            out["Chat search"].setText("Blocked · Tor is off")
        elif self.search_consent.isChecked() or (self.active_kind == "chat" and self.tor_in_use):
            out["Chat search"].setText("Allowed this turn")
        else:
            out["Chat search"].setText("Not allowed this turn")
        rules = self._project_rules() if self.feature("rules") else None
        out["Agent network"].setText("Closed by this project’s rules" if rules and rules.get("noNetwork")
                                     else "Tor bridge · proxy-ignoring commands not covered")
        out["Downloads"].setText(f"Downloading {self._pulling} directly…" if self._pulling
                                 else "Direct, only after you confirm")
        out["Updates"].setText("Over Tor, checked automatically while Tor is up" if self.update_permission_enabled()
                               else "Over Tor, only when you check")

    def _pull_finished(self) -> None:
        self._pulling = None
        self._refresh_boundary()

    def _sync_toolbar_features(self) -> None:
        width = self.width()
        self.palette_button.setVisible(self.feature("palette") and width >= 1000)
        self.boundary_button.setVisible(self.feature("boundary") and width >= 860)
        self.boundary_button.set_label_visible(width >= 1200)
        if width >= 1200 and self.toolbar_layout.minimumSize().width() > width:
            # Everything on at once can over-fill the toolbar; the label goes
            # first so no control is drawn over another.
            self.boundary_button.set_label_visible(False)
        lock = getattr(self, "lock_button", None)
        if lock is not None:
            lock.setVisible(self.feature("lock") and width >= 1000)

    def _palette_rows(self) -> list[tuple[str, str, str, Callable[[], None]]]:
        rows: list[tuple[str, str, str, Callable[[], None]]] = []
        for index in range(self.left_rail.projects.count()):
            item = self.left_rail.projects.item(index)
            path = _as_text(item.data(Qt.ItemDataRole.UserRole))
            hint = "current" if _path_key(path) == _path_key(self.workspace) else path
            rows.append(("Projects", Path(path).name or path, hint, lambda p=path: self.select_workspace(p)))
        for index in range(self.left_rail.sessions.count()):
            item = self.left_rail.sessions.item(index)
            title, _, when = item.text().partition("\n")
            session_id = _as_text(item.data(Qt.ItemDataRole.UserRole))
            rows.append(("Sessions", title, when, lambda sid=session_id: self.load_session(sid)))
        for model in self.installed_model_ids:
            hint = "in use" if model == self.current_model_id() else ""

            def pick(m: str = model) -> None:
                index = self.model_combo.findData(m)
                if index >= 0:
                    self.model_combo.setCurrentIndex(index)
                    self._model_picked()

            rows.append(("Model power", f"{self._tier_name(model)} · {self._describe_model(model)}", hint, pick))
        native = lambda seq: QKeySequence(seq).toString(QKeySequence.SequenceFormat.NativeText)
        commands: list[tuple[str, str, Callable[[], None], bool]] = [
            ("New task", native("Ctrl+N"), lambda: self.new_task(), True),
            ("Toggle terminal", native("Ctrl+`"), lambda: self.toggle_terminal(), True),
            ("Review changes", "", lambda: (self.toggle_inspector(True), self.inspector.tabs.setCurrentWidget(self.inspector.changes_tab)), True),
            ("Show the Run tab", "", lambda: (self.toggle_inspector(True), self.inspector.show_run_tab()), self.feature("runPanel")),
            ("Turn Tor off" if self.tor_phase == "running" else "Turn Tor on", "", self._toggle_tor, True),
            ("Models", "", self.open_model_manager, True),
            ("Settings", native("Ctrl+,"), self.open_settings, True),
            ("New features", "", self.open_features_popover, True),
            ("Export conversation", native("Ctrl+Shift+S"), self.export_conversation, True),
            ("Boundary map", native("Ctrl+B"), lambda: self.toggle_boundary(), self.feature("boundary")),
            ("Keyboard shortcuts", "?", lambda: self.open_settings_tab("Keyboard"), self.feature("keyboard")),
            ("Run setup again", "", self.open_setup, self.feature("setup")),
            ("Lock Onionmind", "", lambda: getattr(self, "lock_now", lambda: None)(), self.feature("lock")),
            ("Export network log", "", self.open_network_log, self.feature("netlog")),
            ("Agent rules for this project", "", lambda: self.open_settings_tab("Agent rules"), self.feature("rules")),
        ]
        if self.feature("presets"):
            for preset in getattr(self.desktop_core, "load_presets", lambda _s: [])(self.settings_data):
                commands.append((f"Preset: {preset['name']}", "",
                                 lambda pid=preset["id"]: getattr(self, "set_preset", lambda _p: None)(pid), True))
        if self.feature("appearance"):
            commands.append(("Switch to light theme" if current_theme() == "dark" else "Switch to dark theme", "",
                             lambda: getattr(self, "toggle_theme", lambda: None)(), True))
        rows.extend(("Commands", label, hint, action) for label, hint, action, shown in commands if shown)
        return rows

    def open_palette(self) -> None:
        if not self.feature("palette"):
            return
        palette = CommandPalette(self, self._palette_rows())
        self._palette = palette
        palette.place()
        palette.open()
        palette.search.setFocus(Qt.FocusReason.PopupFocusReason)

    def open_settings_tab(self, label: str) -> None:
        dialog = SettingsDialog(self.data_root, self.harness_bridge.limitation, self.update_bridge, self)
        dialog.show_tab(label)
        dialog.exec()

    def _question_shortcut(self) -> None:
        if not self.feature("keyboard"):
            return
        focus = QApplication.focusWidget()
        if isinstance(focus, (QLineEdit, QTextEdit, QPlainTextEdit)) and not getattr(focus, "isReadOnly", lambda: False)():
            return                               # typing a question mark, not asking for help
        self.open_settings_tab("Keyboard")

    def network_log_rows(self) -> list[dict[str, str]]:
        today = datetime.now().strftime("%Y-%m-%d")
        rows: list[dict[str, str]] = []
        log_type = getattr(self.desktop_core, "EgressLog", None)
        if log_type is not None:
            rows.extend(log_type(self.egress_path).entries(today))
        agent_rows = getattr(self.desktop_core, "agent_net_entries", None)
        net_log = _as_text(getattr(self.core, "NET_LOG", ""))
        if callable(agent_rows) and net_log:
            rows.extend(agent_rows(net_log, today))
        return sorted(rows, key=lambda row: row.get("time", ""))

    def open_network_log(self) -> None:
        if not self.feature("netlog"):
            return
        dialog = NetworkLogDialog(self.network_log_rows(), self.desktop_core, self)
        self._netlog_dialog = dialog
        dialog.exec()
        if dialog.saved_path:
            self.set_status(f"Saved {dialog.saved_path} · {len(dialog.rows)} entries")

    def _apply_feature_palette(self, on: bool) -> None:
        del on
        self._sync_toolbar_features()

    def _apply_feature_boundary(self, on: bool) -> None:
        if not on:
            self.boundary_panel.hide()
        self._sync_toolbar_features()
        self._refresh_boundary()

    def _apply_feature_zones(self, on: bool) -> None:
        self.inspector.set_zones(on)

    def _apply_feature_netlog(self, on: bool) -> None:
        self.inspector.netlog_button.setVisible(on)

    def _apply_feature_keyboard(self, on: bool) -> None:
        set_system_motion_probe(on)

    # --- v2: message actions -------------------------------------------

    def _render_messages(self) -> None:
        """The transcript from self.chat_messages, each block knowing its index."""
        self.transcript.clear()
        for index, message in enumerate(self.chat_messages):
            role = message.get("role")
            if role in ("user", "assistant"):
                content = message.get("content")
                block = self.transcript.add_message(
                    role, content if isinstance(content, str) else "[local image attachment]")
                self._index_block(block, index)
            elif role == "tool":
                self.transcript.add_tool_card(
                    _as_text(message.get("tool_name") or "Tool result"),
                    [("Local tool output", _as_text(message.get("content"))[:80])],
                )

    def _index_block(self, block: Optional[MessageBlock], index: int) -> None:
        if block is None or index < 0:
            return
        block.message_index = index
        block.set_actions(self._message_actions(block) if self.feature("msgActions") else [])

    def _message_actions(self, block: MessageBlock) -> list[tuple[str, str, Callable[[], None]]]:
        if block.role == "user":
            return [("Edit", "pencil", lambda b=block: self._edit_message(b)),
                    ("Copy", "copy", lambda b=block: self._copy_message(b))]
        return [("Copy", "copy", lambda b=block: self._copy_message(b)),
                ("Regenerate", "regenerate", lambda b=block: self._regenerate(b)),
                ("Branch from here", "branch", lambda b=block: self._branch_from(b))]

    def _copy_message(self, block: MessageBlock) -> None:
        QApplication.clipboard().setText(block.text)
        self.set_status("Copied to the clipboard.")

    def _valid_index(self, block: MessageBlock) -> Optional[int]:
        index = block.message_index
        if index is None or index >= len(self.chat_messages):
            return None
        return index if self.chat_messages[index].get("role") == block.role else None

    def _edit_message(self, block: MessageBlock) -> None:
        index = self._valid_index(block)
        if self.active_kind or index is None:
            self.set_status("Stop the active run before editing a message." if self.active_kind
                            else "That message is no longer in this session.")
            return
        self._edit_from = index
        self.set_mode("chat")
        self.composer.setPlainText(block.text.split("\n\nAttached locally:", 1)[0])
        self.edit_banner.show()
        self.composer_frame.setMaximumHeight(214)
        self.focus_composer()

    def _cancel_edit(self) -> None:
        self._edit_from = None
        self.edit_banner.hide()
        self.composer_frame.setMaximumHeight(178 if self.context_warning.isHidden() else 214)

    def _regenerate(self, block: MessageBlock) -> None:
        index = self._valid_index(block)
        if self.active_kind or index is None:
            return
        user = next((i for i in range(index - 1, -1, -1) if self.chat_messages[i].get("role") == "user"), None)
        if user is None:
            self.set_status("There is no question before this reply to ask again.")
            return
        self.chat_messages = self.chat_messages[:user + 1]
        self._render_messages()
        self.set_mode("chat")
        self.inspector.append_activity("Regenerating a reply")
        self._set_active("chat")
        self._start_chat()

    def _branch_from(self, block: MessageBlock) -> None:
        index = self._valid_index(block)
        if self.active_kind or index is None:
            return
        if not self.preferences.get("save_history", True):
            self.set_status("Saving conversations to this machine is off, so there is nowhere to branch to.")
            return
        if not self.save_current_session():
            return
        source = _as_text(_field(self.current_session, "title", "")) or self._session_title()
        title = f"Branch of {source}"[:72]
        messages = copy.deepcopy(self.chat_messages[:index + 1])
        try:
            model = self.current_model_id()
            session = self.session_bridge.create(title, model, self.workspace, messages)
            session = self.session_bridge.save(session, title=title, model=model,
                                               workspace=self.workspace, messages=messages)
        except Exception as exc:
            self.set_status(f"Could not branch the session: {exc}")
            return
        session_id = _as_text(_field(session, "id"))
        self._refresh_session_rows()
        self.current_session = None
        self.chat_messages = []
        self.load_session(session_id)
        self.set_status(f"New session branched from “{source}”. The original is unchanged.")
        self.inspector.append_activity(f"Branched a new session from “{source}”")

    def _apply_feature_msgActions(self, on: bool) -> None:
        for block in list(self.transcript._message_blocks):
            if block.message_index is not None:
                block.set_actions(self._message_actions(block) if on else [])
        if not on and self._edit_from is not None:
            self._cancel_edit()

    # --- v2: session search ------------------------------------------------

    def _session_search(self, text: str) -> None:
        search = getattr(self.desktop_core, "search_sessions", None)
        if not self.feature("sessionSearch") or not callable(search) or len(text.strip()) < 2:
            self.left_rail.set_message_hits(None, str)
            return
        hits = search(self.session_bridge.list(), text)
        self.left_rail.set_message_hits(
            hits, lambda workspace: Path(_as_text(workspace)).name if workspace else "No project")

    def _apply_feature_sessionSearch(self, on: bool) -> None:
        field = self.left_rail.session_filter
        field.setPlaceholderText("Search sessions and messages" if on else "Filter sessions")
        field.setAccessibleName("Search sessions and messages in every project" if on else "Filter sessions by name")
        self._session_search(field.text())

    # --- v2: undo agent changes --------------------------------------------

    def _revert_paths(self, paths: list[str]) -> None:
        reverter = getattr(self.desktop_core, "revert_changes", None)
        if not self.feature("undo") or not callable(reverter) or not self.workspace:
            return
        if self.active_kind:
            self.set_status("Stop the active run before reverting changes.")
            return
        wanted = set(paths)
        changes = [c for c in (self.current_snapshot or {}).get("changes") or [] if c.get("path") in wanted]
        if not changes:
            return
        workspace, backups = self.workspace, self.data_root / "reverts"
        previous = self._revert_record
        label = changes[0]["path"] if len(changes) == 1 else f"{len(changes)} files"

        def job(signals: WorkerSignals) -> Any:
            del signals
            record = reverter(workspace, changes, backups)
            if previous is not None:
                shutil.rmtree(_field(previous, "backup"), ignore_errors=True)
            return record

        def done(record: Any) -> None:
            self._revert_record = record
            self.inspector.undo_label.setText(f"Reverted {label}")
            self.inspector.undo_bar.show()
            self.set_status(f"Reverted {len(changes)} file(s) to the last commit.")
            self.inspector.append_activity(f"Reverted {label} (git restore)", "machine")
            self.refresh_workspace()

        worker = self._start_worker(job)
        worker.signals.result.connect(done)
        worker.signals.error.connect(lambda message: self.set_status(f"Could not revert: {message}"))

    def _choose_hunks(self, path: str) -> None:
        reader = getattr(self.desktop_core, "diff_hunks", None)
        if not self.feature("undo") or not callable(reader) or not self.workspace:
            return
        if self.active_kind:
            self.set_status("Stop the active run before reverting changes.")
            return
        workspace = self.workspace

        def done(hunks: list[Any]) -> None:
            if not hunks:
                self.set_status(f"{path} has no changes to choose from.")
                return
            dialog = HunkDialog(path, hunks, self)
            self._hunk_dialog = dialog
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected():
                self._revert_hunks(path, dialog.selected())

        worker = self._start_worker(lambda signals: reader(workspace, path))
        worker.signals.result.connect(done)
        worker.signals.error.connect(lambda message: self.set_status(f"Could not read the changes: {message}"))

    def _revert_hunks(self, path: str, indices: list[int]) -> None:
        reverter = getattr(self.desktop_core, "revert_hunks", None)
        if not callable(reverter) or not self.workspace or self.active_kind:
            return
        workspace, backups = self.workspace, self.data_root / "reverts"
        previous = self._revert_record
        label = f"{len(indices)} change(s) in {path}"

        def job(signals: WorkerSignals) -> Any:
            del signals
            record = reverter(workspace, path, indices, backups)
            if previous is not None:
                shutil.rmtree(_field(previous, "backup"), ignore_errors=True)
            return record

        def done(record: Any) -> None:
            self._revert_record = record
            self.inspector.undo_label.setText(f"Reverted {label}")
            self.inspector.undo_bar.show()
            self.set_status(f"Reverted {label} to the last commit.")
            self.inspector.append_activity(f"Reverted {label} (git apply -R)", "machine")
            self.refresh_workspace()

        worker = self._start_worker(job)
        worker.signals.result.connect(done)
        worker.signals.error.connect(lambda message: self.set_status(f"Could not revert: {message}"))

    def _undo_revert(self) -> None:
        undo = getattr(self.desktop_core, "undo_revert", None)
        record = self._revert_record
        if record is None or not callable(undo):
            return

        def job(signals: WorkerSignals) -> int:
            del signals
            restored = undo(record)
            shutil.rmtree(_field(record, "backup"), ignore_errors=True)
            return restored

        def done(_restored: int) -> None:
            self._revert_record = None
            self.inspector.undo_bar.hide()
            self.set_status("Revert undone. The changes are back on disk.")
            self.inspector.append_activity("Revert undone; the changes are back on disk", "machine")
            self.refresh_workspace()

        worker = self._start_worker(job)
        worker.signals.result.connect(done)
        worker.signals.error.connect(lambda message: self.set_status(f"Could not undo the revert: {message}"))

    def _apply_feature_undo(self, on: bool) -> None:
        self.inspector.undo_enabled = on
        if not on:
            self.inspector.undo_bar.hide()
        self.inspector.update_snapshot(self.current_snapshot or {})

    # --- v2: rules and presets ---------------------------------------------

    def save_project_rules(self, rules: dict[str, Any]) -> None:
        store = getattr(self.desktop_core, "store_rules", None)
        if not callable(store) or not self.workspace:
            return
        store(self.settings_data, self.workspace, rules)
        if not self.demo:
            self.settings_bridge.save(self.settings_data)

    def presets(self) -> list[dict[str, str]]:
        loader = getattr(self.desktop_core, "load_presets", None)
        return list(loader(self.settings_data)) if callable(loader) else []

    def _preset_text(self) -> str:
        if not self.feature("presets"):
            return ""
        chooser = getattr(self.desktop_core, "active_preset", None)
        return _as_text(chooser(self.settings_data).get("text")) if callable(chooser) else ""

    def set_preset(self, preset_id: str) -> None:
        self.settings_data["preset"] = preset_id
        if not self.demo:
            self.settings_bridge.save(self.settings_data)
        self._sync_preset_chip()
        name = next((p["name"] for p in self.presets() if p["id"] == preset_id), preset_id)
        if preset_id != "default":
            self.inspector.append_activity(f"Preset: {name}")
        self.set_status(f"Preset: {name}")
        self._update_context_meter()

    def set_preset_text(self, preset_id: str, text: Optional[str]) -> None:
        """Store edited text, or None to go back to the shipped text."""
        texts = dict(self.settings_data.get("preset_texts") or {})
        if text is None:
            texts.pop(preset_id, None)
        else:
            texts[preset_id] = text
        self.settings_data["preset_texts"] = texts
        if not self.demo:
            self.settings_bridge.save(self.settings_data)

    def _sync_preset_chip(self) -> None:
        chooser = getattr(self.desktop_core, "active_preset", None)
        active = chooser(self.settings_data) if callable(chooser) else {"id": "default", "name": "Default"}
        self.preset_chip.setText(active["name"])
        self.preset_chip.setAccessibleName(f"Preset: {active['name']}")
        self.preset_menu.clear()
        title = self.preset_menu.addAction("Preset")
        title.setEnabled(False)
        for preset in self.presets():
            action = self.preset_menu.addAction(f"{preset['name']} · {preset['short']}")
            action.setCheckable(True)
            action.setChecked(preset["id"] == active["id"])
            action.triggered.connect(lambda _checked=False, pid=preset["id"]: self.set_preset(pid))
        self.preset_menu.addSeparator()
        self.preset_menu.addAction("Edit presets…").triggered.connect(lambda: self.open_settings_tab("Presets"))

    def _apply_feature_presets(self, on: bool) -> None:
        self.preset_chip.setVisible(on and self.mode == "chat")
        if on:
            self._sync_preset_chip()
        self._update_context_meter()

    # --- v2: errors and recovery -------------------------------------------

    FAULTS: dict[str, dict[str, str]] = {
        "server": {"title": "The local model server stopped",
                   "body": "Onionmind can’t reach the model server on this machine, so nothing can be answered "
                           "right now. Your conversation is safe.",
                   "status": "Model server stopped. Restart it to continue.",
                   "fix": "Restarting the model server…", "done": "Model server restarted. Onionmind is ready."},
        "oom": {"title": "Not enough GPU memory for {tier}",
                "body": "The model needs more graphics memory than is free right now{vram}. Close another app "
                        "that uses the GPU, or switch to a lighter tier.",
                "status": "Out of GPU memory. Free some memory or switch to a lighter tier.",
                "fix": "Freeing GPU memory and reloading…", "done": "Memory freed. {tier} is loaded again."},
        "tor": {"title": "The Tor circuit failed",
                "body": "The search couldn’t complete because the Tor circuit dropped. Nothing left this machine "
                        "outside Tor. You can try a new circuit or answer without searching.",
                "status": "Tor circuit failed. Try a new circuit.",
                "fix": "Building a new Tor circuit…", "done": "New Tor circuit verified."},
        "node": {"title": "Agent stopped: Node.js crashed",
                 "body": "The Agent runtime exited unexpectedly. No files were changed after the last observed "
                         "change. Chat still works.",
                 "status": "Agent runtime stopped. Restart it to continue.",
                 "fix": "Restarting the Agent runtime…", "done": "Agent runtime restarted."},
    }

    def _fault_text(self, kind: str, key: str) -> str:
        total = self._model_power.get("vram_total")
        return self.FAULTS[kind][key].format(
            tier=self._tier_name(self.current_model_id()),
            vram=f" (this GPU has {total / 1024:.0f} GB)" if total else "")

    def _show_fault(self, kind: str, detail: str = "") -> None:
        self._clear_fault()
        self._fault = kind
        card = NoticeCard("error", self._fault_text(kind, "title"), self._fault_text(kind, "body"))
        if detail:
            card.setToolTip(_brand_runtime_text(detail)[:300])
        primary, secondary = {
            "server": (("Restart model server", self._fix_server), ("Open Models", self._fault_open_models)),
            "oom": ((f"Switch to {self._tier_name(self._lighter_model() or '') or 'a lighter tier'}", self._fix_oom_switch),
                    ("Free memory and retry", self._fix_oom_free)),
            "tor": (("Try a new circuit", self._fix_tor), ("Continue without search", self._fix_tor_skip)),
            "node": (("Restart Agent runtime", self._fix_node), ("Switch to Chat", self._fix_node_chat)),
        }[kind]
        card.add_button(secondary[0], secondary[1])
        card.add_button(primary[0], primary[1], primary=True)
        self._fault_card = card
        self.fault_layout.addWidget(card)
        self.fault_host.show()
        self.retry_button.hide()
        self.set_status(self.FAULTS[kind]["status"])
        self.inspector.append_activity(f"Problem: {self._fault_text(kind, 'title')}")

    def _clear_fault(self, done: str = "") -> None:
        if self._fault_card is not None:
            self.fault_layout.removeWidget(self._fault_card)
            self._fault_card.hide()
            self._fault_card.deleteLater()
        self._fault_card = None
        self._fault = None
        self._sync_no_model_banner()
        if done:
            self.set_status(done)
            self.inspector.append_activity(done)

    def _fault_busy(self, kind: str) -> None:
        if self._fault_card is not None:
            self._fault_card.set_busy(self._fault_text(kind, "fix"))

    def _fault_failed(self, kind: str, message: str) -> None:
        self._show_fault(kind, message)
        if self._fault_card is not None:
            self._fault_card.body_label.setText(
                f"{self._fault_text(kind, 'body')}\n\nThat did not work: {_brand_runtime_text(message)[:200]}")

    def _fix_server(self) -> None:
        """Re-probe the model server; start it first when its program is here and it is not running."""
        self._fault_busy("server")
        core = self.core

        def job(signals: WorkerSignals) -> list[str]:
            del signals
            detector = getattr(core, "detect_backend", None)
            try:
                if callable(detector):
                    detector()
            except BaseException:
                program = shutil.which("ollama")
                if not program:
                    raise RuntimeError("The model server is not running and its program was not found. "
                                       "Start it, then try again.")
                subprocess.Popen([program, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 stdin=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                                 start_new_session=os.name != "nt")
                deadline = time.monotonic() + 20
                while True:
                    try:
                        detector()
                        break
                    except BaseException:
                        if time.monotonic() > deadline:
                            raise RuntimeError("The model server did not come up within 20 seconds.")
                        time.sleep(0.5)
            installed = getattr(core, "installed_models", None)
            return list(installed()) if callable(installed) else []

        def done(models: list[str]) -> None:
            self._clear_fault(self._fault_text("server", "done"))
            self._model_probe_complete(("", models))

        worker = self._start_worker(job)
        worker.signals.result.connect(done)
        worker.signals.error.connect(lambda message: self._fault_failed("server", message))

    def _fault_open_models(self) -> None:
        self.open_model_manager()

    def _lighter_model(self) -> Optional[str]:
        current = self.current_model_id()
        sizes = self._model_power.get("sizes") or {}
        order = {"SPARK": 0, "EMBER": 1, "BLAZE": 2, "INFERNO": 3}
        def weight(model: str) -> tuple[int, int]:
            return (sizes.get(model) or 0, order.get(self._tier_name(model), 9))
        lighter = [m for m in self.installed_model_ids if m != current and weight(m) < weight(current)]
        return max(lighter, key=weight) if lighter else None

    def _fix_oom_switch(self) -> None:
        target = self._lighter_model()
        if target is None:
            self._fault_failed("oom", "No lighter model is installed. Add one in Models.")
            return
        self.model_combo.setCurrentIndex(self.model_combo.findData(target))
        self._clear_fault(f"Switched to {self._tier_name(target)} · {self._describe_model(target)}")
        self.inspector.append_activity(f"Switched to {self._tier_name(target)} after running out of GPU memory")
        self._switch_loaded_model(target)

    def _fix_oom_free(self) -> None:
        """Unload every model this server holds, then ask the same turn again."""
        self._fault_busy("oom")
        loaded, unload = getattr(self.core, "loaded_models", None), getattr(self.core, "unload_model", None)

        def job(signals: WorkerSignals) -> int:
            del signals
            names = [item.get("name") for item in (loaded() if callable(loaded) else []) if item.get("name")]
            for name in names:
                if callable(unload):
                    unload(name)
            return len(names)

        def done(_count: int) -> None:
            self._clear_fault(self._fault_text("oom", "done"))
            self._retry_last_turn()

        worker = self._start_worker(job)
        worker.signals.result.connect(done)
        worker.signals.error.connect(lambda message: self._fault_failed("oom", message))

    def _fix_tor(self) -> None:
        self._fault_busy("tor")
        self._clear_fault()
        self._start_tor_from_toolbar()
        self._awaiting_tor_fix = True

    def _fix_tor_skip(self) -> None:
        self._clear_fault("Search turned off for this turn.")
        if self.chat_messages and self.chat_messages[-1].get("role") == "assistant" and _as_text(
                self.chat_messages[-1].get("content")).startswith("Local inference failed"):
            self._retry_last_turn()

    def _fix_node(self) -> None:
        task = self._last_agent_task
        self._clear_fault(self._fault_text("node", "done"))
        if task and not self.active_kind:
            self.set_mode("agent")
            self._set_active("agent")
            self._start_harness(task)

    def _fix_node_chat(self) -> None:
        self._clear_fault("Switched to Chat.")
        self.set_mode("chat")

    def _apply_feature_errors(self, on: bool) -> None:
        if not on:
            self._clear_fault()

    # --- v2: empty and first-use states -------------------------------------

    STARTERS = (
        "Explain how this project is structured",
        "Find likely bugs in the files I selected",
        "Write tests for the code I selected",
        "Is there a newer release of a library this project uses?",
    )

    def _sync_starters(self) -> None:
        if self._starters is not None:
            self.transcript.remove_widget(self._starters)
            self._starters = None
        if not self.feature("empty") or self.chat_messages or self.active_kind:
            return
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Try one of these")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        for prompt in self.STARTERS:
            button = QPushButton(prompt)
            button.setObjectName("chipButton")
            button.setAccessibleName(f"Start with: {prompt}")
            button.clicked.connect(lambda _checked=False, text=prompt: self._use_starter(text))
            layout.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)
        self._starters = self.transcript.add_widget(box)

    def _use_starter(self, prompt: str) -> None:
        if self.active_kind:
            return
        if self._starters is not None:
            self.transcript.remove_widget(self._starters)
            self._starters = None
        self.composer.setPlainText(prompt)
        self.submit()

    def _sync_no_model_banner(self) -> None:
        if not hasattr(self, "fault_layout"):
            return
        banner = getattr(self, "_no_model_banner", None)
        wanted = self.feature("empty") and self._no_models and self._fault is None
        if wanted and banner is None:
            banner = NoticeCard("ask", "No model is installed yet. Onionmind needs one on this machine before it can answer.")
            banner.add_button("Choose a model", self.open_model_manager, primary=True)
            self._no_model_banner = banner
            self.fault_layout.addWidget(banner)
        elif not wanted and banner is not None:
            self.fault_layout.removeWidget(banner)
            banner.hide()
            banner.deleteLater()
            self._no_model_banner = None
        self.fault_host.setVisible(self._fault is not None or getattr(self, "_no_model_banner", None) is not None)

    def _apply_feature_empty(self, on: bool) -> None:
        self.left_rail.empty_text_enabled = on
        self.left_rail.sync_empty_text()
        self.inspector.empty_changes_note = (
            "Nothing has changed in this project yet. When Agent edits files here, each change shows up in "
            "this list with its diff, read straight from git." if on else "")
        self.inspector.update_snapshot(self.current_snapshot or {})
        self._sync_starters()
        self._sync_no_model_banner()

    # --- v2: first-launch setup ----------------------------------------------

    def open_setup(self) -> None:
        if not self.feature("setup"):
            self.set_status("Turn on First-launch setup in New features to run it.")
            return
        self.setup_overlay.open()

    # --- Remote compute over SSH ---------------------------------------------

    def remote_settings(self) -> dict[str, Any]:
        loader = getattr(self.desktop_core, "load_remote_settings", None)
        if not callable(loader):
            return {"host": "", "autoconnect": False}
        return loader(self.settings_data)

    def set_remote_setting(self, key: str, value: Any) -> None:
        stored = self.settings_data.get("remote_compute")
        settings = dict(stored) if isinstance(stored, dict) else {}
        settings[key] = value
        self.settings_data["remote_compute"] = settings
        if not self.demo:
            self.settings_bridge.save(self.settings_data)

    def inference_location(self) -> str:
        tunnel = self.remote_tunnel
        if self.remote_phase == "on" and tunnel is not None:
            return f"on {tunnel.target.label} (over SSH)"
        return "on this machine"

    def _set_remote_phase(self, phase: str, message: str = "") -> None:
        self.remote_phase, self.remote_message = phase, message
        self.remoteChanged.emit()

    def connect_remote(self, host: Optional[str] = None) -> None:
        """Open the SSH tunnel to `host` (default: the saved one) off the UI thread."""
        dc = self.desktop_core
        if dc is None or not hasattr(dc, "SshTunnel") or not callable(getattr(self.core, "use_ollama_port", None)):
            self._set_remote_phase("error", "This Onionmind build cannot use remote compute.")
            return
        target = dc.parse_ssh_target(host if host is not None else self.remote_settings()["host"])
        if target is None:
            self._set_remote_phase("error", "Enter a server as user@host, host, or an ~/.ssh/config alias.")
            return
        if self.remote_phase == "connecting":
            return
        self.disconnect_remote(reprobe=False)
        tunnel = dc.SshTunnel(target)
        self._remote_pending = tunnel
        self._set_remote_phase("connecting", f"Connecting to {target.label} over SSH…")
        self.model_status.set_status("Connecting", "busy")
        self.inspector.append_activity(f"Remote compute: opening SSH tunnel to {target.label} (direct, not Tor)")

        def open_tunnel(signals: WorkerSignals) -> Any:
            del signals
            tunnel.start()
            return tunnel

        worker = self._start_worker(open_tunnel)
        worker.signals.result.connect(self._remote_connected)
        worker.signals.error.connect(lambda message: self._remote_failed(message))

    def _remote_connected(self, tunnel: Any) -> None:
        self._remote_pending = None
        if self.remote_phase != "connecting":
            tunnel.stop()                  # disconnected (or closed) while connecting
            return
        self.remote_tunnel = tunnel
        self.core.use_ollama_port(tunnel.local_port)
        label = tunnel.target.label
        self._set_remote_phase("on", f"Connected to {label} · Ollama {tunnel.version}")
        self.set_status(f"Models now run on {label}")
        self.inspector.append_activity(f"Remote compute on: models run on {label}")
        self._probe_models()

    def _remote_failed(self, message: str) -> None:
        self._remote_pending = None
        if self.remote_phase != "connecting":
            return
        self._set_remote_phase("error", message)
        self.set_status(f"Remote compute: {message}")
        self.inspector.append_activity(f"Remote compute failed: {message}")
        self._probe_models()               # show what this machine can still do

    def disconnect_remote(self, reprobe: bool = True, message: str = "") -> None:
        tunnel, self.remote_tunnel = self.remote_tunnel, None
        pending, self._remote_pending = self._remote_pending, None
        was_on = tunnel is not None or self.remote_phase == "connecting"
        if tunnel is not None:
            tunnel.stop()
        if pending is not None:
            pending.cancel()
        use_port = getattr(self.core, "use_ollama_port", None)
        if callable(use_port):
            use_port(None)
        self._set_remote_phase("error" if message else "off", message)
        if was_on and reprobe:
            self.inspector.append_activity("Remote compute off: models run on this machine")
            self._probe_models()

    def _poll_remote_liveness(self) -> None:
        tunnel = self.remote_tunnel
        if self.remote_phase != "on" or tunnel is None or tunnel.alive:
            return
        detail = tunnel.last_error()
        message = f"Lost the SSH tunnel to {tunnel.target.label}: {detail}"
        self.set_status(message + " Models run on this machine again.")
        self.disconnect_remote(message=message)

    # --- v2: auto-delete and app lock ----------------------------------------

    def _lock_settings(self) -> dict[str, Any]:
        stored = self.settings_data.get("lock")
        return dict(stored) if isinstance(stored, dict) else {}

    def set_lock_setting(self, key: str, value: Any) -> None:
        settings = self._lock_settings()
        settings[key] = value
        self.settings_data["lock"] = settings
        if not self.demo:
            self.settings_bridge.save(self.settings_data)
        self._apply_feature_lock(self.feature("lock"))

    def set_passphrase(self, passphrase: str) -> bool:
        hasher = getattr(self.desktop_core, "hash_passphrase", None)
        if not passphrase or not callable(hasher):
            return False
        self.set_lock_setting("passphrase", hasher(passphrase))
        return True

    def has_passphrase(self) -> bool:
        return isinstance(self._lock_settings().get("passphrase"), dict)

    def _verify_passphrase(self, text: str) -> bool:
        verify = getattr(self.desktop_core, "verify_passphrase", None)
        record = self._lock_settings().get("passphrase")
        return bool(callable(verify) and isinstance(record, dict) and verify(text, record))

    def lock_now(self) -> bool:
        if not self.feature("lock"):
            return False
        if not self.has_passphrase():
            self.set_status("Set a passphrase in Settings → Privacy before locking.")
            self.open_settings_tab("Privacy")
            return False
        self.lock_overlay.lock()
        self.inspector.append_activity("Onionmind locked")
        return True

    _INPUT_EVENTS = None

    def eventFilter(self, watched: Any, event: Any) -> bool:
        kinds = OnionmindWindow._INPUT_EVENTS
        if kinds is None:
            from PySide6.QtCore import QEvent

            kinds = OnionmindWindow._INPUT_EVENTS = {
                QEvent.Type.KeyPress, QEvent.Type.MouseButtonPress, QEvent.Type.MouseMove, QEvent.Type.Wheel}
        if event.type() in kinds:
            self._last_input = time.monotonic()
        return False

    def _set_idle_tracking(self, on: bool) -> None:
        """Watch for input app-wide only while an idle lock is armed."""
        app = QApplication.instance()
        if app is None or on == self._idle_tracking:
            return
        if on:
            self._last_input = time.monotonic()
            app.installEventFilter(self)
        else:
            app.removeEventFilter(self)
        self._idle_tracking = on

    def _check_idle(self) -> None:
        minutes = int(self._lock_settings().get("lock_after") or 0)
        if not self.feature("lock") or not minutes or self.lock_overlay.isVisible() or not self.has_passphrase():
            return
        if time.monotonic() - self._last_input >= minutes * 60:
            self.lock_now()

    def old_sessions(self) -> list[Any]:
        days = int(self._lock_settings().get("auto_delete_days") or 0)
        finder = getattr(self.desktop_core, "sessions_older_than", None)
        if not days or not callable(finder):
            return []
        current = _as_text(_field(self.current_session, "id", "")) if self.current_session is not None else ""
        candidates = [*self.session_bridge.list(), *self.session_bridge.list(archived=True)]
        return [s for s in finder(candidates, days) if _as_text(_field(s, "id")) != current]

    def _auto_delete_old(self) -> int:
        if not self.feature("lock"):
            return 0
        removed = sum(1 for session in self.old_sessions() if self.session_bridge.delete(_as_text(_field(session, "id"))))
        if removed:
            self._refresh_session_rows()
            self.inspector.append_activity(f"Deleted {removed} old conversation(s)")
            self.set_status(f"Deleted {removed} old conversation(s).")
        return removed

    def _apply_feature_lock(self, on: bool) -> None:
        self._sync_toolbar_features()
        minutes = int(self._lock_settings().get("lock_after") or 0)
        if on and minutes:
            self.idle_timer.start()
        else:
            self.idle_timer.stop()
        self._set_idle_tracking(bool(on and minutes))
        if on and self._lock_settings().get("auto_delete_days"):
            self.housekeeping_timer.start()
            if not self.demo:
                QTimer.singleShot(0, self._auto_delete_old)
        else:
            self.housekeeping_timer.stop()
        if not on and self.lock_overlay.isVisible():
            # Turning the feature off is done from an unlocked window only.
            pass

    # --- v2: appearance --------------------------------------------------------

    def _appearance(self) -> dict[str, Any]:
        stored = self.settings_data.get("appearance")
        values = {"theme": "dark", "ui_scale": 100, "notify": False}
        if isinstance(stored, dict):
            if stored.get("theme") in ("dark", "light"):
                values["theme"] = stored["theme"]
            if stored.get("ui_scale") in (90, 100, 110, 125):
                values["ui_scale"] = stored["ui_scale"]
            values["notify"] = bool(stored.get("notify"))
        return values

    def _ui_scale_factor(self) -> float:
        if not getattr(self, "features", None) or not self.feature("appearance"):
            return 1.0
        return self._appearance()["ui_scale"] / 100

    def set_appearance(self, key: str, value: Any) -> None:
        values = self._appearance()
        values[key] = value
        self.settings_data["appearance"] = values
        if not self.demo:
            self.settings_bridge.save(self.settings_data)
        self._apply_feature_appearance(self.feature("appearance"))

    def toggle_theme(self) -> None:
        self.set_appearance("theme", "light" if current_theme() == "dark" else "dark")

    def _apply_feature_appearance(self, on: bool) -> None:
        theme = self._appearance()["theme"] if on else "dark"
        look = (theme, self._ui_scale_factor())
        if look == getattr(self, "_applied_look", ("dark", 1.0)):
            return                               # restyling every widget is not free
        self._applied_look = look
        if theme != current_theme():
            set_theme(theme)
            _retheme_icons(self)
            self.tor_status._update_state_icon()
            self.model_status.refresh_theme()
        if hasattr(self, "preferences"):
            self._apply_preferences()

    def _apply_feature_runPanel(self, on: bool) -> None:
        self.inspector.set_run_tab(on)

    def _apply_feature_approvals(self, on: bool) -> None:
        del on
        self.approval_state.setText(self._approval_text(self.yolo_consent.isChecked()))

    def _notify(self, title: str, body: str) -> None:
        """A real OS notification (system tray), only with appearance on, the
        person's say-so, and the window not already in front."""
        if not self.feature("appearance") or not self._appearance().get("notify"):
            return
        if self.isActiveWindow() and not self.isMinimized():
            return
        self.last_notification = (title, body)
        from PySide6.QtWidgets import QSystemTrayIcon

        if QSystemTrayIcon.isSystemTrayAvailable():
            if self._tray is None:
                self._tray = QSystemTrayIcon(self.windowIcon(), self)
                self._tray.setToolTip("Onionmind")
            self._tray.show()
            self._tray.showMessage(title, body, QSystemTrayIcon.MessageIcon.Information, 6000)
        else:
            QApplication.alert(self)

    # --- v2 features -----------------------------------------------------
    #
    # One dict, saved with the other settings. Each feature owns an
    # _apply_feature_<key>(on) hook that shows or hides its own UI, so turning
    # a flag off restores the original workbench at once, without a restart.

    def _load_features(self) -> dict[str, bool]:
        loader = getattr(self.desktop_core, "load_features", None)
        if not callable(loader):
            return {}
        try:
            return dict(loader(self.settings_data))
        except Exception:
            return {}

    def feature(self, key: str) -> bool:
        return bool(self.features.get(key))

    def set_feature(self, key: str, on: bool) -> None:
        if key not in self.features or self.features[key] == bool(on):
            return
        self.features[key] = bool(on)
        self._store_features()

    def set_all_features(self, on: bool) -> None:
        if not self.features:
            return
        for key in self.features:
            self.features[key] = bool(on)
        self._store_features()

    def _store_features(self) -> None:
        self.settings_data["features"] = dict(self.features)
        if not self.demo:
            self.settings_bridge.save(self.settings_data)
        self._apply_features()
        on = sum(1 for value in self.features.values() if value)
        self.set_status(f"New features: {on} of {len(self.features)} on")

    def _apply_features(self) -> None:
        for key, on in self.features.items():
            hook = getattr(self, f"_apply_feature_{key}", None)
            if callable(hook):
                hook(bool(on))
        popover = self._features_popover
        if popover is not None:
            popover.toggles.sync()
        self.featuresChanged.emit(dict(self.features))

    def open_features_popover(self) -> None:
        if self._features_popover is None:
            self._features_popover = FeaturesPopover(self)
        self._features_popover.show_below(self.features_button)

    def _populate_demo(self) -> None:
        self.set_model_options(["inferno", "blaze", "ember"], "inferno")
        self.model_status.set_status("Local · Ready", "good")
        # Demo state has to agree with itself now that the pill is a control:
        # a label saying Running while the phase says off would offer Start.
        self.tor_phase = "running"
        self.tor_status.set_status("Ready", "good")
        _set_tor_action(
            self,
            "Turn off",
            tooltip="Stop Onionmind's background Tor process.",
        )
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
        self.inspector.append_activity("Background Tor state and local model readiness reported separately")
        self.set_mode("agent")
        self.set_status("Ready · 4 observed changes · all inference local")
        self._sync_action_states()
        QTimer.singleShot(0, lambda: self.transcript.verticalScrollBar().setValue(0))

    def closeEvent(self, event: Any) -> None:
        self._set_idle_tracking(False)
        self._save_window_layout()
        self._remember_draft()
        self.save_current_session()
        timer = getattr(self, "tor_liveness_timer", None)
        if timer is not None:
            timer.stop()
        if self.stop_event is not None:
            self.stop_event.set()
        if self.harness_process is not None and self.harness_process.state() != QProcess.ProcessState.NotRunning:
            self.harness_process.kill()
        self.terminal.stop()
        for tunnel in (self.remote_tunnel, self._remote_pending):
            if tunnel is not None:
                tunnel.cancel()
        if self.preferences.get("clear_on_exit", False):
            # Last, so it takes what this very close just wrote with it.
            self.wipe_machine_data()
        stop_tor = getattr(self.core, "stop_managed_tor", None)
        if callable(stop_tor):
            try:
                stop_tor()
            except Exception:
                pass
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
    _apply_native_dark_title_bar(window)
    # A system scheme flip (scheduled dark mode, Settings) must not re-light
    # the only surface the app does not paint itself.
    style_hints = app.styleHints()
    if hasattr(style_hints, "colorSchemeChanged"):
        style_hints.colorSchemeChanged.connect(
            lambda _scheme: _apply_native_dark_title_bar(window)
        )
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
