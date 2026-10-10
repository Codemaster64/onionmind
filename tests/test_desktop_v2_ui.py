"""The v2 workbench features: each one off (nothing new shows) and on (it works).

Every feature sits behind one flag in window.features. These tests build the
real window over the real desktop core, with the inference core stubbed the
way tests/test_desktop_ui.py stubs it, and drive the controls a person would.

Run: python tests/test_desktop_v2_ui.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QProcess, QStandardPaths, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication, QCheckBox, QPushButton, QWidget

    import onionmind_desktop as ui
    import onionmind_desktop_core as desktop_core

    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False


class _CoreStub:
    MODEL = "inferno"
    BACKEND = "onionmind"


@unittest.skipUnless(QT_AVAILABLE, "PySide6 desktop runtime is not installed")
class V2Base(unittest.TestCase):
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

    def _window(self, core=None, **features) -> "ui.OnionmindWindow":
        window = ui.OnionmindWindow(core or _CoreStub(), desktop_core, demo=True)
        window.save_current_session = lambda: True
        for key, on in features.items():
            window.set_feature(key, on)
        window.show()
        self.app.processEvents()
        return window

    def _close(self, widget: QWidget) -> None:
        widget.close()
        widget.deleteLater()
        self.app.processEvents()

    def _settings(self, window) -> "ui.SettingsDialog":
        dialog = ui.SettingsDialog(window.data_root, "limitation", None, window)
        self.addCleanup(lambda: (dialog.done(0), dialog.deleteLater()))
        return dialog

    @staticmethod
    def _tab_labels(dialog) -> list[str]:
        return [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())]


class FeatureStoreTests(V2Base):
    def test_defaults_leave_the_original_workbench_and_only_web_ask_on(self) -> None:
        window = self._window()
        try:
            self.assertEqual({k for k, on in window.features.items() if on}, {"webAsk"})
            self.assertEqual(len(window.features), 24)
            self.assertFalse(window.features_button.isHidden())
            dialog = self._settings(window)
            self.assertEqual(self._tab_labels(dialog),
                             ["General", "Privacy", "Updates", "Storage", "Features"])
        finally:
            self._close(window)

    def test_popover_lists_every_feature_and_turns_them_all_on_and_off(self) -> None:
        window = self._window()
        try:
            window.features_button.click()
            popover = window._features_popover
            self.assertTrue(self._wait_until(popover.isVisible))
            self.assertEqual(len(popover.toggles.boxes), 24)
            popover.toggles.all_on.click()
            self.assertTrue(all(window.features.values()))
            self.assertEqual(popover.toggles.count_label.text(), "24 of 24 on")
            self.assertTrue(popover.toggles.run_setup.isVisibleTo(popover))
            popover.toggles.all_off.click()
            self.assertFalse(any(window.features.values()))
            popover.toggles.boxes["palette"].click()
            self.assertTrue(window.features["palette"])
            popover.close()
        finally:
            self._close(window)

    def test_settings_tab_and_popover_drive_the_same_flags(self) -> None:
        window = self._window()
        try:
            dialog = self._settings(window)
            dialog.feature_toggles.boxes["zones"].click()
            self.assertTrue(window.features["zones"])
            window.set_feature("zones", False)
            self.assertFalse(dialog.feature_toggles.boxes["zones"].isChecked())
        finally:
            self._close(window)

    def test_flags_are_saved_between_launches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bridge = ui.SettingsBridge(desktop_core, Path(tmp))
            window = self._window()
            try:
                window.demo = False
                window.settings_bridge = bridge
                window.set_feature("palette", True)
                window.set_feature("webAsk", False)
            finally:
                window.demo = True
                self._close(window)
            stored = bridge.load()["features"]
            self.assertTrue(stored["palette"])
            self.assertFalse(stored["webAsk"])
            self.assertEqual(desktop_core.load_features(bridge.load())["palette"], True)

    def test_without_the_desktop_core_there_are_no_v2_features(self) -> None:
        window = ui.OnionmindWindow(_CoreStub(), None, demo=True)
        try:
            self.assertEqual(window.features, {})
            self.assertTrue(window.features_button.isHidden())
        finally:
            self._close(window)


FIXTURE_LINES = (Path(__file__).resolve().parent / "qwen-stream-sample.jsonl").read_text(
    encoding="utf-8").splitlines()


class _FakeProcess:
    """Stands in for the agent QProcess: records what the window writes to it."""

    def __init__(self) -> None:
        self.written: list[dict] = []
        self.closed = False
        self.terminated = False

    def write(self, data: bytes) -> int:
        for line in data.decode("utf-8").splitlines():
            if line.strip():
                self.written.append(json.loads(line))
        return len(data)

    def state(self):
        return QProcess.ProcessState.Running

    def closeWriteChannel(self) -> None:
        self.closed = True

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.terminated = True


def _cards(window, title_part: str) -> list:
    return [card for card in window.transcript.content.findChildren(ui.NoticeCard)
            if title_part in card.title_label.text() and card.isVisibleTo(window)]


def _button(widget, text: str) -> QPushButton:
    return next(b for b in widget.findChildren(QPushButton) if b.text() == text)


class WebAskTests(V2Base):
    PROMPT = "Is there a newer minitest release?"

    def _chat_window(self, **features):
        window = self._window(**features)
        window.set_mode("chat")
        started: list[bool] = []
        window._start_chat = lambda allow_search=False: started.append(allow_search)
        return window, started

    def test_off_sends_straight_to_the_model_with_no_card(self) -> None:
        window, started = self._chat_window(webAsk=False)
        try:
            window.composer.setPlainText(self.PROMPT)
            window.submit()
            self.assertEqual(started, [False])
            self.app.processEvents()
            self.assertEqual(_cards(window, "search the web"), [])
        finally:
            self._close(window)

    def test_on_pauses_shows_the_query_and_answers_locally(self) -> None:
        window, started = self._chat_window()
        try:
            window.tor_phase = "running"
            window.composer.setPlainText(self.PROMPT)
            window.submit()
            self.assertEqual(started, [])
            self.assertTrue(self._wait_until(lambda: _cards(window, "search the web")))
            card = _cards(window, "search the web")[0]
            labels = [l.text() for l in card.findChildren(ui.QLabel)]
            self.assertIn("a newer minitest release", labels)
            self.assertIn("Search once over Tor", [b.text() for b in card.findChildren(QPushButton)])
            _button(card, "Answer without searching").click()
            self.assertEqual(started, [False])
            self.assertEqual(window.status_label.text(), "Answering from the model alone, without the web.")
        finally:
            self._close(window)

    def test_tor_off_variant_turns_tor_on_and_searches_once(self) -> None:
        enabled: list[bool] = []
        core = SimpleNamespace(MODEL="inferno", BACKEND="onionmind",
                               tor_enabled=lambda: bool(enabled and enabled[-1]),
                               set_tor_enabled=enabled.append)
        window = self._window(core=core)
        window.set_mode("chat")
        started: list[bool] = []
        window._start_chat = lambda allow_search=False: started.append(allow_search)
        try:
            window.tor_phase = "off"
            window.composer.setPlainText("What is the latest PySide6 version?")
            window.submit()
            self.assertTrue(self._wait_until(lambda: _cards(window, "search the web")))
            card = _cards(window, "search the web")[0]
            _button(card, "Turn on Tor and search once").click()
            self.assertEqual(enabled, [True])
            self.assertEqual(started, [True])
        finally:
            self._close(window)

    def test_stop_while_asking_sends_nothing(self) -> None:
        window, started = self._chat_window()
        try:
            window.composer.setPlainText(self.PROMPT)
            window.submit()
            self.assertTrue(self._wait_until(lambda: _cards(window, "search the web")))
            window.stop_active()
            self.assertIsNone(window.active_kind)
            self.assertEqual(started, [])
            self.app.processEvents()
            self.assertEqual(_cards(window, "search the web"), [])
        finally:
            self._close(window)

    def test_ordinary_prompts_are_never_held_back(self) -> None:
        window, started = self._chat_window()
        try:
            window.composer.setPlainText("Explain how this project is structured")
            window.submit()
            self.assertEqual(started, [False])
        finally:
            self._close(window)


class AgentStreamBase(V2Base):
    def _agent_window(self, **features):
        window = self._window(**features)
        window.workspace = "/work/demo"
        window.active_kind = "agent"
        window._agent_stream = True
        window._agent_rules = None
        window._agent_outcome = None
        window._run_allow_all = False
        process = _FakeProcess()
        window.harness_process = process
        window.stream_block = window.transcript.add_message("assistant", "", "Onionmind Agent")
        return window, process

    def _feed(self, window, lines) -> None:
        for line in lines:
            window._handle_agent_line(line)
        self.app.processEvents()


class ApprovalTests(AgentStreamBase):
    def test_off_keeps_the_plain_agent_and_its_stop_safely_contract(self) -> None:
        window = self._window()
        try:
            self.assertFalse(window._uses_agent_stream())
            self.assertEqual(window.approval_state.text(), "Protected actions stop safely")
            core = SimpleNamespace(__file__="/x/onionmind.py", run_agent=lambda *a, **k: 0)
            argv = ui.HarnessBridge(desktop_core, core)._launcher("m", "the task", "/w")
            self.assertEqual(argv[-1], "the task")
            self.assertNotIn("--stream-json", argv)
        finally:
            self._close(window)

    def test_on_pauses_for_allow_once_and_writes_the_answer_to_the_agent(self) -> None:
        window, process = self._agent_window(approvals=True)
        try:
            self.assertEqual(window.approval_state.text(), "Asks before protected actions")
            core = SimpleNamespace(__file__="/x/onionmind.py", run_agent=lambda *a, **k: 0)
            argv = ui.HarnessBridge(desktop_core, core)._launcher("m", "secret task", "/w", stream=True)
            self.assertIn("--stream-json", argv)
            self.assertNotIn("secret task", argv)
            self._feed(window, FIXTURE_LINES[:4])      # banner, init, tool_use, permission
            cards = _cards(window, "wants to edit a file")
            self.assertEqual(len(cards), 1)
            self.assertIn("hello.txt · +1 / −0 · inside demo", cards[0].body_label.text())
            self.assertEqual(process.written, [])         # nothing answered yet: it waits
            _button(cards[0], "Allow once").click()
            answer = process.written[-1]["response"]
            self.assertEqual(answer["response"]["behavior"], "allow")
            self.assertEqual(answer["request_id"], json.loads(FIXTURE_LINES[3])["request_id"])
            self.app.processEvents()
            self.assertEqual(_cards(window, "wants to edit a file"), [])
            self._feed(window, FIXTURE_LINES[4:])
            self.assertTrue(process.closed)                # result: stdin closed so qwen exits
            self.assertIn("Done.", window.stream_block.text)
        finally:
            self._close(window)

    def test_deny_refuses_and_interrupts_the_run(self) -> None:
        window, process = self._agent_window(approvals=True)
        try:
            self._feed(window, FIXTURE_LINES[:4])
            _button(_cards(window, "wants to edit a file")[0], "Deny").click()
            self.assertEqual(process.written[0]["response"]["response"]["behavior"], "deny")
            self.assertEqual(process.written[1]["request"]["subtype"], "interrupt")
            self.assertTrue(window._agent_outcome.startswith("denied:edit hello.txt"))
        finally:
            self._close(window)

    def test_allow_for_this_run_answers_later_requests_itself(self) -> None:
        window, process = self._agent_window(approvals=True)
        try:
            self._feed(window, FIXTURE_LINES[:4])
            _button(_cards(window, "wants to edit a file")[0], "Allow for this run").click()
            again = json.loads(FIXTURE_LINES[3])
            again["request_id"] = "second"
            self._feed(window, [json.dumps(again)])
            self.assertEqual(_cards(window, "wants to edit a file"), [])
            self.assertEqual(process.written[-1]["response"]["request_id"], "second")
            self.assertEqual(process.written[-1]["response"]["response"]["behavior"], "allow")
        finally:
            self._close(window)

    def test_stream_without_approvals_still_stops_safely(self) -> None:
        window, process = self._agent_window(runPanel=True)
        try:
            self._feed(window, FIXTURE_LINES[:4])
            self.assertEqual(_cards(window, "wants to edit a file"), [])
            self.assertEqual(process.written[0]["response"]["response"]["behavior"], "deny")
            self.assertEqual(process.written[1]["request"]["subtype"], "interrupt")
            self.assertTrue(window._agent_outcome.startswith("halted:"))
        finally:
            self._close(window)


class RunPanelTests(AgentStreamBase):
    def test_off_leaves_the_three_original_tabs(self) -> None:
        window = self._window()
        try:
            tabs = window.inspector.tabs
            self.assertEqual([tabs.tabText(i) for i in range(tabs.count())], ["Context", "Changes", "Activity"])
        finally:
            self._close(window)

    def test_on_adds_run_with_real_steps_todos_status_and_stop(self) -> None:
        window, process = self._agent_window(runPanel=True, approvals=True)
        try:
            tabs = window.inspector.tabs
            self.assertEqual(tabs.tabText(tabs.count() - 1), "Run")
            panel = window.inspector.run_panel
            panel.start(window._run_meta())
            self.assertIn("Tor-only network · asks before edits", panel.meta_label.text())
            self._feed(window, FIXTURE_LINES[:3])
            self.assertIn("edit hello.txt", panel.plan.item(0).text())
            self.assertIn("Working…", panel.plan.item(0).text())
            self._feed(window, [FIXTURE_LINES[3]])
            self.assertEqual(panel.status_label.text(), "Waiting for you")
            self.assertEqual(panel.approval_holder.count(), 1)
            todo = json.dumps({"type": "assistant", "message": {"content": [{
                "type": "tool_use", "id": "t1", "name": "todo_write", "input": {"todos": [
                    {"content": "Read the parser", "status": "completed"},
                    {"content": "Fix the bug", "status": "in_progress"}]}}]}})
            self._feed(window, [todo])
            texts = [panel.plan.item(i).text() for i in range(panel.plan.count())]
            self.assertTrue(any("Read the parser" in t and "Done" in t for t in texts), texts)
            self.assertTrue(any("Fix the bug" in t for t in texts), texts)
            panel.stop_button.click()       # wired to stop_active at build time
            self.assertTrue(process.terminated)
            self.assertEqual(window._agent_outcome, "stopped")
            window._finish_run_panel(False)
            self.assertEqual(panel.status_label.text(), "Stopped")
            self.assertFalse(panel.stop_button.isEnabled())
        finally:
            self._close(window)

    def test_turning_it_off_removes_the_tab_at_once(self) -> None:
        window = self._window(runPanel=True)
        try:
            window.set_feature("runPanel", False)
            tabs = window.inspector.tabs
            self.assertEqual(tabs.count(), 3)
        finally:
            self._close(window)


class TimelineTests(V2Base):
    def test_off_tool_activity_stays_as_cards(self) -> None:
        window = self._window()
        try:
            before = len(window.transcript._tool_cards)
            window._chat_event({"kind": "tool_started", "name": "web_search", "arguments": {"query": "q"}})
            window._chat_event({"kind": "tool_finished", "name": "web_search", "results": 3})
            self.assertEqual(len(window.transcript._tool_cards), before + 2)
            self.assertEqual(window.transcript.content.findChildren(ui.TimelineView), [])
        finally:
            self._close(window)

    def test_on_steps_join_one_timeline_and_settle(self) -> None:
        window = self._window(timeline=True)
        try:
            before = len(window.transcript._tool_cards)
            window._chat_event({"kind": "tool_started", "name": "web_search", "arguments": {"query": "q"}})
            timeline = window.transcript.content.findChildren(ui.TimelineView)[-1]
            self.assertEqual(timeline.steps[0].state, "running")
            window._chat_event({"kind": "tool_finished", "name": "web_search", "results": 3})
            self.assertEqual(timeline.steps[0].state, "done")
            self.assertEqual(timeline.steps[0].detail_label.text(), "3 results over Tor")
            self.assertEqual(len(window.transcript._tool_cards), before)
        finally:
            self._close(window)


if __name__ == "__main__":
    unittest.main()
