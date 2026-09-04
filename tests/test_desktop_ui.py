"""Interaction coverage for Onionmind's native desktop controls."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QEvent, QProcess, QStandardPaths, Qt, QTimer
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import (
        QApplication,
        QDialogButtonBox,
        QPushButton,
        QScrollArea,
        QToolButton,
        QWidget,
    )

    import onionmind_desktop as ui
    import onionmind_desktop_core as desktop_core

    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False


class _CoreStub:
    MODEL = "inferno"
    BACKEND = "onionmind"


@unittest.skipUnless(QT_AVAILABLE, "PySide6 desktop runtime is not installed")
class DesktopUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        QStandardPaths.setTestModeEnabled(True)
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")
        cls.app.setStyleSheet(ui.STYLE_SHEET)

    def _wait_until(self, predicate, timeout_ms: int = 4000) -> bool:
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.01)
        self.app.processEvents()
        return bool(predicate())

    def _window(self) -> "ui.OnionmindWindow":
        window = ui.OnionmindWindow(_CoreStub(), desktop_core, demo=True)
        window.save_current_session = lambda: True
        window.show()
        self.app.processEvents()
        return window

    def _close(self, widget: QWidget) -> None:
        widget.close()
        widget.deleteLater()
        self.app.processEvents()

    def test_tor_pill_click_starts_background_tor(self) -> None:
        """The pill drew a pointing-hand cursor and its clicked signal went
        nowhere, so the only way to start Tor was Chat's one-turn search
        consent - unreachable from Agent mode, whose own refusal told the user
        to start Tor from this toolbar."""
        window = self._window()
        try:
            calls: list[bool] = []

            def fake_start(*args, **kwargs):
                del args, kwargs
                calls.append(True)
                return 9150

            window.core.start_tor_hidden = fake_start
            window.tor_phase = "off"             # demo state opens on "running"
            window.tor_status.clicked.emit()
            self.assertTrue(
                self._wait_until(
                    lambda: bool(calls) and window.tor_phase in ("running", "proxy")
                )
            )
            self.assertIn("9150", window.tor_status.label.text())
        finally:
            self._close(window)

    def test_tor_pill_click_during_startup_probe_starts_rather_than_cancels(self) -> None:
        """A real window opens in the "probing" phase, not "off". Treating that
        as a start-in-progress made the first click after launch stop Tor."""
        window = self._window()
        try:
            started: list[bool] = []
            window.core.start_tor_hidden = lambda *a, **k: (started.append(True), 9150)[1]
            window.tor_phase = "probing"
            window.tor_status.clicked.emit()
            self.app.processEvents()
            self.assertEqual(window.tor_phase, "starting")
            self.assertTrue(self._wait_until(lambda: bool(started)))
        finally:
            self._close(window)

    def test_tor_pill_click_stops_the_tor_onionmind_started(self) -> None:
        """The same control stops it again, and only ever Onionmind's own."""
        window = self._window()
        try:
            stopped: list[bool] = []
            window.core.stop_managed_tor = lambda: stopped.append(True)
            window.core._managed_tor_process = object()   # ours, so ours to stop
            window.tor_phase = "running"
            window.tor_status.clicked.emit()
            self.app.processEvents()
            self.assertEqual(stopped, [True])
            self.assertEqual(window.tor_phase, "off")

            # A listener Onionmind only found is left alone.
            stopped.clear()
            window.core._managed_tor_process = None
            window.tor_phase = "running"
            window.tor_status.clicked.emit()
            self.app.processEvents()
            self.assertEqual(stopped, [])
            self.assertEqual(window.tor_phase, "running")
        finally:
            self._close(window)

    def test_agent_yolo_toggle_reaches_the_launcher_and_says_so(self) -> None:
        """YOLO is a run-shaping choice, so it has to reach the argv that starts
        the agent - and the label beside it has to name the mode that is armed,
        not the default."""
        window = self._window()
        try:
            # _launcher refuses without a real core to route through Tor.
            window.harness_bridge.core = type("C", (), {
                "__file__": "onionmind.py", "run_agent": staticmethod(lambda *a, **k: 0)})
            window.set_mode("agent")
            self.assertTrue(window.yolo_consent.isVisible())
            self.assertEqual(window.approval_state.text(),
                             "Protected actions stop safely")

            argv = window.harness_bridge._launcher("m", "task", "C:/work", False)
            self.assertIsNotNone(argv)
            self.assertNotIn("--yolo", argv)
            self.assertIn("--cwd", argv)
            self.assertEqual(argv[-1], "task")

            window.yolo_consent.setChecked(True)
            self.app.processEvents()
            self.assertIn("YOLO", window.approval_state.text())

            armed = window.harness_bridge._launcher("m", "task", "C:/work", True)
            self.assertIn("--yolo", armed)
            # The task stays the last argument whichever mode is armed, so the
            # flag can never be swallowed as part of the prompt.
            self.assertEqual(armed[-1], "task")

            # Chat mode has no agent to approve for.
            window.set_mode("chat")
            self.assertFalse(window.yolo_consent.isVisible())
        finally:
            self._close(window)

    def test_instant_worker_result_survives_a_late_connect(self) -> None:
        """The stall this guards: Agent mode sitting on "Preparing..." forever.

        Every call site connects to the worker AFTER _start_worker returns, and
        the job that fails fast - harness check() reporting "Tor is not up" with
        no subprocess to spawn - used to finish before that connect and drop its
        result on the floor, leaving no worker alive and no message on screen.
        """
        window = self._window()
        try:
            received: list[object] = []
            worker = window._start_worker(lambda signals: "done")
            worker.signals.result.connect(received.append)
            self.assertTrue(self._wait_until(lambda: received == ["done"]))
        finally:
            self._close(window)

    def test_left_rail_buttons_emit_and_unavailable_actions_disable(self) -> None:
        rail = ui.LeftRail()
        events: list[tuple[str, str]] = []
        rail.newTaskRequested.connect(lambda: events.append(("new", "")))
        rail.addSessionRequested.connect(lambda: events.append(("add-session", "")))
        rail.openFolderRequested.connect(lambda: events.append(("folder", "")))
        rail.removeProjectRequested.connect(
            lambda value: events.append(("remove-project", value))
        )
        rail.deleteProjectRequested.connect(
            lambda value: events.append(("delete-project", value))
        )
        rail.modelsRequested.connect(lambda: events.append(("models", "")))
        rail.settingsRequested.connect(lambda: events.append(("settings", "")))
        rail.exportRequested.connect(lambda: events.append(("export", "")))
        rail.archiveRequested.connect(lambda value: events.append(("archive", value)))
        rail.deleteSessionRequested.connect(
            lambda value: events.append(("delete-session", value))
        )

        self.assertFalse(rail.export_button.isEnabled())
        self.assertFalse(rail.archive_button.isEnabled())
        self.assertFalse(rail.delete_session_button.isEnabled())
        self.assertFalse(rail.remove_project_button.isEnabled())
        self.assertFalse(rail.delete_project_button.isEnabled())
        rail.new_button.click()
        rail.folder_button.click()
        rail.add_project_button.click()
        rail.add_session_button.click()
        rail.models_button.click()
        rail.settings_button.click()
        rail.set_conversation_available(True)
        rail.export_button.click()
        rail.set_sessions([{"id": "session-1", "title": "Polish UI"}], "session-1")
        rail.archive_button.click()
        rail.delete_session_button.click()
        rail.add_project("C:/projects/onionmind")
        rail.remove_project_button.click()
        rail.delete_project_button.click()

        # The same scoped actions remain available from each list's context menu.
        rail.project_add_action.trigger()
        rail.session_remove_action.trigger()
        rail.session_delete_action.trigger()
        rail.session_add_action.trigger()
        rail.project_remove_action.trigger()
        rail.project_delete_action.trigger()

        self.assertEqual(events.count(("folder", "")), 3)
        self.assertIn(("new", ""), events)
        self.assertEqual(events.count(("add-session", "")), 2)
        self.assertIn(("models", ""), events)
        self.assertIn(("settings", ""), events)
        self.assertIn(("export", ""), events)
        self.assertEqual(events.count(("archive", "session-1")), 2)
        self.assertEqual(events.count(("delete-session", "session-1")), 2)
        self.assertEqual(
            events.count(("remove-project", "C:/projects/onionmind")), 2
        )
        self.assertEqual(
            events.count(("delete-project", "C:/projects/onionmind")), 2
        )
        self._close(rail)

    def test_left_rail_can_remove_project_rows_without_deleting_the_folder(self) -> None:
        rail = ui.LeftRail()
        path = "C:/projects/kept-on-disk"
        rail.add_project(path)

        self.assertEqual(rail.projects.count(), 1)
        self.assertTrue(rail.remove_project(path))
        self.assertEqual(rail.projects.count(), 0)
        self.assertFalse(rail.remove_project(path))
        self.assertFalse(rail.remove_project_button.isEnabled())
        self._close(rail)

    def test_window_adds_and_permanently_deletes_saved_sessions(self) -> None:
        window = self._window()
        with tempfile.TemporaryDirectory() as temporary:
            window.session_bridge = ui.SessionBridge(
                desktop_core, Path(temporary) / "sessions"
            )
            window.session_objects = {}
            window.current_session = None
            window.chat_messages = []

            window.add_session()
            sessions = window.session_bridge.list()
            self.assertEqual(len(sessions), 1)
            session_id = sessions[0].id
            self.assertEqual(window.left_rail.sessions.count(), 1)
            self.assertEqual(window.current_session.id, session_id)

            with mock.patch.object(
                window, "_confirm_permanent_deletion", return_value=True
            ):
                window.delete_session_from_machine(session_id)

            self.assertEqual(window.session_bridge.list(), [])
            self.assertEqual(window.left_rail.sessions.count(), 0)
            self.assertIsNone(window.current_session)
        self._close(window)

    def test_project_remove_keeps_folder_and_machine_delete_removes_it(self) -> None:
        window = self._window()
        window.left_rail.projects.clear()
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "delete-me"
            project.mkdir()
            (project / "work.txt").write_text("local work", encoding="utf-8")
            path = str(project.resolve())
            window.workspace = path
            window.terminal.set_workspace(path)
            window.settings_data.update(
                workspace=path,
                recent_projects=[path],
            )
            window.left_rail.add_project(path)

            window.remove_project_from_menu(path)
            self.assertTrue(project.is_dir())
            self.assertEqual(window.left_rail.projects.count(), 0)
            self.assertEqual(window.workspace, path)
            self.assertEqual(window.settings_data["workspace"], "")

            window.settings_data.update(workspace=path, recent_projects=[path])
            window.left_rail.add_project(path)
            with mock.patch.object(
                window, "_confirm_permanent_deletion", return_value=True
            ):
                window.delete_project_from_machine(path)
            self.assertTrue(
                self._wait_until(
                    lambda: window._project_delete_pending is None
                    and not project.exists()
                )
            )
            self.assertEqual(window.left_rail.projects.count(), 0)
            self.assertIsNone(window.workspace)
            self.assertEqual(window.repo_label.text(), "No project")
        self._close(window)

    def test_project_machine_delete_refuses_protected_broad_paths(self) -> None:
        window = self._window()
        protected = (
            Path(Path.home().anchor),
            Path.home(),
            window.data_root,
            ui.MODULE_DIR,
        )
        for path in protected:
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    window._validated_project_delete_target(str(path))
        self._close(window)

    def test_terminal_stop_clear_close_and_command_states(self) -> None:
        terminal = ui.TerminalPane(desktop_core)
        closed: list[bool] = []
        terminal.closeRequested.connect(lambda: closed.append(True))
        self.assertFalse(terminal.stop_button.isEnabled())
        self.assertFalse(terminal.clear_button.isEnabled())

        terminal.append("temporary output")
        self.assertTrue(terminal.clear_button.isEnabled())
        terminal.clear_button.click()
        self.assertEqual(terminal.output.toPlainText(), "")
        self.assertFalse(terminal.clear_button.isEnabled())
        terminal.close_button.click()
        self.assertEqual(closed, [True])

        with tempfile.TemporaryDirectory() as temporary:
            terminal.set_workspace(temporary)
            command = "Start-Sleep -Seconds 5" if os.name == "nt" else "sleep 5"
            terminal.command.setText(command)
            terminal.run_current()
            self.assertTrue(
                self._wait_until(
                    lambda: terminal.process.state() == QProcess.ProcessState.Running
                )
            )
            self.assertTrue(terminal.stop_button.isEnabled())
            self.assertFalse(terminal.command.isEnabled())
            terminal.stop_button.click()
            self.assertTrue(
                self._wait_until(
                    lambda: terminal.process.state() == QProcess.ProcessState.NotRunning
                )
            )
            self.assertFalse(terminal.stop_button.isEnabled())
            self.assertTrue(terminal.command.isEnabled())
        self._close(terminal)

    def test_model_manager_add_and_close_controls(self) -> None:
        dialog = ui.ModelManagerDialog(
            ["inferno", "deepseek-r1:8b"],
            "inferno",
            lambda raw: "INFERNO" if "inferno" in raw else "ONIONMIND CUSTOM · 8B",
        )
        requested: list[str] = []
        dialog.pullRequested.connect(requested.append)
        self.assertFalse(dialog.pull_button.isEnabled())
        self.assertNotIn("deepseek", " ".join(dialog.models.item(i).text() for i in range(dialog.models.count())).lower())

        dialog.model_name.setText("blaze")
        self.assertTrue(dialog.pull_button.isEnabled())
        with mock.patch.object(
            ui.QMessageBox,
            "warning",
            return_value=ui.QMessageBox.StandardButton.Yes,
        ) as confirm_download:
            dialog.pull_button.click()
        confirm_download.assert_called_once()
        self.assertEqual(requested, ["blaze"])
        self.assertFalse(dialog.model_name.isEnabled())
        dialog.set_error("Ollama failed")
        self.assertTrue(dialog.model_name.isEnabled())
        self.assertNotIn("ollama", dialog.progress.format().lower())
        dialog.set_complete()
        self.assertFalse(dialog.pull_button.isEnabled())

        dialog.show()
        box = dialog.findChild(QDialogButtonBox)
        self.assertIsNotNone(box)
        assert box is not None
        close_button = box.button(QDialogButtonBox.StandardButton.Close)
        self.assertIsNotNone(close_button)
        assert close_button is not None
        close_button.click()
        self.assertFalse(dialog.isVisible())
        self._close(dialog)

    def test_inspector_refresh_is_available_only_with_a_workspace(self) -> None:
        inspector = ui.InspectorPane()
        refreshed: list[bool] = []
        inspector.refreshRequested.connect(lambda: refreshed.append(True))
        self.assertFalse(inspector.refresh_button.isEnabled())
        inspector.update_snapshot({"root": "C:/project", "is_git": False})
        self.assertTrue(inspector.refresh_button.isEnabled())
        inspector.refresh_button.click()
        self.assertEqual(refreshed, [True])
        self._close(inspector)

    def test_settings_storage_and_close_buttons_report_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            storage = Path(temporary) / "onionmind-storage"
            dialog = ui.SettingsDialog(storage, desktop_core.HARNESS_LIMITATION)
            dialog.show()
            open_button = next(
                button
                for button in dialog.findChildren(QPushButton)
                if button.accessibleName() == "Open Onionmind storage folder"
            )
            with mock.patch.object(ui.QDesktopServices, "openUrl", return_value=False):
                open_button.click()
            self.assertTrue(storage.is_dir())
            self.assertIn("could not open", dialog.storage_feedback.text().lower())

            box = dialog.findChild(QDialogButtonBox)
            self.assertIsNotNone(box)
            assert box is not None
            close_button = box.button(QDialogButtonBox.StandardButton.Close)
            self.assertIsNotNone(close_button)
            assert close_button is not None
            close_button.click()
            self.assertFalse(dialog.isVisible())
            self._close(dialog)

    def test_settings_dialog_fits_the_screen_and_scrolls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            storage = Path(temporary) / "onionmind-storage"
            dialog = ui.SettingsDialog(storage, desktop_core.HARNESS_LIMITATION)
            dialog.show()
            self.app.processEvents()
            # A laptop panel shorter than the stacked sections must still get
            # the whole dialog on screen, with the overflow scrolling instead
            # of pushing the Close row away.
            screen = dialog.screen() or self.app.primaryScreen()
            available = screen.availableGeometry()
            self.assertLessEqual(dialog.width(), available.width())
            self.assertLessEqual(dialog.height(), available.height())
            scroll = dialog.findChild(QScrollArea)
            self.assertIsNotNone(scroll)
            assert scroll is not None
            self.assertTrue(scroll.widgetResizable())
            self._close(dialog)

    def test_send_during_a_run_stops_it_and_sends_the_direction_next(self) -> None:
        window = self._window()
        window._set_active("chat")
        window.stop_event = threading.Event()
        window.composer.setPlainText("now do it differently")
        window.submit()
        # The run is stopping, and the typed direction is held to become the
        # next turn the moment the run finishes unwinding.
        self.assertTrue(window.stop_event.is_set())
        self.assertTrue(window._pending_redirect)
        self.assertEqual(window.composer.toPlainText(), "now do it differently")
        with mock.patch.object(window, "submit") as submit:
            window._set_active(None)
            # The redirect submit is deferred through the event loop so the
            # finishing run's handlers can unwind first.
            self.app.processEvents()
            submit.assert_called_once()

        # An empty composer during a run keeps the plain-stop meaning.
        window._set_active("chat")
        window.stop_event = threading.Event()
        window.composer.clear()
        window.submit()
        self.assertFalse(window._pending_redirect)
        self.assertTrue(window.stop_event.is_set())
        self._close(window)

    def test_window_actions_modes_and_responsive_toggles(self) -> None:
        window = self._window()
        window.resize(1420, 900)
        self.app.processEvents()
        self.assertTrue(window.rail_toggle.isChecked())
        self.assertTrue(window.terminal_toggle.isChecked())
        self.assertTrue(window.inspector_toggle.isChecked())

        window.resize(800, 700)
        self.app.processEvents()
        self.assertTrue(window.left_rail.isHidden())
        self.assertTrue(window.inspector.isHidden())
        self.assertFalse(window.rail_toggle.isChecked())
        self.assertFalse(window.inspector_toggle.isChecked())
        window.rail_toggle.click()
        self.assertFalse(window.left_rail.isHidden())
        window.rail_toggle.click()
        window.inspector_toggle.click()
        self.assertFalse(window.inspector.isHidden())
        window.terminal_toggle.click()
        self.assertTrue(window.terminal.isHidden())
        window.terminal_toggle.click()
        self.assertFalse(window.terminal.isHidden())

        window.chat_messages = []
        window.composer.clear()
        window.clear_attachments()
        window._sync_action_states()
        self.assertFalse(window.send_button.isEnabled())
        self.assertFalse(window.left_rail.export_button.isEnabled())
        window.composer.setPlainText("Polish the interface")
        self.assertTrue(window.send_button.isEnabled())
        window._set_active("agent")
        window.composer.clear()
        self.assertEqual(window.send_button.text(), "Stop")
        self.assertTrue(window.send_button.isEnabled())
        window._set_active(None)
        self.assertEqual(window.send_button.text(), "Send")
        self.assertFalse(window.send_button.isEnabled())

        with tempfile.TemporaryDirectory() as temporary:
            attachment = Path(temporary) / "notes.md"
            attachment.write_text("Onionmind", encoding="utf-8")
            attach_button = next(
                button
                for button in window.findChildren(QToolButton)
                if button.accessibleName() == "Attach files or images"
            )
            with mock.patch.object(
                ui.QFileDialog,
                "getOpenFileNames",
                return_value=([str(attachment)], ""),
            ):
                attach_button.click()
            self.assertEqual(window.attachments, [str(attachment.resolve())])
            self.assertTrue(window.send_button.isEnabled())
            clear_button = next(
                button
                for button in window.findChildren(QToolButton)
                if button.accessibleName() == "Remove all attachments"
            )
            clear_button.click()
            self.assertEqual(window.attachments, [])
            self.assertFalse(window.send_button.isEnabled())

        window.chat_button.click()
        self.assertEqual(window.mode, "chat")
        window.agent_button.click()
        self.assertEqual(window.mode, "agent")
        self._close(window)

    def test_window_send_open_export_models_and_settings_paths(self) -> None:
        window = self._window()
        window.chat_messages = []
        window._sync_action_states()
        starts: list[tuple[str, str]] = []
        window._start_chat = lambda: (starts.append(("chat", "")), window._set_active(None))
        window.set_mode("chat")
        window.composer.setPlainText("Hello Onionmind")
        window.send_button.click()
        self.assertEqual(starts, [("chat", "")])

        with tempfile.TemporaryDirectory() as temporary:
            window.refresh_workspace = lambda: None
            with mock.patch.object(
                ui.QFileDialog, "getExistingDirectory", return_value=temporary
            ):
                window.left_rail.folder_button.click()
            self.assertEqual(window.workspace, str(Path(temporary).resolve()))

            agent_tasks: list[str] = []
            window._start_harness = lambda task: (
                agent_tasks.append(task),
                window._set_active(None),
            )
            window.set_mode("agent")
            window.composer.setPlainText("Check every button")
            with mock.patch.object(
                ui.QMessageBox,
                "warning",
                return_value=ui.QMessageBox.StandardButton.Yes,
            ) as confirm_agent:
                window.send_button.click()
            confirm_agent.assert_called_once()
            self.assertEqual(agent_tasks, ["Check every button"])

            export_path = Path(temporary) / "conversation.md"
            with mock.patch.object(
                ui.QFileDialog,
                "getSaveFileName",
                return_value=(str(export_path), "Markdown (*.md)"),
            ):
                window.left_rail.export_button.click()
            self.assertTrue(export_path.exists())
            self.assertIn(
                "Model: `Qwen3.8 27B · heavy - ~12-16 GB VRAM`",
                export_path.read_text(encoding="utf-8"),
            )

        def close_modal() -> None:
            modal = QApplication.activeModalWidget()
            if modal is not None:
                modal.reject()

        QTimer.singleShot(0, close_modal)
        window.left_rail.models_button.click()
        self.assertIsNone(window._model_dialog)
        QTimer.singleShot(0, close_modal)
        window.left_rail.settings_button.click()
        self._close(window)

    def test_tor_pill_reports_managed_verified_and_off_states(self) -> None:
        window = self._window()
        window.demo = False
        core = window.core
        core._port = None

        class _Process:
            def __init__(self) -> None:
                self.alive = True
                self.stopped = False

            def poll(self):
                return None if self.alive else 0

        managed = _Process()
        stopped: list[bool] = []
        core._managed_tor_process = managed
        core.stop_managed_tor = lambda: stopped.append(True) or setattr(managed, "alive", False)
        window._show_local_tor_state(9150)
        self.assertEqual(window.tor_status.label.text(), "Running · 9150")
        self.assertEqual(window.tor_phase, "running")

        # A listener that answers but was never verified stays a warning, never "ready".
        core._managed_tor_process = None
        window._show_local_tor_state(9151)
        self.assertEqual(window.tor_status.label.text(), "Proxy · 9151")
        self.assertEqual(window.tor_phase, "proxy")

        window._show_local_tor_state(None)
        self.assertEqual(window.tor_status.label.text(), "Off")
        self.assertEqual(window.tor_phase, "off")
        self.assertFalse(stopped)
        self._close(window)

    def test_visible_copy_and_accessible_controls_use_onionmind_language(self) -> None:
        window = self._window()
        branded_error = ui._brand_runtime_text(
            "DeepSeek Harness via Ollama, llama.cpp, DSH, and Node.js"
        ).lower()
        for term in ("ollama", "deepseek", "harness", "dsh", "llama.cpp", "node.js"):
            self.assertNotIn(term, branded_error)
        self.assertEqual(ui._brand_runtime_text("test harness"), "test harness")
        window.set_model_options(["deepseek-r1:8b", "inferno:latest"], "deepseek-r1:8b")
        self.assertEqual(window.current_model_id(), "deepseek-r1:8b")
        # Model names are deliberately NOT branded away: the picker states the
        # real model and its weight class. Runtime and harness names still are.
        forbidden = ("ollama", "harness", " dsh", "llama.cpp", "node.js")
        visible: list[str] = [window.windowTitle()]
        for widget in window.findChildren(QWidget):
            for getter_name in (
                "text",
                "toolTip",
                "accessibleName",
                "placeholderText",
                "windowTitle",
            ):
                getter = getattr(widget, getter_name, None)
                if callable(getter):
                    try:
                        visible.append(str(getter()))
                    except TypeError:
                        pass
        visible.extend(
            window.model_combo.itemText(index)
            for index in range(window.model_combo.count())
        )
        copy = "\n".join(visible).lower()
        for term in forbidden:
            self.assertNotIn(term, copy)
        self.assertIn("deepseek-r1:8b", copy)
        self.assertIn("qwen3.8 27b · heavy - ~12-16 gb vram", copy)
        buttons = [
            *window.findChildren(QPushButton),
            *window.findChildren(QToolButton),
        ]
        for button in buttons:
            if button.parentWidget() is not None and button.parentWidget().__class__.__name__ == "QTabBar":
                continue
            self.assertTrue(button.text() or button.accessibleName())
        self._close(window)


@unittest.skipUnless(QT_AVAILABLE, "PySide6 desktop runtime is not installed")
class UpdatePermissionTests(unittest.TestCase):
    """The updater is reachable at all times but networks only with permission."""

    @classmethod
    def setUpClass(cls) -> None:
        QStandardPaths.setTestModeEnabled(True)
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")
        cls.app.setStyleSheet(ui.STYLE_SHEET)

    def _manifest(self):
        return desktop_core.parse_update_manifest(
            json.dumps(
                {
                    "revision": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                    "version": "1.0.1",
                    "asset": "Onionmind-Windows-x64.zip",
                    "asset_url": "https://github.com/Codemaster64/onionmind/releases/download/desktop-latest/Onionmind-Windows-x64.zip",
                    "size": 10,
                    "sha256": "a" * 64,
                }
            )
        )

    def _window_with_bridge(self, temporary: str):
        window = ui.OnionmindWindow(_CoreStub(), desktop_core, demo=False)
        window.save_current_session = lambda: True
        # These windows share the test-mode settings file; a previous test or
        # an earlier run may have persisted standing update permission or a
        # recent check timestamp. Every test here starts from "no permission
        # granted, nothing checked yet".
        window.settings_data["updates_autocheck_enabled"] = False
        window.settings_data.pop("updates_last_check", None)
        window.settings_bridge.save(window.settings_data)
        if window._update_timer is not None:
            window._update_timer.stop()
        bridge = ui.UpdateBridge(_CoreStub(), desktop_core)
        bridge.install_dir = Path(temporary) / "Onionmind-Windows-x64"
        bridge.work_dir = Path(temporary) / "onionmind-update"
        bridge.install_dir.mkdir(parents=True, exist_ok=True)
        (bridge.install_dir / desktop_core.UPDATE_REVISION_FILENAME).write_text(
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
        )
        window.update_bridge = bridge
        window.show()
        self.app.processEvents()
        return window, bridge

    def _close(self, widget) -> None:
        widget.close()
        widget.deleteLater()
        self.app.processEvents()

    def _wait_until(self, predicate, timeout_ms: int = 4000) -> bool:
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.01)
        self.app.processEvents()
        return bool(predicate())

    def test_updates_entry_is_always_present_and_neutral(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            window, _bridge = self._window_with_bridge(temporary)
            self.assertTrue(window.update_status.isVisible())
            self.assertEqual(window.update_status.text(), "Updates…")
            self.assertFalse(bool(window.update_status.property("attention")))
            self.assertFalse(window.update_permission_enabled())
            self._close(window)

    def test_without_permission_no_network_and_timer_stays_off(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            window, bridge = self._window_with_bridge(temporary)
            window.settings_data["updates_autocheck_enabled"] = False
            self.assertFalse(window.update_permission_enabled())

            def refuse(*args, **kwargs):
                raise AssertionError("update check ran without permission")

            # Let the startup service probes drain first so the worker set
            # below is uncontaminated by the model probe.
            self.assertTrue(self._wait_until(lambda: len(window._workers) == 0))
            with mock.patch.object(bridge, "check", side_effect=refuse):
                window._maybe_autocheck_updates()
                self.app.processEvents()
                time.sleep(0.2)
                self.app.processEvents()
                self.assertEqual(len(window._workers), 0)
                self.assertIsNone(window._update_timer)
            self._close(window)

    def test_granting_permission_checks_and_persists(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            window, bridge = self._window_with_bridge(temporary)
            manifest = self._manifest()
            with (
                mock.patch.object(bridge, "check", return_value=manifest),
                mock.patch.object(bridge, "tor_port", return_value=9150),
                mock.patch.object(bridge, "housekeep"),
            ):
                window.set_update_permission(True)
                self.assertTrue(window.update_permission_enabled())
                self.assertIsNotNone(window._update_timer)
                self.assertTrue(window._update_timer.isActive())
                self.assertTrue(
                    self._wait_until(
                        lambda: window.update_status.property("attention") is True
                        or window.update_status.property("attention") == True  # noqa: E712
                    )
                )
                self.assertIn("Update available", window.update_status.text())
                settings_path = window.settings_bridge.store.path
                self.assertIn("updates_autocheck_enabled", settings_path.read_text(encoding="utf-8"))

                window.set_update_permission(False)
                self.assertFalse(window._update_timer.isActive())
            self._close(window)

    def test_manual_check_requires_tor_and_then_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            window, bridge = self._window_with_bridge(temporary)
            dialog = ui.SettingsDialog(
                Path(temporary), desktop_core.HARNESS_LIMITATION, bridge, window
            )
            dialog.show()
            self.app.processEvents()
            self.assertFalse(dialog.autocheck_box.isChecked())
            self.assertTrue(dialog.check_updates_button.isEnabled())

            # Nothing listening and nothing verified: refuse without touching
            # the network.
            with mock.patch.object(bridge, "check", side_effect=AssertionError("network without Tor")):
                dialog.check_updates_button.click()
                self.app.processEvents()
                self.assertIn("tor is not up", dialog.update_feedback.text().lower())

            # A pill reading "Running" only proves a SOCKS port is listening.
            # The check must verify the circuit itself rather than refuse.
            manifest = self._manifest()
            verified: list[bool] = []
            window.core.tor_proxy_port = lambda: 9150
            window.core.tor_check = lambda: verified.append(True)
            with (
                mock.patch.object(bridge, "check", return_value=manifest),
                mock.patch.object(
                    bridge,
                    "tor_port",
                    side_effect=lambda: 9150 if verified else None,
                ),
            ):
                dialog.check_updates_button.click()
                self.assertTrue(
                    self._wait_until(
                        lambda: "available" in dialog.update_feedback.text().lower()
                    )
                )
                self.assertEqual(verified, [True])

            dialog._download_button = None
            with (
                mock.patch.object(bridge, "check", return_value=manifest),
                mock.patch.object(bridge, "tor_port", return_value=9150),
            ):
                dialog.check_updates_button.click()
                self.assertTrue(
                    self._wait_until(
                        lambda: "available" in dialog.update_feedback.text().lower()
                    )
                )
                self.assertIsNotNone(getattr(dialog, "_download_button", None))
                self.assertTrue(dialog._download_button.isEnabled())
            self._close(dialog)
            self._close(window)


@unittest.skipUnless(QT_AVAILABLE, "PySide6 desktop runtime is not installed")
class WindowLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        QStandardPaths.setTestModeEnabled(True)
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")
        cls.app.setStyleSheet(ui.STYLE_SHEET)

    def _close(self, widget: QWidget) -> None:
        widget.close()
        widget.deleteLater()
        self.app.processEvents()

    def _window(self, bridge: "ui.SettingsBridge") -> "ui.OnionmindWindow":
        window = ui.OnionmindWindow(_CoreStub(), desktop_core, demo=True)
        window.save_current_session = lambda: True
        # Demo mode skips persistence by design; point the window at an
        # isolated store and opt back in so save and restore run exactly the
        # production code paths.
        window.settings_bridge = bridge
        window.settings_data = bridge.load()
        window.demo = False
        window.show()
        self.app.processEvents()
        return window

    def test_layout_is_remembered_and_restored_between_launches(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bridge = ui.SettingsBridge(desktop_core, Path(temporary) / "settings.json")

            first = self._window(bridge)
            first.resize(1180, 741)
            first.main_splitter.setSizes([190, 660, 330])
            self.app.processEvents()
            self._close(first)

            stored = bridge.load()
            for key in ("window_geometry", "splitter_state", "rail_visible", "inspector_visible"):
                self.assertIn(key, stored)
            self.assertTrue(stored["rail_visible"])
            self.assertTrue(stored["inspector_visible"])

            second = self._window(bridge)
            second._restore_window_layout()
            self.app.processEvents()
            # restoreGeometry clamps to the available screen so a remembered
            # layout can never land bigger than the display; the exact clamp
            # margin is Qt's, so assert the contract rather than the constant.
            screen = second.screen() or self.app.primaryScreen()
            self.assertEqual(second.height(), 741)
            self.assertLessEqual(second.width(), screen.availableGeometry().width())
            self.assertNotEqual(second.width(), 1420)
            sizes = second.main_splitter.sizes()
            # The remembered rail width applies; the inspector lands on its
            # remembered width when the screen fits it, or its pane minimum
            # when the offscreen test screen cannot.
            self.assertAlmostEqual(sizes[0], 190, delta=10)
            self.assertGreaterEqual(sizes[2], 250)
            self.assertLessEqual(sizes[2], 330)
            self.assertFalse(second.left_rail.isHidden())
            self.assertFalse(second.inspector.isHidden())

            second.toggle_rail(False)
            second.toggle_inspector(False)
            self._close(second)
            self.assertFalse(bridge.load()["rail_visible"])
            self.assertFalse(bridge.load()["inspector_visible"])

            third = self._window(bridge)
            third._restore_window_layout()
            self.app.processEvents()
            self.assertTrue(third.left_rail.isHidden())
            self.assertTrue(third.inspector.isHidden())
            self._close(third)

    def test_settings_reset_button_restores_the_default_workbench(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bridge = ui.SettingsBridge(desktop_core, Path(temporary) / "settings.json")
            window = self._window(bridge)
            window.resize(1005, 700)
            self.app.processEvents()
            self._close(window)   # the odd layout is now remembered
            self.assertIn("window_geometry", bridge.load())

            window = self._window(bridge)
            dialog = ui.SettingsDialog(
                window.data_root, desktop_core.HARNESS_LIMITATION, parent=window
            )
            dialog.show()
            reset = next(
                button
                for button in dialog.findChildren(QPushButton)
                if button.accessibleName() == "Reset the remembered Onionmind window layout"
            )
            reset.click()
            self.assertIn("reset", dialog.window_feedback.text().lower())
            self.assertEqual((window.width(), window.height()), (1420, 900))
            self.assertFalse(window.left_rail.isHidden())
            self.assertFalse(window.inspector.isHidden())
            self.assertAlmostEqual(window.main_splitter.sizes()[0], 224, delta=10)
            self.assertAlmostEqual(window.main_splitter.sizes()[2], 292, delta=10)
            stored = bridge.load()
            for key in ("window_geometry", "splitter_state", "rail_visible", "inspector_visible"):
                self.assertNotIn(key, stored)
            self._close(dialog)
            self._close(window)

    def test_settings_keyboard_shortcut_is_registered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bridge = ui.SettingsBridge(desktop_core, Path(temporary) / "settings.json")
            window = self._window(bridge)
            sequences = {shortcut.key().toString() for shortcut in window.shortcuts}
            self.assertIn("Ctrl+,", sequences)
            self._close(window)


@unittest.skipUnless(QT_AVAILABLE, "PySide6 desktop runtime is not installed")
class WorkbenchPreferencesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        QStandardPaths.setTestModeEnabled(True)
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")
        cls.app.setStyleSheet(ui.STYLE_SHEET)

    def _close(self, widget: QWidget) -> None:
        widget.close()
        widget.deleteLater()
        self.app.processEvents()

    def _window(self, bridge: "ui.SettingsBridge") -> "ui.OnionmindWindow":
        window = ui.OnionmindWindow(_CoreStub(), desktop_core, demo=True)
        window.save_current_session = lambda: True
        # Demo mode skips persistence by design; point the window at an
        # isolated store and opt back in so preferences persist for real.
        window.settings_bridge = bridge
        window.settings_data = bridge.load()
        window.preferences = window._load_preferences()
        window.demo = False
        window.show()
        self.app.processEvents()
        return window

    def test_preferences_apply_live_and_persist(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bridge = ui.SettingsBridge(desktop_core, Path(temporary) / "settings.json")
            window = self._window(bridge)
            try:
                base_size = self.app.font().pointSizeF()
                window.set_preference("text_scale", "comfortable")
                self.assertEqual(window.preferences["text_scale"], "comfortable")
                if base_size > 0:
                    self.assertGreater(self.app.font().pointSizeF(), base_size)
                self.assertIn(f"{11 * 1.15:g}pt", self.app.styleSheet())
                self.assertEqual(bridge.load()["text_scale"], "comfortable")

                window.set_preference("enter_sends", False)
                self.assertFalse(window.composer.enterSends)
                self.assertFalse(bridge.load()["enter_sends"])

                window.set_preference("reduce_motion", "reduced")
                self.assertFalse(ui._ui_animations_enabled())
                window.set_preference("reduce_motion", "full")
                self.assertTrue(ui._ui_animations_enabled())
                window.set_preference("reduce_motion", "system")
                self.assertIsNone(ui._MOTION_OVERRIDE)

                window.set_preference("text_scale", "system")
                if base_size > 0:
                    self.assertAlmostEqual(self.app.font().pointSizeF(), base_size)
                self.assertEqual(self.app.styleSheet(), ui.STYLE_SHEET)
            finally:
                ui.set_motion_override("system")
                ui.apply_text_scale(1.0)
            self._close(window)

    def test_composer_send_key_follows_the_preference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bridge = ui.SettingsBridge(desktop_core, Path(temporary) / "settings.json")
            window = self._window(bridge)
            composer = window.composer
            sent: list[bool] = []
            composer.sendRequested.connect(lambda: sent.append(True))

            def press(key: int, modifiers: Qt.KeyboardModifier) -> None:
                composer.keyPressEvent(
                    QKeyEvent(QEvent.Type.KeyPress, key, modifiers)
                )

            press(Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
            self.assertEqual(len(sent), 1)
            press(Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
            self.assertEqual(len(sent), 1)

            window.set_preference("enter_sends", False)
            press(Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
            self.assertEqual(len(sent), 1)
            press(Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
            self.assertEqual(len(sent), 2)
            self._close(window)

    def test_startup_preferences_set_mode_and_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bridge = ui.SettingsBridge(desktop_core, Path(temporary) / "settings.json")
            bridge.save({"mode": "chat", "startup_mode": "agent", "show_terminal_on_launch": True})
            window = self._window(bridge)
            window._apply_startup_preferences()
            self.assertEqual(window.mode, "agent")
            self.assertFalse(window.terminal.isHidden())

            bridge.save({"mode": "chat", "startup_mode": "remember"})
            window.settings_data = bridge.load()
            window.preferences = window._load_preferences()
            window.toggle_terminal(False)
            window._apply_startup_preferences()
            self.assertEqual(window.mode, "chat")
            self.assertTrue(window.terminal.isHidden())
            self._close(window)

    def test_settings_dialog_controls_write_preferences(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bridge = ui.SettingsBridge(desktop_core, Path(temporary) / "settings.json")
            window = self._window(bridge)
            dialog = ui.SettingsDialog(
                window.data_root, desktop_core.HARNESS_LIMITATION, parent=window
            )
            dialog.show()
            try:
                dialog.text_size_combo.setCurrentIndex(
                    dialog.text_size_combo.findData("comfortable")
                )
                self.assertEqual(window.preferences["text_scale"], "comfortable")
                self.assertEqual(bridge.load()["text_scale"], "comfortable")

                dialog.enter_sends_box.setChecked(False)
                self.assertFalse(window.composer.enterSends)

                dialog.startup_combo.setCurrentIndex(dialog.startup_combo.findData("agent"))
                self.assertEqual(window.preferences["startup_mode"], "agent")

                dialog.terminal_box.setChecked(True)
                self.assertTrue(bridge.load()["show_terminal_on_launch"])

                dialog.motion_combo.setCurrentIndex(dialog.motion_combo.findData("reduced"))
                self.assertFalse(ui._ui_animations_enabled())
            finally:
                dialog.text_size_combo.setCurrentIndex(
                    dialog.text_size_combo.findData("system")
                )
                dialog.motion_combo.setCurrentIndex(dialog.motion_combo.findData("system"))
                ui.set_motion_override("system")
                ui.apply_text_scale(1.0)
                self._close(dialog)
            self._close(window)


@unittest.skipUnless(QT_AVAILABLE, "PySide6 desktop runtime is not installed")
class QualityOfLifeTests(unittest.TestCase):
    """Rename, filter, drafts, retry, and the unfocused-run nudge."""

    @classmethod
    def setUpClass(cls) -> None:
        QStandardPaths.setTestModeEnabled(True)
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")
        cls.app.setStyleSheet(ui.STYLE_SHEET)

    def _close(self, widget: QWidget) -> None:
        widget.close()
        widget.deleteLater()
        self.app.processEvents()

    def _window(self, bridge: "ui.SettingsBridge") -> "ui.OnionmindWindow":
        window = ui.OnionmindWindow(_CoreStub(), desktop_core, demo=True)
        window.save_current_session = lambda: True
        window.settings_bridge = bridge
        window.settings_data = bridge.load()
        window.preferences = window._load_preferences()
        window.demo = False
        window.show()
        self.app.processEvents()
        return window

    def test_session_filter_hides_non_matching_rows(self) -> None:
        rail = ui.LeftRail()
        rail.set_sessions(
            [
                {"id": "a1", "title": "Parser work", "updated_at": "2026-09-01T10:00:00"},
                {"id": "b2", "title": "Tor notes", "updated_at": "2026-09-02T10:00:00"},
            ]
        )
        self.assertFalse(rail.sessions.item(1).isHidden())
        rail.session_filter.setText("parser")
        self.assertFalse(rail.sessions.item(0).isHidden())
        self.assertTrue(rail.sessions.item(1).isHidden())
        rail.session_filter.setText("")
        self.assertFalse(rail.sessions.item(1).isHidden())
        self._close(rail)

    def test_rename_dialog_emits_the_selected_session_and_clean_title(self) -> None:
        rail = ui.LeftRail()
        rail.set_sessions(
            [{"id": "b2", "title": "Tor notes", "updated_at": "2026-09-02T10:00:00"}]
        )
        rail.sessions.setCurrentRow(0)
        emitted: list[tuple[str, str]] = []
        rail.renameSessionRequested.connect(lambda i, t: emitted.append((i, t)))
        with mock.patch.object(
            ui.QInputDialog, "getText", return_value=("  Renamed session ", True)
        ):
            rail._rename_selected_session()
        self.assertEqual(emitted, [("b2", "Renamed session")])
        self._close(rail)

    def test_window_renames_a_saved_session_in_the_store(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            sessions_bridge = ui.SessionBridge(desktop_core, Path(temporary) / "sessions")
            created = sessions_bridge.store.create(title="Old name", model="inferno")
            window = self._window(ui.SettingsBridge(desktop_core, Path(temporary) / "settings.json"))
            window.session_bridge = sessions_bridge
            window.session_objects = {created.id: created}
            window.current_session = None
            window.rename_session(created.id, "New name")
            self.assertEqual(sessions_bridge.store.load(created.id).title, "New name")
            titles = [
                window.left_rail.sessions.item(row).text()
                for row in range(window.left_rail.sessions.count())
            ]
            self.assertTrue(any("New name" in title for title in titles))
            self._close(window)

    def test_composer_draft_survives_a_restart_and_is_forgotten_when_sent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bridge = ui.SettingsBridge(desktop_core, Path(temporary) / "settings.json")
            first = self._window(bridge)
            first.composer.setPlainText("half-finished thought")
            self._close(first)
            self.assertEqual(bridge.load()["composer_draft"], "half-finished thought")

            second = self._window(bridge)
            second._restore_window_layout()
            self.assertEqual(second.composer.toPlainText(), "half-finished thought")
            second.composer.setPlainText("sent instead")
            second.settings_data["composer_draft"] = "sent instead"
            second._forget_draft()
            self.assertNotIn("composer_draft", bridge.load())
            self._close(second)

    def test_retry_replays_the_last_user_turn_after_a_failure(self) -> None:
        window = self._window(ui.SettingsBridge(desktop_core, Path(tempfile.mkdtemp()) / "s.json"))
        window.chat_messages = [
            {"role": "user", "content": "fix the parser"},
            {"role": "assistant", "content": "Local inference failed: boom"},
        ]
        with mock.patch.object(window, "_start_chat") as start_chat:
            window._retry_last_turn()
            start_chat.assert_called_once_with()
        self.assertEqual(
            window.chat_messages, [{"role": "user", "content": "fix the parser"}]
        )
        self.assertEqual(window.active_kind, "chat")
        self.assertTrue(window.retry_button.isHidden())

        # A failed chat turn offers the retry control; nothing to retry keeps
        # the user informed instead of silently doing nothing.
        other = self._window(ui.SettingsBridge(desktop_core, Path(tempfile.mkdtemp()) / "s.json"))
        other.stream_block = None
        other._chat_failed("boom")
        self.assertTrue(other.retry_button.isVisible())
        other.chat_messages = [{"role": "assistant", "content": "done"}]
        other._retry_last_turn()
        self.assertIn("no failed chat turn", other.status_label.text().lower())
        self._close(other)
        self._close(window)

    def test_finishing_a_run_alerts_an_unfocused_window(self) -> None:
        window = self._window(ui.SettingsBridge(desktop_core, Path(tempfile.mkdtemp()) / "s.json"))
        window._set_active("chat")
        with mock.patch.object(window, "isActiveWindow", return_value=False), \
             mock.patch.object(ui.QApplication, "alert") as alert:
            window._set_active(None)
        alert.assert_called_once_with(window)
        with mock.patch.object(window, "isActiveWindow", return_value=True), \
             mock.patch.object(ui.QApplication, "alert") as alert:
            window._set_active("chat")
            window._set_active(None)
        alert.assert_not_called()
        self._close(window)


@unittest.skipUnless(QT_AVAILABLE, "PySide6 desktop runtime is not installed")
class NativeTitleBarTests(unittest.TestCase):
    def test_windows_frame_is_pinned_dark_with_the_modern_attribute(self) -> None:
        window = mock.Mock()
        window.winId.return_value = 4242
        dwm = mock.Mock()
        dwm.DwmSetWindowAttribute.return_value = 0
        with mock.patch.object(ui.os, "name", "nt"), \
             mock.patch("ctypes.windll", new=mock.Mock(dwmapi=dwm), create=True):
            ui._apply_native_dark_title_bar(window)
        call = dwm.DwmSetWindowAttribute.call_args
        self.assertEqual(call.args[0], 4242)
        self.assertEqual(call.args[1], 20)          # DWMWA_USE_IMMERSIVE_DARK_MODE
        self.assertEqual(call.args[3], 4)

    def test_legacy_attribute_19_is_the_fallback(self) -> None:
        window = mock.Mock()
        window.winId.return_value = 4242
        dwm = mock.Mock()
        dwm.DwmSetWindowAttribute.side_effect = [2147942487, 0]   # E_INVALIDARG, ok
        with mock.patch.object(ui.os, "name", "nt"), \
             mock.patch("ctypes.windll", new=mock.Mock(dwmapi=dwm), create=True):
            ui._apply_native_dark_title_bar(window)
        self.assertEqual(
            [c.args[1] for c in dwm.DwmSetWindowAttribute.call_args_list], [20, 19]
        )

    def test_non_windows_platforms_touch_nothing(self) -> None:
        window = mock.Mock()
        with mock.patch.object(ui.os, "name", "posix"):
            ui._apply_native_dark_title_bar(window)
        window.winId.assert_not_called()

    def test_dwm_failures_stay_silent(self) -> None:
        window = mock.Mock()
        window.winId.side_effect = TypeError("no native handle (offscreen)")
        with mock.patch.object(ui.os, "name", "nt"), \
             mock.patch("ctypes.windll", create=True) as windll:
            ui._apply_native_dark_title_bar(window)     # must not raise
            windll.dwmapi.DwmSetWindowAttribute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
