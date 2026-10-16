"""The model manager, the feature toggles and the settings dialog.

Part of onionmind_desktop, which re-exports every name defined here.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
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
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from onionmind_desktop_theme import (
    _as_text,
    _brand_runtime_text,
    _field,
    _optional_callable,
    SafeWorker,
    theme_tokens,
    WorkerSignals,
)

from onionmind_desktop_panes import UpdateBridge


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

    def _encryption_section(self) -> QWidget:
        """Encryption at rest: what protects the data now, and the optional
        password that unlocks it when Onionmind opens."""
        section = QWidget()
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 8, 0, 0)
        heading = QLabel("Encryption")
        heading.setObjectName("brand")
        layout.addWidget(heading)
        window = self._window()
        vault = getattr(window, "vault", None)
        self.encryption_status = QLabel("")
        self.encryption_status.setWordWrap(True)
        layout.addWidget(self.encryption_status)
        self.password_current = QLineEdit()
        self.password_new = QLineEdit()
        self.password_repeat = QLineEdit()
        for field, text in ((self.password_current, "Current password"), (self.password_new, "New password"),
                            (self.password_repeat, "Repeat the new password")):
            field.setEchoMode(QLineEdit.EchoMode.Password)
            field.setPlaceholderText(text)
            field.setAccessibleName(text)
            layout.addWidget(field)
        buttons = QHBoxLayout()
        self.password_set_button = QPushButton("Set password")
        self.password_set_button.setObjectName("primaryButton")
        self.password_remove_button = QPushButton("Remove password")
        buttons.addWidget(self.password_set_button)
        buttons.addWidget(self.password_remove_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.password_feedback = QLabel(
            "Optional. With a password, Onionmind asks for it each time it opens and the key is "
            "nowhere on this machine. If you forget it, your conversations and settings cannot "
            "be recovered - not by you, not by anyone.")
        self.password_feedback.setObjectName("meta")
        self.password_feedback.setWordWrap(True)
        layout.addWidget(self.password_feedback)
        transfer = QHBoxLayout()
        self.export_button = QPushButton("Export…")
        self.export_button.setToolTip("Save every conversation and your settings to one password-protected file")
        self.import_button = QPushButton("Import…")
        self.import_button.setToolTip("Add the conversations from an Onionmind export file")
        self.export_button.clicked.connect(self._export_data)
        self.import_button.clicked.connect(self._import_data)
        transfer.addWidget(self.export_button)
        transfer.addWidget(self.import_button)
        transfer.addStretch(1)
        layout.addLayout(transfer)
        self.transfer_feedback = QLabel(
            "Move your conversations to another computer or phone, or keep a backup. The file is "
            "encrypted with a password you choose for it; it opens anywhere with that password.")
        self.transfer_feedback.setObjectName("meta")
        self.transfer_feedback.setWordWrap(True)
        layout.addWidget(self.transfer_feedback)
        if window is None or getattr(getattr(window, "session_bridge", None), "store", None) is None:
            self.export_button.setEnabled(False)
            self.import_button.setEnabled(False)
        if vault is None:
            for widget in (self.password_current, self.password_new, self.password_repeat,
                           self.password_set_button, self.password_remove_button, self.password_feedback):
                widget.hide()
            notice = _as_text(getattr(window, "encryption_notice", "")) if window is not None else ""
            self.encryption_status.setText(notice or "Not encrypted: files in the data folder are plain text.")
            self.encryption_status.setObjectName("danger")
        else:
            self.password_set_button.clicked.connect(self._set_password)
            self.password_remove_button.clicked.connect(self._remove_password)
            self._describe_encryption()
        return section

    def _describe_encryption(self) -> None:
        vault = getattr(self._window(), "vault", None)
        if vault is None:
            return
        self.encryption_status.setText(vault.describe())
        has = vault.has_password
        self.password_current.setVisible(has)
        self.password_remove_button.setVisible(has)
        self.password_set_button.setText("Change password" if has else "Set password")

    def _set_password(self) -> None:
        window = self._window()
        vault = getattr(window, "vault", None)
        new, repeat = self.password_new.text(), self.password_repeat.text()
        if vault is None:
            return
        if len(new) < 8:
            self.password_feedback.setText("Use at least 8 characters; a few words are easier to remember.")
            return
        if new != repeat:
            self.password_feedback.setText("The two new passwords do not match.")
            return
        try:
            vault.set_password(new, current=self.password_current.text() if vault.has_password else None)
        except Exception as exc:
            self.password_feedback.setText(_as_text(exc) or "The password could not be set.")
            return
        for field in (self.password_current, self.password_new, self.password_repeat):
            field.clear()
        self.password_feedback.setText("Password set. Onionmind will ask for it each time it opens. "
                                       "Keep it somewhere safe: it cannot be reset.")
        self._describe_encryption()
        window.passwordChanged.emit()

    def _remove_password(self) -> None:
        window = self._window()
        vault = getattr(window, "vault", None)
        if vault is None or not vault.has_password:
            return
        try:
            vault.remove_password(self.password_current.text())
        except Exception as exc:
            self.password_feedback.setText(_as_text(exc) or "The password could not be removed.")
            return
        self.password_current.clear()
        self.password_feedback.setText("Password removed. The data stays encrypted; "
                                       "the key is back in this machine's key store.")
        self._describe_encryption()
        window.passwordChanged.emit()

    def _export_data(self) -> None:
        window = self._window()
        if window is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export Onionmind data", str(Path.home() / "onionmind-export.onionmind"),
                                              "Onionmind export (*.onionmind)")
        if not path:
            return
        if not path.endswith(".onionmind"):
            path += ".onionmind"
        sent: list[int] = []

        def attempt(password: str, _settings: bool) -> Optional[str]:
            try:
                sent.append(window.export_data(path, password))
            except Exception as exc:
                return _as_text(exc) or "The export could not be written."
            return None

        dialog = TransferDialog("export", attempt, parent=self)
        dialog.exec()
        if sent:
            self.transfer_feedback.setText(f"Exported {sent[0]} conversation(s) and your settings to {path}. "
                                           "Anyone with the file and its password can read them.")

    def _import_data(self) -> None:
        window = self._window()
        if window is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import Onionmind data", str(Path.home()),
                                              "Onionmind export (*.onionmind);;All files (*)")
        if not path:
            return
        result: list[dict[str, int]] = []

        def attempt(password: str, settings: bool) -> Optional[str]:
            try:
                result.append(window.import_data(path, password, settings=settings))
            except Exception as exc:
                return _as_text(exc) or "The file could not be imported."
            return None

        dialog = TransferDialog("import", attempt, parent=self)
        dialog.exec()
        if result:
            counts = result[0]
            text = f"Imported {counts.get('added', 0)} conversation(s)."
            if counts.get("existing"):
                text += f" {counts['existing']} were already here and were left as they are."
            if counts.get("invalid"):
                text += f" {counts['invalid']} could not be read and were skipped."
            if counts.get("settings"):
                text += " Settings replaced; some take effect after a restart."
            self.transfer_feedback.setText(text)

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
        encrypted = getattr(window, "vault", None) is not None
        if encrypted:
            # One secret: the idle lock asks for the Encryption password above.
            self.passphrase_field.hide()
            self.passphrase_button.hide()
            label = form.labelForField(pass_row)
            if label is not None:
                label.hide()
        self.lock_feedback = QLabel(
            "The lock asks for your Encryption password (above)." if encrypted
            else "Only a salted scrypt hash of the passphrase is stored.")
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
            self.lock_feedback.setText(
                "Set a password under Encryption first; without one anybody could unlock."
                if getattr(window, "vault", None) is not None
                else "Set a passphrase first; without one anybody could unlock.")
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
        outer.addWidget(self._encryption_section())

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
        available = bool(update_bridge and update_bridge.available)
        # The installer's source install was labelled a "development copy", and
        # its Check button silently did nothing; say what does update it.
        source_install = (Path(__file__).resolve().parent / ".onionmind-revision").is_file()
        version = QLabel(
            update_bridge.revision_label() if available
            else "Installed from source: update it with the onionmind-update command. "
                 "This in-app updater replaces only the standalone Onionmind.exe bundle."
            if source_install
            else "Development copy — the updater applies to an installed Onionmind bundle"
        )
        version.setWordWrap(True)
        updates_row.addWidget(version, 1)
        self.check_updates_button = QPushButton("Check for updates")
        self.check_updates_button.setAccessibleName("Check for Onionmind updates over Tor")
        self.check_updates_button.setEnabled(available)
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
        # The window class marks itself; the settings module cannot import it.
        return parent if getattr(type(parent), "IS_ONIONMIND_WINDOW", False) else None

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
                "Tor is not up. Turn it on with the Tor button in the toolbar, then check "
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


class UnlockDialog(QDialog):
    """Before the window opens: the password for the encrypted data folder,
    or what to do when its key is gone.

    kind "password": unlock, or erase everything after a forgotten password.
    kind "missing-key": the key store lost the key; quit or erase.
    kind "upgrade": an older lock passphrase becomes the password, or is dropped.
    `attempt(text)` returns an error to show, or None when it worked.
    `outcome` ends as "unlocked", "erase", "skip" or "quit"."""

    def __init__(self, kind: str, attempt: Optional[Callable[[str], Optional[str]]] = None,
                 detail: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.attempt = attempt
        self.outcome = "quit"
        self.setWindowTitle("Onionmind")
        self.setModal(True)
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(10)
        titles = {
            "password": "Unlock Onionmind",
            "missing-key": "The key to your data is missing",
            "upgrade": "Use your lock passphrase as your password",
        }
        copies = {
            "password": "Your conversations, projects and settings are encrypted on this machine. "
                        "Enter your password to open them.",
            "missing-key": (detail or "The data key is no longer in this machine's key store.")
                           + " The encrypted data cannot be read without it.",
            "upgrade": "Onionmind now encrypts everything it keeps. You had set a lock passphrase: "
                       "enter it to make it your password. Onionmind will then ask for it each time "
                       "it opens, and the data cannot be read without it.",
        }
        title = QLabel(titles[kind])
        title.setObjectName("brand")
        body = QLabel(copies[kind])
        body.setObjectName("meta")
        body.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(body)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("Passphrase" if kind == "upgrade" else "Password")
        self.password.setAccessibleName(self.password.placeholderText())
        self.password.returnPressed.connect(self._submit)
        self.error = QLabel("")
        self.error.setObjectName("danger")
        self.error.setWordWrap(True)
        self.error.hide()
        buttons = QHBoxLayout()
        self.secondary = QPushButton({"password": "Forgot your password?", "missing-key": "Erase and start over",
                                      "upgrade": "Don't use a password"}[kind])
        self.secondary.setObjectName("dangerButton" if kind != "upgrade" else "")
        self.secondary.clicked.connect(self._secondary)
        self.primary = QPushButton({"password": "Unlock", "missing-key": "Quit", "upgrade": "Use as password"}[kind])
        self.primary.setObjectName("primaryButton")
        self.primary.setDefault(True)
        self.primary.clicked.connect(self._submit)
        buttons.addWidget(self.secondary)
        buttons.addStretch(1)
        buttons.addWidget(self.primary)
        if kind != "missing-key":
            layout.addWidget(self.password)
        layout.addWidget(self.error)
        layout.addLayout(buttons)
        self.password.setFocus(Qt.FocusReason.OtherFocusReason)

    def _submit(self) -> None:
        if self.kind == "missing-key":
            self.outcome = "quit"
            self.reject()
            return
        text = self.password.text()
        if not text:
            self._show_error("Enter your password." if self.kind == "password" else "Enter your passphrase.")
            return
        problem = self.attempt(text) if self.attempt is not None else None
        if problem:
            self._show_error(problem)
            self.password.selectAll()
            return
        self.password.clear()
        self.outcome = "unlocked"
        self.accept()

    def _show_error(self, text: str) -> None:
        self.error.setText(text)
        self.error.show()

    def _secondary(self) -> None:
        if self.kind == "upgrade":
            self.outcome = "skip"
            self.accept()
            return
        answer = QMessageBox.warning(
            self,
            "Erase all Onionmind data?",
            "This permanently deletes every conversation, project list, network log and setting "
            "Onionmind keeps, and its key. There is no way to get them back. Your project folders "
            "and models are not touched.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Discard,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Discard:
            self.outcome = "erase"
            self.accept()

    def reject(self) -> None:
        self.outcome = "quit"                       # Esc or the close box
        super().reject()


class TransferDialog(QDialog):
    """The password for an export file: chosen twice to export, typed once to
    import (with the choice to take the file's settings too).
    `attempt(password, include_settings)` returns an error to show, or None."""

    def __init__(self, kind: str, attempt: Callable[[str, bool], Optional[str]],
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.attempt = attempt
        self.outcome = "cancel"
        self.setWindowTitle("Onionmind")
        self.setModal(True)
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(10)
        title = QLabel("Export your data" if kind == "export" else "Import an export")
        title.setObjectName("brand")
        body = QLabel(
            "Choose a password for this file. It is not your Onionmind password, and without it the "
            "file cannot be opened - not by you, not by anyone." if kind == "export" else
            "Enter the password the file was exported with. Conversations already here are kept; "
            "the file's conversations are added beside them.")
        body.setObjectName("meta")
        body.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(body)
        self.password = QLineEdit()
        self.repeat = QLineEdit()
        for field, text in ((self.password, "Export password" if kind == "export" else "Password"),
                            (self.repeat, "Repeat the password")):
            field.setEchoMode(QLineEdit.EchoMode.Password)
            field.setPlaceholderText(text)
            field.setAccessibleName(text)
            field.returnPressed.connect(self._submit)
        layout.addWidget(self.password)
        if kind == "export":
            layout.addWidget(self.repeat)
        self.settings = QCheckBox("Also replace my settings with the ones in the file")
        if kind == "import":
            layout.addWidget(self.settings)
        self.error = QLabel("")
        self.error.setObjectName("danger")
        self.error.setWordWrap(True)
        self.error.hide()
        layout.addWidget(self.error)
        buttons = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.primary = QPushButton("Export" if kind == "export" else "Import")
        self.primary.setObjectName("primaryButton")
        self.primary.setDefault(True)
        self.primary.clicked.connect(self._submit)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(self.primary)
        layout.addLayout(buttons)
        self.password.setFocus(Qt.FocusReason.OtherFocusReason)

    def _submit(self) -> None:
        text = self.password.text()
        if self.kind == "export":
            if len(text) < 8:
                return self._show_error("Use at least 8 characters; a few words are easier to remember.")
            if text != self.repeat.text():
                return self._show_error("The two passwords do not match.")
        elif not text:
            return self._show_error("Enter the file's password.")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            problem = self.attempt(text, self.settings.isChecked())
        finally:
            QApplication.restoreOverrideCursor()
        if problem:
            self.password.selectAll()
            return self._show_error(problem)
        self.password.clear()
        self.repeat.clear()
        self.outcome = "done"
        self.accept()

    def _show_error(self, text: str) -> None:
        self.error.setText(text)
        self.error.show()
