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
    QProcessEnvironment,
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

# The UI is split across these modules; every name stays importable from here.
from onionmind_desktop_theme import (  # noqa: F401
    _apply_native_dark_title_bar,
    _as_text,
    _BASE_APP_FONT,
    _BRAND_REPLACEMENTS,
    _brand_runtime_text,
    _conversation_markdown,
    _FALLBACK_PREFERENCES,
    _field,
    _friendly_error,
    _icon,
    _ICON_ROLE,
    _mix,
    _MOTION_OVERRIDE,
    _now_iso,
    _optional_callable,
    _PALETTE,
    _partial_think_tag,
    _path_is_within,
    _path_key,
    _REASONING_FIELDS,
    _register_system_fonts,
    _retheme_icons,
    _sanitize_assistant_content,
    _sanitize_assistant_messages,
    _strip_thinking,
    _STYLE_TEMPLATE,
    _SYSTEM_MOTION_PROBE,
    _SYSTEM_REDUCES_MOTION,
    _system_reduces_motion,
    _themed_icon,
    _THINK_TAG,
    _think_tag_candidate,
    _THINK_TAG_PATTERN,
    _tor_control_icon,
    _ui_animations_enabled,
    ACCENT,
    ANSI_ESCAPE,
    APP_ID,
    APP_NAME,
    apply_text_scale,
    build_style_sheet,
    current_theme,
    IMAGE_SUFFIXES,
    MAX_IMAGE_COUNT,
    MAX_IMAGE_FILE_BYTES,
    MAX_IMAGE_TOTAL_BYTES,
    MAX_TEXT_FILE_BYTES,
    MAX_TEXT_TOTAL_BYTES,
    MODULE_DIR,
    NOCTURNE_DARK,
    NOCTURNE_LIGHT,
    SafeWorker,
    set_motion_override,
    set_system_motion_probe,
    set_theme,
    SKIP_DIRS,
    STYLE_SHEET,
    theme_tokens,
    ThinkingStreamFilter,
    WorkerSignals,
)
from onionmind_desktop_widgets import (  # noqa: F401
    _Overlay,
    _PaletteSearch,
    _StateColors,
    BoundaryButton,
    BoundaryPanel,
    CommandPalette,
    ComposerEdit,
    HunkDialog,
    LockOverlay,
    MessageBlock,
    NetworkLogDialog,
    NoticeCard,
    ProvenanceChips,
    RunPanel,
    SetupOverlay,
    StatusActionButton,
    StatusDot,
    StatusPill,
    ThinkingDots,
    ThinkingIndicator,
    ThoughtLine,
    TimelineStep,
    TimelineView,
    ToolActivityCard,
    TranscriptView,
)
from onionmind_desktop_panes import (  # noqa: F401
    HarnessBridge,
    InspectorPane,
    LeftRail,
    SessionBridge,
    SettingsBridge,
    TerminalPane,
    UpdateBridge,
    WorkspaceBridge,
)
from onionmind_desktop_dialogs import (  # noqa: F401
    _feature_catalog,
    FeaturesPopover,
    FeatureToggles,
    ModelManagerDialog,
    SettingsDialog,
    UnlockDialog,
)
_SCREENSHOT_PATH: Optional[str] = None


def _v2(host: Any, name: str, *args: Any, default: Any = None) -> Any:
    """Call a v2 hook when the host has it. Core paths (chat, Tor) are also
    driven on minimal hosts by the original tests; v2 must not be required."""
    hook = getattr(host, name, None)
    return hook(*args) if callable(hook) else default


def _start_model_server(way: Any, detector: Callable[[], Any], problems: list[str],
                        wait_seconds: float = 20) -> bool:
    """Try one way of starting the local model server; True once it answers."""
    label = _as_text(_field(way, "label", "the model server"))
    argv = list(_field(way, "argv", ()))
    try:
        if _field(way, "waits", False):
            # systemctl may wait on a polkit password prompt; give it time.
            result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=120,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            if result.returncode != 0:
                detail = result.stderr.decode("utf-8", "replace").strip().splitlines()
                problems.append(f"{label}: {detail[-1] if detail else f'exit {result.returncode}'}")
                return False
        else:
            subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                             start_new_session=os.name != "nt")
    except (OSError, subprocess.TimeoutExpired) as exc:
        problems.append(f"{label}: {exc}")
        return False
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            detector()
            return True
        except BaseException:
            if time.monotonic() > deadline:
                problems.append(f"{label} did not answer within {wait_seconds:.0f} seconds")
                return False
            time.sleep(0.5)


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


def desktop_data_root() -> Path:
    """The desktop data folder (Qt's AppDataLocation + /desktop); the TUI mirrors it."""
    data_location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    return Path(data_location or (Path.home() / ".onionmind")) / "desktop"


class OnionmindWindow(QMainWindow):
    IS_ONIONMIND_WINDOW = True
    featuresChanged = Signal(object)
    remoteChanged = Signal()
    passwordChanged = Signal()

    def __init__(self, core: Any, desktop_core: Any = None, demo: bool = False, vault: Any = None) -> None:
        super().__init__()
        self.core = core
        self.desktop_core = desktop_core
        self.demo = demo
        # The open data vault (encryption at rest), or None: then files are
        # plaintext, as before - tests, demo, or a missing cryptography package.
        self.vault = vault
        self.encryption_notice = ""
        self._workers: set[SafeWorker] = set()
        self.data_root = desktop_data_root()
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.settings_bridge = SettingsBridge(desktop_core, self.data_root, vault)
        self.session_bridge = SessionBridge(desktop_core, self.data_root / "sessions", vault)
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
        # The running agent's private scratch folder (qwen's runtime dir and
        # the network log), only while the data is encrypted.
        self._agent_scratch: Optional[Path] = None
        self._resume_cards: list[Any] = []
        # qwen's own context figures: live during a run, and the overhead it
        # adds to every run, learnt from the last one.
        self._agent_ctx_live: Optional[tuple[int, int]] = None
        self._agent_overhead: Optional[int] = None
        self._agent_ctx_pending = False
        self._agent_closing = False
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
        self.lock_overlay.unlocked.connect(self._after_unlock)
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
        agent_sessions = self.data_root / getattr(self.desktop_core, "AGENT_ARCHIVE_DIR", "agent-runtime")
        if callable(shred_tree) and agent_sessions.is_dir():
            shred_tree(agent_sessions)                # the agent's sealed qwen sessions
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
            # After the finishing run has saved its result (still on this stack).
            QTimer.singleShot(0, self._drop_key_if_locked)
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
        self._agent_ctx_live = None
        self._agent_ctx_pending = False
        self._agent_closing = False
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
        try:
            scratch_env = self._open_agent_scratch()
        except Exception as exc:
            # Never fall back to qwen writing the run into ~/.qwen in plaintext.
            self._harness_start_failed(f"its private working folder could not be prepared ({exc})", generation)
            return
        process = QProcess(self)
        self.harness_process = process
        if scratch_env:
            environment = QProcessEnvironment.systemEnvironment()
            for name, value in scratch_env.items():
                environment.insert(name, value)
            process.setProcessEnvironment(environment)
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
            self._request_agent_context()
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
        self._absorb_agent_log()
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
        self._seal_agent_scratch()
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
        self._seal_agent_scratch()
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
        elif kind == "control_response" and event.get("request_id") == getattr(
                self.desktop_core, "AGENT_CONTEXT_REQUEST", None):
            self._agent_context_answer(event)
        elif kind == "result":
            # The turn is over; closing stdin lets qwen exit on its own - after
            # one last context reading, or a moment's wait if it never comes.
            self._agent_closing = True
            self._request_agent_context()
            if not self._agent_ctx_pending:
                self._close_agent_input()
            else:
                QTimer.singleShot(3000, self._close_agent_input)
        self.transcript._scroll_later()

    def _request_agent_context(self) -> None:
        ask = getattr(self.desktop_core, "agent_context_usage_message", None)
        if not callable(ask) or self._agent_ctx_pending:
            return
        self._agent_ctx_pending = True
        self._agent_write(ask())

    def _agent_context_answer(self, event: dict[str, Any]) -> None:
        self._agent_ctx_pending = False
        read = getattr(self.desktop_core, "agent_context_usage", None)
        usage = read(event.get("payload") or {}) if callable(read) and event.get("ok") else None
        if usage:
            self._agent_ctx_live = (usage["used"], usage["window"])
            if usage["overhead"]:
                self._agent_overhead = usage["overhead"]
            self._update_context_meter()
        if self._agent_closing:
            self._close_agent_input()

    def _close_agent_input(self) -> None:
        process = self.harness_process
        if process is not None and process.state() != QProcess.ProcessState.NotRunning:
            process.closeWriteChannel()

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
        self._request_agent_context()
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
        if self.mode == "agent" and self.active_kind == "agent" and self._agent_ctx_live:
            return self._agent_ctx_live           # qwen's own count for the run in progress
        if self._context_used_real is not None and self.mode == "chat":
            used = self._context_used_real
        elif self.mode == "agent" and self._agent_overhead:
            used = self._agent_overhead           # qwen's prompt + tools, from the last run
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
        if self.mode != "agent":
            agent_note = ""
        elif self.active_kind == "agent" and self._agent_ctx_live:
            agent_note = " Reported by the agent for the run in progress."
        elif self._agent_overhead:
            agent_note = f" Includes the agent's own instructions and tools ({fmt(self._agent_overhead)}), as it reported last run."
        else:
            agent_note = " The agent adds its own instructions on top of this; the first run measures them."
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
        self._egress_log().append(route, what)
        counts = self._today_counts()
        counts["tor" if route == "Tor" else "direct"] = counts.get("tor" if route == "Tor" else "direct", 0) + 1
        if what.startswith("Chat search"):
            counts["search"] = counts.get("search", 0) + 1
        self._refresh_boundary()

    def _vault_kw(self) -> dict[str, Any]:
        """`vault=` for core calls, only when one is open (older cores lack the keyword)."""
        return {"vault": self.vault} if self.vault is not None else {}

    def _egress_log(self) -> Any:
        log_type = getattr(self.desktop_core, "EgressLog", None)
        return log_type(self.egress_path, **self._vault_kw()) if log_type is not None else None

    def _agent_net_logs(self) -> list[Path]:
        """The plaintext agent logs: the run's scratch log, and the shared one
        (an older agent, or a child that did not inherit the redirect)."""
        logs = [Path(_as_text(getattr(self.core, "NET_LOG", "")) or ".")]
        if self._agent_scratch is not None:
            logs.insert(0, self._agent_scratch / getattr(self.desktop_core, "AGENT_NET_LOG_NAME", "agent-net.log"))
        return [log for log in logs if log.is_file()]

    def _absorb_agent_log(self) -> None:
        """Take the agent's plaintext network log into the encrypted one (when a
        log is wanted) and shred it. Only with a vault: without one the file is
        the log, as before."""
        absorb = _optional_callable(self.desktop_core, "absorb_agent_net_log")
        if self.vault is None or absorb is None:
            return
        logging = self.feature("netlog") or self.feature("boundary")
        for net_log in self._agent_net_logs():
            try:
                absorb(net_log, self._egress_log() if logging else None)
            except OSError:
                continue
            self._egress_counts = None

    def _open_agent_scratch(self) -> dict[str, str]:
        """With a vault: a private scratch folder for this run (restoring the
        resumed qwen session into it), and the environment that points qwen
        and the agent's network log there. Empty without a vault."""
        opener = _optional_callable(self.desktop_core, "open_agent_scratch")
        if self.vault is None or opener is None:
            return {}
        self._seal_agent_scratch()                   # never two at once
        scratch = Path(opener(self.data_root, self.vault, resume=self._agent_session_id or ""))
        self._agent_scratch = scratch
        name = getattr(self.desktop_core, "AGENT_NET_LOG_NAME", "agent-net.log")
        return {"QWEN_RUNTIME_DIR": str(scratch), "ONIONMIND_NET_LOG": str(scratch / name)}

    def _seal_agent_scratch(self) -> None:
        """After a run: its network log into the encrypted log, qwen's session
        into the vault, and the plaintext scratch folder shredded."""
        scratch, self._agent_scratch = self._agent_scratch, None
        sealer = _optional_callable(self.desktop_core, "seal_agent_scratch")
        if scratch is None or sealer is None or self.vault is None:
            return
        absorb = _optional_callable(self.desktop_core, "absorb_agent_net_log")
        log = scratch / getattr(self.desktop_core, "AGENT_NET_LOG_NAME", "agent-net.log")
        if absorb is not None and log.is_file():
            logging = self.feature("netlog") or self.feature("boundary")
            try:
                absorb(log, self._egress_log() if logging else None)
                self._egress_counts = None
            except OSError:
                pass
        try:
            sealer(self.data_root, self.vault, scratch, self._agent_session_id or "")
        except Exception as exc:
            shred = _optional_callable(self.desktop_core, "shred_tree")
            if shred is not None:
                shred(scratch)
            self.inspector.append_activity(f"The agent's session could not be kept for resuming: {exc}")

    def _today_counts(self) -> dict[str, int]:
        today = datetime.now().strftime("%Y-%m-%d")
        if self._egress_counts is None or getattr(self, "_egress_counts_day", "") != today:
            log = self._egress_log()
            self._egress_counts = log.counts(today) if log is not None else {}
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
        self._absorb_agent_log()
        log = self._egress_log()
        if log is not None:
            rows.extend(log.entries(today))
        agent_rows = getattr(self.desktop_core, "agent_net_entries", None)
        if callable(agent_rows):
            for net_log in self._agent_net_logs():
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
            record = reverter(workspace, changes, backups, **self._vault_kw())
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
            record = reverter(workspace, path, indices, backups, **self._vault_kw())
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
            restored = undo(record, **self._vault_kw())
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

    def _fault_failed(self, kind: str, message: str, command: str = "") -> None:
        self._show_fault(kind, message)
        if self._fault_card is not None:
            # The command is shown verbatim: branding would rename the program in it.
            exact = f"\n\nStart it yourself, then try again:\n{command}" if command else ""
            self._fault_card.body_label.setText(
                f"{self._fault_text(kind, 'body')}\n\nThat did not work: {_brand_runtime_text(message)[:200]}{exact}")

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
                planner = getattr(desktop_core, "model_server_start_plan", None)
                plan = planner() if callable(planner) else []
                if not plan:
                    raise RuntimeError("The model server is not running and its program was not found. "
                                       "Start it, then try again.")
                problems: list[str] = []
                for way in plan:
                    if _start_model_server(way, detector, problems):
                        break
                else:
                    manual[0] = next((_as_text(_field(w, "manual", "")) for w in plan if _field(w, "manual", "")), "")
                    raise RuntimeError("Could not start the model server: " + "; ".join(problems))
            installed = getattr(core, "installed_models", None)
            return list(installed()) if callable(installed) else []

        desktop_core = self.desktop_core
        manual = [""]                            # filled by the job when every way failed

        def done(models: list[str]) -> None:
            self._clear_fault(self._fault_text("server", "done"))
            self._model_probe_complete(("", models))

        worker = self._start_worker(job)
        worker.signals.result.connect(done)
        worker.signals.error.connect(lambda message: self._fault_failed("server", message, manual[0]))

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
        if self.vault is not None:
            # With encryption the lock's passphrase is the vault password.
            if self.vault.has_password or not passphrase:
                return False
            self.vault.set_password(passphrase)
            self.passwordChanged.emit()
            return True
        hasher = getattr(self.desktop_core, "hash_passphrase", None)
        if not passphrase or not callable(hasher):
            return False
        self.set_lock_setting("passphrase", hasher(passphrase))
        return True

    def has_passphrase(self) -> bool:
        if self.vault is not None:
            return self.vault.has_password
        return isinstance(self._lock_settings().get("passphrase"), dict)

    def _verify_passphrase(self, text: str) -> bool:
        if self.vault is not None:
            if getattr(self.vault, "locked", False):
                try:
                    self.vault.unlock(text)          # the key comes back only from the password
                except Exception:
                    return False
                return True
            return self.vault.check_password(text)
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
        self._drop_key_if_locked()
        return True

    def _drop_key_if_locked(self) -> None:
        """Take the data key out of memory while the window is locked.

        A run that is still going keeps the key until it ends and has saved
        its result; `_set_active` calls back here then."""
        if self.vault is None or self.lock_overlay.isHidden() or self.active_kind:
            return
        locker = getattr(self.vault, "lock", None)
        if not callable(locker) or getattr(self.vault, "locked", False):
            return
        self._remember_draft()                       # written while the key is still here
        if locker():
            self.inspector.append_activity("Data key removed from memory until unlock")

    def _after_unlock(self) -> None:
        self._egress_counts = None                   # counts read while locked came back empty
        self._refresh_session_rows()
        self.set_status("Unlocked.")

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
        if not self.feature("lock") or getattr(self.vault, "locked", False):
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
            _apply_native_dark_title_bar(self)
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


_QUIT = object()


def _open_vault_for_startup(desktop_core: Any, root: Path) -> tuple[Any, str]:
    """Open the encrypted data folder before the window reads anything from it.

    Returns (vault, notice): the vault, None to run unencrypted (with the reason
    in notice), or _QUIT when the person chose to quit or the data cannot be
    opened. Never silently falls back to plaintext over existing encrypted data."""
    opener = _optional_callable(desktop_core, "open_vault")
    available = _optional_callable(desktop_core, "encryption_available")
    if opener is None or available is None:
        return None, ""
    root.mkdir(parents=True, exist_ok=True)
    vault_file = root / desktop_core.VAULT_FILE
    if not available():
        if vault_file.is_file():
            QMessageBox.critical(None, "Onionmind", "Your Onionmind data is encrypted, but the cryptography "
                                 "package that opens it is missing. Run the Onionmind updater to install it.")
            return _QUIT, ""
        return None, ("Not encrypted: the cryptography package is missing. "
                      "Run the Onionmind updater to install it.")
    while True:
        try:
            if desktop_core.vault_needs_password(root):
                opened: list[Any] = []

                def attempt(text: str) -> Optional[str]:
                    try:
                        opened.append(opener(root, password=text))
                    except desktop_core.WrongPassword:
                        return "That password is not right."
                    return None

                dialog = UnlockDialog("password", attempt)
                dialog.exec()
                if dialog.outcome == "erase":
                    desktop_core.erase_data_folder(root)
                    continue
                if dialog.outcome != "unlocked" or not opened:
                    return _QUIT, ""
                return opened[0], ""
            vault = opener(root)
        except desktop_core.KeystoreUnavailable as exc:
            dialog = UnlockDialog("missing-key", detail=_as_text(exc))
            dialog.exec()
            if dialog.outcome == "erase":
                desktop_core.erase_data_folder(root)
                continue
            return _QUIT, ""
        except desktop_core.VaultError as exc:
            QMessageBox.critical(None, "Onionmind", f"Onionmind's data could not be opened: {exc}")
            return _QUIT, ""
        _upgrade_lock_passphrase(desktop_core, root, vault)
        return vault, ""


def _upgrade_lock_passphrase(desktop_core: Any, root: Path, vault: Any) -> None:
    """An older version kept a lock passphrase as a hash; offer it as the password."""
    settings_store = desktop_core.SettingsStore(root / "settings.json", defaults={}, vault=vault)
    settings = settings_store.load()
    lock = settings.get("lock")
    record = lock.get("passphrase") if isinstance(lock, dict) else None
    if not isinstance(record, dict):
        return

    def attempt(text: str) -> Optional[str]:
        if not desktop_core.verify_passphrase(text, record):
            return "That passphrase is not right."
        vault.set_password(text)
        return None

    dialog = UnlockDialog("upgrade", attempt)
    dialog.exec()
    if dialog.outcome in {"unlocked", "skip"}:
        lock = dict(lock)
        lock.pop("passphrase", None)
        settings["lock"] = lock
        settings_store.save(settings)


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
    desktop_core = _load_desktop_core()
    vault, notice = (None, "") if demo else _open_vault_for_startup(desktop_core, desktop_data_root())
    if vault is _QUIT:
        return 1
    window = OnionmindWindow(core_module, desktop_core, demo=demo, vault=vault)
    window.encryption_notice = notice
    if notice:
        window.set_status(notice)
    window._absorb_agent_log()                      # a run that ended with the app closed
    sweep = _optional_callable(desktop_core, "sweep_agent_scratch")
    if vault is not None and sweep is not None:
        logging = window.feature("netlog") or window.feature("boundary")
        sweep(window._egress_log() if logging else None)   # a run the app never saw end
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
