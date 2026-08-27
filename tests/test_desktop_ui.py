"""Interaction coverage for Onionmind's native desktop controls."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QProcess, QStandardPaths, QTimer
    from PySide6.QtWidgets import (
        QApplication,
        QDialogButtonBox,
        QPushButton,
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

    def test_left_rail_buttons_emit_and_unavailable_actions_disable(self) -> None:
        rail = ui.LeftRail()
        events: list[tuple[str, str]] = []
        rail.newTaskRequested.connect(lambda: events.append(("new", "")))
        rail.openFolderRequested.connect(lambda: events.append(("folder", "")))
        rail.modelsRequested.connect(lambda: events.append(("models", "")))
        rail.settingsRequested.connect(lambda: events.append(("settings", "")))
        rail.exportRequested.connect(lambda: events.append(("export", "")))
        rail.archiveRequested.connect(lambda value: events.append(("archive", value)))

        self.assertFalse(rail.export_button.isEnabled())
        self.assertFalse(rail.archive_button.isEnabled())
        rail.new_button.click()
        rail.folder_button.click()
        rail.add_project_button.click()
        rail.models_button.click()
        rail.settings_button.click()
        rail.set_conversation_available(True)
        rail.export_button.click()
        rail.set_sessions([{"id": "session-1", "title": "Polish UI"}], "session-1")
        rail.archive_button.click()

        self.assertEqual(events.count(("folder", "")), 2)
        self.assertIn(("new", ""), events)
        self.assertIn(("models", ""), events)
        self.assertIn(("settings", ""), events)
        self.assertIn(("export", ""), events)
        self.assertIn(("archive", "session-1"), events)
        self._close(rail)

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

    def test_model_manager_select_add_and_close_controls(self) -> None:
        dialog = ui.ModelManagerDialog(
            ["inferno", "deepseek-r1:8b"],
            "inferno",
            lambda raw: "INFERNO" if "inferno" in raw else "ONIONMIND CUSTOM · 8B",
        )
        requested: list[str] = []
        selected: list[str] = []
        dialog.pullRequested.connect(requested.append)
        dialog.modelSelected.connect(selected.append)
        self.assertEqual(dialog.models.currentRow(), 0)
        use_button = next(
            button
            for button in dialog.findChildren(QPushButton)
            if button.accessibleName() == "Use selected Onionmind model"
        )
        dialog.models.setCurrentRow(1)
        self.assertTrue(use_button.isEnabled())
        use_button.click()
        self.assertEqual(selected, ["deepseek-r1:8b"])
        self.assertFalse(dialog.pull_button.isEnabled())
        self.assertNotIn("deepseek", " ".join(dialog.models.item(i).text() for i in range(dialog.models.count())).lower())

        dialog.model_name.setText("blaze")
        self.assertTrue(dialog.pull_button.isEnabled())
        dialog.pull_button.click()
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
            dialog = ui.SettingsDialog(storage, desktop_core.AGENT_BOUNDARY)
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

    def test_window_actions_modes_and_responsive_toggles(self) -> None:
        window = self._window()
        self.assertFalse(hasattr(window, "model_combo"))
        self.assertEqual(window.onionmind_status.prefix, "Onionmind")
        self.assertEqual(window.onionmind_status.label.text(), "Ready")
        window.set_model_options(
            ["spark:latest", "inferno:latest"], "spark:latest"
        )
        self.assertEqual(window.current_model_id(), "spark:latest")
        self.assertEqual(window.agent_model_id(), "inferno:latest")
        self.assertIn("INFERNO", window.approval_state.text())
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
            window._start_agent = lambda task: (
                agent_tasks.append(task),
                window._set_active(None),
            )
            window.set_mode("agent")
            window.composer.setPlainText("Check every button")
            window.send_button.click()
            self.assertEqual(agent_tasks, ["Check every button"])

            export_path = Path(temporary) / "conversation.md"
            with mock.patch.object(
                ui.QFileDialog,
                "getSaveFileName",
                return_value=(str(export_path), "Markdown (*.md)"),
            ):
                window.left_rail.export_button.click()
            self.assertTrue(export_path.exists())
            self.assertIn("Model: `INFERNO`", export_path.read_text(encoding="utf-8"))

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

    def test_agent_process_is_workspace_scoped_streamed_and_refreshed(self) -> None:
        window = self._window()
        with tempfile.TemporaryDirectory(prefix="onionmind agent ui ") as temporary:
            refreshed: list[bool] = []
            window.workspace = temporary
            window.refresh_workspace = lambda: refreshed.append(True)
            window._set_active("agent")
            window.agent_generation = 1
            window.stream_block = window.transcript.add_message(
                "assistant", "", "Onionmind Agent"
            )
            script = (
                "import os; "
                "print(os.environ.get('ONIONMIND_AGENT_TEST', 'missing'), flush=True)"
            )
            window._agent_prepared(
                1,
                {
                    "available": True,
                    "argv": [sys.executable, "-u", "-c", script],
                    "cwd": temporary,
                    "environment": {"ONIONMIND_AGENT_TEST": "workspace-only"},
                    "unset_environment": ["HTTPS_PROXY", "HTTP_PROXY"],
                },
            )
            self.assertTrue(
                self._wait_until(lambda: window.active_kind is None, timeout_ms=8000)
            )
            self.assertIn("workspace-only", window.terminal.output.toPlainText())
            self.assertEqual(refreshed, [True])
        self._close(window)

    def test_visible_copy_and_accessible_controls_use_onionmind_language(self) -> None:
        window = self._window()
        branded_error = ui._brand_runtime_text(
            "Qwen Code and DeepSeek Harness via Ollama, llama.cpp, DSH, and Node.js"
        ).lower()
        for term in ("qwen", "ollama", "deepseek", "harness", "dsh", "llama.cpp", "node.js"):
            self.assertNotIn(term, branded_error)
        self.assertEqual(ui._brand_runtime_text("test harness"), "test harness")
        window.set_model_options(["deepseek-r1:8b", "inferno:latest"], "deepseek-r1:8b")
        self.assertEqual(window.current_model_id(), "deepseek-r1:8b")
        forbidden = ("qwen", "ollama", "deepseek", "harness", " dsh", "llama.cpp", "node.js")
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
        copy = "\n".join(visible).lower()
        for term in forbidden:
            self.assertNotIn(term, copy)
        self.assertNotIn("onionmind custom · 8b", copy)
        buttons = [
            *window.findChildren(QPushButton),
            *window.findChildren(QToolButton),
        ]
        for button in buttons:
            if button.parentWidget() is not None and button.parentWidget().__class__.__name__ == "QTabBar":
                continue
            self.assertTrue(button.text() or button.accessibleName())
        self._close(window)


if __name__ == "__main__":
    unittest.main()
