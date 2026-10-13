"""Reusable desktop widgets: status pills and buttons, the transcript, timeline,
notices, run panel, command palette, boundary, small dialogs and overlays.

Part of onionmind_desktop, which re-exports every name defined here.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable, Optional

from PySide6.QtCore import QPointF, QRectF, QSize, QStandardPaths, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontDatabase, QKeyEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from onionmind_desktop_theme import (
    _as_text,
    _brand_runtime_text,
    _field,
    _icon,
    _themed_icon,
    _tor_control_icon,
    _ui_animations_enabled,
    MODULE_DIR,
    theme_tokens,
    WorkerSignals,
)

if TYPE_CHECKING:  # the window imports this module, not the other way round
    from onionmind_desktop import OnionmindWindow


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
