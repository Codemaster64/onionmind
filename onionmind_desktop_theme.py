"""Shared ground for the desktop UI: constants, the Nocturne palette and style
sheet, text helpers, icons, fonts, background workers, motion and the native
title bar.

Part of onionmind_desktop, which re-exports every name defined here.
"""

from __future__ import annotations

import copy
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QApplication, QListWidget, QTreeWidget, QWidget
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
QLabel#contextHint { color: @accent_text@; background: @accent_hover@; border: 1px solid @accent_line@; border-radius: 8px; padding: 1px 6px; }
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
    if not ui_family:
        # macOS keeps Verdana under /System/Library/Fonts/Supplemental and
        # distros put DejaVu where they like; both are already registered there.
        installed = set(QFontDatabase.families())
        ui_family = next((f for f in ("Verdana", "DejaVu Sans") if f in installed), "")
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


def _apply_native_dark_title_bar(window: Any, dark: Optional[bool] = None) -> None:
    """Match the Windows title bar to the app's theme, not the system scheme.

    Otherwise a light-mode Windows install puts a bright frame around the dark
    workbench, and a dark one a dark frame around the light theme.
    DWMWA_USE_IMMERSIVE_DARK_MODE (attribute 20; 19 on pre-2004 builds) sets
    it. Purely cosmetic: every failure path is silent and the app runs
    identically without it.
    """
    if os.name != "nt":
        return
    if dark is None:
        dark = current_theme() == "dark"
    try:
        import ctypes

        for attribute in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                int(window.winId()), attribute, ctypes.byref(ctypes.c_int(1 if dark else 0)), 4
            ) == 0:
                return
    except (AttributeError, OSError, TypeError, ValueError):
        pass


def _field(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _optional_callable(source: Any, name: str) -> Optional[Callable[..., Any]]:
    fn = getattr(source, name, None)
    return fn if callable(fn) else None
