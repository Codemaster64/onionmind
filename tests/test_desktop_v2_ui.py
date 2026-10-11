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
        # Let background jobs (a git inspection, say) finish first: on Windows
        # a child still running in a temp folder keeps it from being removed.
        self._wait_until(lambda: not getattr(widget, "_workers", None))
        widget.close()
        widget.deleteLater()
        self.app.processEvents()
        # Really destroy it: a window left alive is re-polished by every later
        # application style sheet change and slows each following test.
        from PySide6.QtCore import QCoreApplication, QEvent
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
        self.app.processEvents()

    def _settings(self, window) -> "ui.SettingsDialog":
        dialog = ui.SettingsDialog(window.data_root, "limitation", None, window)

        def close() -> None:
            try:
                dialog.done(0)
                dialog.deleteLater()
            except RuntimeError:
                pass                             # already gone with its window

        self.addCleanup(close)
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


class _PowerCore:
    """An engine stub with real-shaped model memory calls that record what ran."""

    MODEL = "inferno"
    BACKEND = "ollama"
    NUM_CTX = 16384

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.loaded = [{"name": "inferno", "size": 17 * 1024 ** 3, "size_vram": 11 * 1024 ** 3}]

    def installed_model_details(self):
        return [{"name": "inferno", "size": 17 * 1024 ** 3}, {"name": "blaze", "size": 6 * 1024 ** 3},
                {"name": "ember", "size": 3 * 1024 ** 3}]

    def loaded_models(self):
        return list(self.loaded)

    def unload_model(self, name):
        self.calls.append(("unload", name))
        self.loaded = [m for m in self.loaded if m["name"] != name]

    def load_model(self, name, keep_alive="30m"):
        time.sleep(0.3)
        self.calls.append(("load", name))
        self.loaded = [{"name": name, "size": 6 * 1024 ** 3, "size_vram": 6 * 1024 ** 3}]
        return True


class LadderTests(V2Base):
    def _items(self, window) -> list[tuple[str, object]]:
        combo = window.model_combo
        return [(combo.itemText(i), combo.itemData(i)) for i in range(combo.count())]

    def test_off_the_model_menu_is_unchanged(self) -> None:
        window = self._window()
        try:
            self.assertTrue(all(data is not None for _text, data in self._items(window)))
            self.assertFalse(any("Fits" in text or "This machine" in text for text, _ in self._items(window)))
        finally:
            self._close(window)

    def test_on_shows_this_machine_fit_labels_and_loads_the_picked_tier(self) -> None:
        core = _PowerCore()
        with mock.patch.object(desktop_core, "gpu_memory_mb", return_value=(11_000, 12_288)), \
             mock.patch.object(desktop_core, "gpu_name", return_value="Test GPU"):
            window = self._window(core=core, ladder=True)
            try:
                self.assertTrue(self._wait_until(lambda: window._model_power.get("vram_total")))
                items = self._items(window)
                self.assertTrue(items[0][0].startswith("This machine: 12 GB GPU (Test GPU)"))
                self.assertIsNone(items[0][1])
                self.assertFalse(window.model_combo.model().item(0).isEnabled())
                labels = dict((data, text) for text, data in items if data)
                self.assertTrue(labels["inferno"].endswith("In use"))
                self.assertTrue(labels["blaze"].endswith("Fits"))
                window.model_combo.setCurrentIndex(window.model_combo.findData("blaze"))
                window._model_picked()
                self.assertEqual(window._model_loading, "blaze")
                self.assertFalse(window.model_loading.isHidden())
                self.assertFalse(window.model_combo.isEnabled())
                window.composer.setPlainText("hello")
                self.assertFalse(window.send_button.isEnabled())
                window.submit()
                self.assertIn("still loading", window.status_label.text())
                self.assertTrue(self._wait_until(lambda: window._model_loading is None, 5000))
                self.assertEqual(core.calls, [("unload", "inferno"), ("load", "blaze")])
                self.assertIn("is loaded and ready", window.status_label.text())
                self.assertTrue(window.model_loading.isHidden())
                self.assertTrue(window.send_button.isEnabled())
            finally:
                self._close(window)


class ModelPlusTests(V2Base):
    def test_off_no_live_stats_and_one_model_for_both_modes(self) -> None:
        window = self._window()
        try:
            self.assertTrue(window.speed_status.isHidden())
            window.set_mode("chat")
            window.model_combo.setCurrentIndex(window.model_combo.findData("blaze"))
            window.set_mode("agent")
            self.assertEqual(window.current_model_id(), "blaze")
        finally:
            self._close(window)

    def test_on_each_mode_remembers_its_tier_and_stats_are_real(self) -> None:
        core = _PowerCore()
        core.load_model = lambda name, keep_alive="30m": True
        with mock.patch.object(desktop_core, "gpu_memory_mb", return_value=(4_096, 12_288)), \
             mock.patch.object(desktop_core, "gpu_name", return_value=None):
            window = self._window(core=core, modelPlus=True)
            try:
                self.assertFalse(window.speed_status.isHidden())
                self.assertTrue(self._wait_until(lambda: window.vram_status.text() == "VRAM 4.0 / 12 GB"))
                window.set_mode("chat")
                window.model_combo.setCurrentIndex(window.model_combo.findData("ember"))
                window.set_mode("agent")
                window.model_combo.setCurrentIndex(window.model_combo.findData("inferno"))
                window.set_mode("chat")
                self.assertEqual(window.current_model_id(), "ember")
                self.assertTrue(window.status_label.text().startswith("Chat uses"))
                headers = [window.model_combo.itemText(i) for i in range(window.model_combo.count())
                           if window.model_combo.itemData(i) is None]
                self.assertTrue(any("Chat uses EMBER · Agent uses INFERNO" in h for h in headers), headers)
                window.active_kind = "chat"
                window._chat_event({"kind": "generating", "chunks": 40, "seconds": 2.0, "reasoning": False})
                self.assertEqual(window.speed_status.text(), "20 tok/s")
                window._chat_event({"kind": "stats", "tokens_per_second": 18.4,
                                    "prompt_eval_count": 3000, "eval_count": 100})
                self.assertEqual(window.speed_status.text(), "18 tok/s")
                self.assertTrue(window.ctx_status.text().startswith("ctx 3.0k / 16k"), window.ctx_status.text())
                window.active_kind = None
                window._update_live_status()
                self.assertEqual(window.speed_status.text(), "Idle")
            finally:
                window.active_kind = None
                self._close(window)


class ThinkingTests(V2Base):
    def _pending(self, window):
        block = window.transcript.add_message("assistant", "")
        block.start_thinking("Thinking")
        window.stream_block = block
        window.active_kind = "chat"
        return block

    def test_off_labels_stay_and_no_thought_line(self) -> None:
        window = self._window()
        try:
            block = self._pending(window)
            window._chat_event({"kind": "generating", "chunks": 5, "seconds": 1.0, "reasoning": False})
            self.assertEqual(block.thinking.label.text(), "Thinking")
            window._chat_event({"kind": "stats", "reasoning_seconds": 4.2})
            window._chat_complete({"answer": "Hi", "history": [], "stopped": False})
            self.assertEqual(window.transcript.content.findChildren(ui.ThoughtLine), [])
        finally:
            window.active_kind = None
            self._close(window)

    def test_on_filter_phase_and_expandable_thought_for(self) -> None:
        window = self._window(thinking=True)
        try:
            block = self._pending(window)
            window._chat_event({"kind": "generating", "chunks": 5, "seconds": 1.0, "reasoning": True})
            self.assertEqual(block.thinking.label.text(), "Thinking")
            window._chat_event({"kind": "generating", "chunks": 9, "seconds": 2.0, "reasoning": False})
            self.assertEqual(block.thinking.label.text(), "Filtering private reasoning")
            window._chat_event({"kind": "stats", "reasoning_seconds": 3.0})
            window._chat_event({"kind": "stats", "reasoning_seconds": 1.2})
            window._chat_complete({"answer": "Hi", "history": [], "stopped": False})
            lines = window.transcript.content.findChildren(ui.ThoughtLine)
            self.assertEqual(len(lines), 1)
            self.assertEqual(lines[0].toggle.text(), "Thought for 4s")
            self.assertTrue(lines[0].detail.isHidden())
            lines[0].toggle.click()
            self.assertFalse(lines[0].detail.isHidden())
            self.assertNotIn("private plan", lines[0].detail.text())
        finally:
            window.active_kind = None
            self._close(window)


class ContextMeterTests(V2Base):
    def test_off_nothing_in_the_composer_row(self) -> None:
        window = self._window()
        try:
            self.assertTrue(window.context_box.isHidden())
            self.assertTrue(window.context_warning.isHidden())
        finally:
            self._close(window)

    def test_on_rises_with_files_and_warns_above_eighty_percent(self) -> None:
        window = self._window(contextMeter=True)
        try:
            window.set_mode("chat")
            window.chat_messages = []
            window._context_used_real = None
            self.assertFalse(window.context_box.isHidden())
            window.composer.setPlainText("x" * 400)
            self.assertEqual(window.context_meter_label.text(), "100 / 16k")
            with tempfile.TemporaryDirectory() as tmp:
                (Path(tmp) / "big.txt").write_text("y" * 40_000, encoding="utf-8")
                window.workspace = tmp
                window.inspector.populate_tree(["big.txt"])
                item = window.inspector.file_tree.topLevelItem(0)
                item.setCheckState(0, Qt.CheckState.Checked)
                window._update_context_meter()
                self.assertEqual(window.context_meter_label.text(), "9.9k / 16k")
                self.assertTrue(window.context_warning.isHidden())
                window.composer.setPlainText("x" * 20_000)
                self.assertFalse(window.context_warning.isHidden())
                self.assertEqual(window.context_meter.property("level"), "warn")
                self.assertIn("of the context", window.context_warning_label.text())
                window.composer.setPlainText("x" * 40_000)
                self.assertIn("over the model’s context", window.context_warning_label.text())
                window.workspace = None
        finally:
            self._close(window)


class PaletteTests(V2Base):
    def test_off_no_button_and_ctrl_k_does_nothing(self) -> None:
        window = self._window()
        try:
            window.resize(1440, 900)
            self.app.processEvents()
            self.assertTrue(window.palette_button.isHidden())
            window.open_palette()
            self.assertFalse(hasattr(window, "_palette"))
        finally:
            self._close(window)

    def test_on_four_groups_keys_choose_and_focus_returns(self) -> None:
        window = self._window(palette=True)
        try:
            window.resize(1440, 900)
            self.app.processEvents()
            self.assertFalse(window.palette_button.isHidden())
            window.resize(900, 800)
            self.app.processEvents()
            self.assertTrue(window.palette_button.isHidden())    # below 1000px
            window.composer.setFocus()
            self.app.processEvents()
            window.open_palette()
            palette = window._palette
            self.assertTrue(self._wait_until(palette.isVisible))
            headers = [palette.list.item(i).text() for i in range(palette.list.count())
                       if not palette.list.item(i).flags() & Qt.ItemFlag.ItemIsEnabled]
            self.assertEqual(headers, ["PROJECTS", "SESSIONS", "MODEL POWER", "COMMANDS"])
            palette.search.setText("zzzz-no-match")
            self.assertFalse(palette.empty.isHidden())
            palette.search.setText("new task")
            self.assertTrue(palette.list.currentItem().text().startswith("New task"))
            called: list[bool] = []
            window.new_task = lambda save_current=True: called.append(True)
            QApplication.sendEvent(palette.search, QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return,
                                                             Qt.KeyboardModifier.NoModifier))
            self.assertTrue(self._wait_until(lambda: called))
            self.assertTrue(self._wait_until(lambda: window.focusWidget() is window.composer))
        finally:
            self._close(window)

    def test_escape_closes_the_palette_without_stopping_anything(self) -> None:
        window = self._window(palette=True)
        try:
            stops: list[bool] = []
            window.stop_active = lambda: stops.append(True)
            window.open_palette()
            palette = window._palette
            self.assertTrue(self._wait_until(palette.isVisible))
            palette.search.setText("mod")
            QApplication.sendEvent(palette.search, QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Down,
                                                             Qt.KeyboardModifier.NoModifier))
            QApplication.sendEvent(palette.search, QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape,
                                                             Qt.KeyboardModifier.NoModifier))
            self.assertTrue(self._wait_until(lambda: not palette.isVisible()))
            self.assertEqual(stops, [])
        finally:
            self._close(window)


class KeyboardTests(V2Base):
    def test_off_no_keyboard_tab_and_question_mark_is_inert(self) -> None:
        window = self._window()
        try:
            self.assertNotIn("Keyboard", self._tab_labels(self._settings(window)))
            opened: list[str] = []
            window.open_settings_tab = opened.append
            window.setFocus()
            window._question_shortcut()
            self.assertEqual(opened, [])
        finally:
            self._close(window)

    def test_on_tab_lists_real_shortcuts_and_question_opens_it(self) -> None:
        window = self._window(keyboard=True)
        try:
            dialog = self._settings(window)
            self.assertIn("Keyboard", self._tab_labels(dialog))
            self.assertNotIn("Command palette", dialog.keyboard_rows)   # palette is off
            self.assertIn("New task", dialog.keyboard_rows)
            window.set_feature("palette", True)
            window.set_feature("keyboard", False)
            window.set_feature("keyboard", True)
            self.assertIn("Command palette", dialog.keyboard_rows)
            opened: list[str] = []
            window.open_settings_tab = opened.append
            window.composer.setFocus()
            self.app.processEvents()
            window._question_shortcut()
            self.assertEqual(opened, [])                 # typing a "?" in the composer
            window.inspector.activity.setFocus()
            self.app.processEvents()
            window._question_shortcut()
            self.assertEqual(opened, ["Keyboard"])
            self.assertTrue(ui._SYSTEM_MOTION_PROBE)
        finally:
            ui.set_system_motion_probe(False)
            self._close(window)


class BoundaryTests(V2Base):
    def _counted(self, window, tmp):
        window.egress_path = Path(tmp) / "net.jsonl"
        window._egress_counts = None

    def test_off_no_map_and_nothing_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            window = self._window()
            try:
                self._counted(window, tmp)
                self.assertTrue(window.boundary_button.isHidden())
                window.toggle_boundary(True)
                self.assertTrue(window.boundary_panel.isHidden())
                window._chat_event({"kind": "tool_finished", "name": "web_search", "results": 2})
                self.assertFalse(window.egress_path.exists())
            finally:
                self._close(window)

    def test_on_counts_real_searches_lights_the_path_and_fills_the_panel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            window = self._window(boundary=True)
            try:
                self._counted(window, tmp)
                window.resize(1440, 900)
                self.app.processEvents()
                self.assertFalse(window.boundary_button.isHidden())
                window._chat_event({"kind": "tool_started", "name": "web_search", "arguments": {"query": "q"}})
                self.assertTrue(window.boundary_button._searching)
                window._chat_event({"kind": "tool_finished", "name": "web_search", "results": 2})
                self.assertFalse(window.boundary_button._searching)
                self.assertEqual(window.boundary_button._label, "1 search today")
                self.assertTrue(window.boundary_button._open)          # demo Tor is verified
                window.toggle_boundary()
                panel = window.boundary_panel
                self.assertFalse(panel.isHidden())
                self.assertIn("Today: 1 search query left over Tor · 0 direct downloads", panel.receipt.text())
                self.assertEqual(panel.node_state.text(), "Circuit verified")
                self.assertEqual(panel.machine_rows["Disk"].text(), "4 observed changes")
                self.assertEqual(panel.outside_rows["Downloads"].text(), "Direct, only after you confirm")
                entries = desktop_core.EgressLog(window.egress_path).entries()
                self.assertEqual([e["what"] for e in entries], ["Chat search · DuckDuckGo onion service"])
                self.assertNotIn("q", json.dumps(entries).replace("query", ""))
                window.resize(1000, 800)
                self.app.processEvents()
                self.assertFalse(window.boundary_button._show_label)  # label only at 1200px+
                window.resize(840, 800)
                self.app.processEvents()
                self.assertTrue(window.boundary_button.isHidden())     # button only at 860px+
                window.set_feature("boundary", False)
                self.assertTrue(panel.isHidden())
            finally:
                self._close(window)


class ZonesTests(V2Base):
    def test_off_activity_is_one_list(self) -> None:
        window = self._window()
        try:
            self.assertFalse(window.inspector.activity.isHidden())
            self.assertTrue(window.inspector.lanes.isHidden())
        finally:
            self._close(window)

    def test_on_events_land_in_their_lanes(self) -> None:
        window = self._window(zones=True)
        try:
            inspector = window.inspector
            self.assertTrue(inspector.activity.isHidden())
            self.assertFalse(inspector.lanes.isHidden())
            before = {zone: lane.count() for zone, lane in inspector.lane_lists.items()}
            inspector.append_activity("Project refreshed; 0 observed change(s)")
            inspector.append_activity("Background Tor path verified")
            inspector.append_activity("Chat search sent", "outside")
            after = {zone: lane.count() for zone, lane in inspector.lane_lists.items()}
            self.assertEqual({z: after[z] - before[z] for z in after}, {"machine": 1, "tor": 1, "outside": 1})
            self.assertTrue(inspector.lane_titles["outside"].text().startswith("OUTSIDE · "))
        finally:
            self._close(window)


class ProvenanceTests(V2Base):
    def _reply(self, window, searches=()):
        window.active_kind = "chat"
        window._turn_searches = list(searches)
        window.stream_block = window.transcript.add_message("assistant", "")
        window._chat_complete({"answer": "Hi", "history": [], "stopped": False})

    def test_off_no_chips(self) -> None:
        window = self._window()
        try:
            self._reply(window)
            self.assertEqual(window.transcript.content.findChildren(ui.ProvenanceChips), [])
        finally:
            self._close(window)

    def test_on_machine_chip_and_real_search_chip(self) -> None:
        window = self._window(provenance=True)
        try:
            self._reply(window)
            chips = window.transcript.content.findChildren(ui.ProvenanceChips)[-1]
            self.assertEqual([l.text() for l in chips.findChildren(ui.QLabel)],
                             ["Answered on this machine · INFERNO"])
            window._start_chat = lambda allow_search=False: None
            self._reply(window, [{"results": 3, "domains": ["rubygems.org", "github.com"]}])
            chips = window.transcript.content.findChildren(ui.ProvenanceChips)[-1]
            labels = chips.findChildren(ui.QLabel)
            self.assertEqual(labels[1].text(), "1 Tor search · 3 results")
            self.assertIn("github.com", labels[1].toolTip())
        finally:
            self._close(window)


class NetlogTests(V2Base):
    def test_off_no_export_button_and_nothing_logged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            window = self._window()
            try:
                window.egress_path = Path(tmp) / "net.jsonl"
                self.assertTrue(window.inspector.netlog_button.isHidden())
                window._egress("Tor", "Update check · github.com")
                self.assertFalse(window.egress_path.exists())
            finally:
                self._close(window)

    def test_on_table_and_exports_hold_no_prompt_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            core = SimpleNamespace(MODEL="inferno", BACKEND="onionmind", NET_LOG=str(Path(tmp) / "agent-net.log"))
            today = time.strftime("%Y-%m-%d")
            Path(core.NET_LOG).write_text(f"{today} 10:00:00 tor pypi.org:443\n", encoding="utf-8")
            window = self._window(core=core, netlog=True)
            try:
                window.egress_path = Path(tmp) / "net.jsonl"
                self.assertFalse(window.inspector.netlog_button.isHidden())
                window.composer.setPlainText("my secret prompt")
                window._chat_event({"kind": "tool_finished", "name": "web_search", "results": 1})
                window._egress("Direct", "Model download · registry.ollama.ai")
                rows = window.network_log_rows()
                self.assertEqual([r["route"] for r in rows], ["Tor", "Tor", "Direct"])
                dialog = ui.NetworkLogDialog(rows, desktop_core, window)
                self.assertEqual(dialog.table.rowCount(), 3)
                self.assertEqual(dialog.table.item(0, 1).text(), "Tor")
                saved_json = dialog.save("json", str(Path(tmp) / "out.json"))
                saved_csv = dialog.save("csv", str(Path(tmp) / "out.csv"))
                for path in (saved_json, saved_csv):
                    text = Path(path).read_text(encoding="utf-8")
                    self.assertNotIn("secret", text)
                    self.assertIn("registry.ollama.ai", text)
                self.assertEqual(len(json.loads(Path(saved_json).read_text())), 3)
                self.assertTrue(dialog.feedback.text().endswith("· 3 entries"))
                empty = ui.NetworkLogDialog([], desktop_core, window)
                self.assertFalse(empty.empty.isHidden())
                for d in (dialog, empty):
                    d.deleteLater()
            finally:
                self._close(window)


class _ChatCore:
    """Records the history each turn sends, answers at once."""

    MODEL = "inferno"
    BACKEND = "ollama"

    def __init__(self) -> None:
        self.sent: list[list[dict]] = []

    def turn_stream(self, history, on_text, **kwargs):
        self.sent.append(json.loads(json.dumps(history)))
        history.append({"role": "assistant", "content": f"answer {len(self.sent)}"})
        on_text(f"answer {len(self.sent)}")
        return f"answer {len(self.sent)}"


def _action_labels(block) -> list[str]:
    if block.actions_row is None:
        return []
    return [b.text() for b in block.actions_row.findChildren(QPushButton)]


class MsgActionsTests(V2Base):
    def _turn(self, window, text):
        window.composer.setPlainText(text)
        window.submit()
        self.assertTrue(self._wait_until(lambda: window.active_kind is None))

    def test_off_messages_have_no_actions(self) -> None:
        core = _ChatCore()
        window = self._window(core=core)
        try:
            window.set_mode("chat")
            window.chat_messages = []
            window.transcript.clear()
            self._turn(window, "hello")
            self.assertTrue(all(_action_labels(b) == [] for b in window.transcript._message_blocks))
        finally:
            self._close(window)

    def test_on_copy_edit_regenerate_and_branch(self) -> None:
        core = _ChatCore()
        window = self._window(core=core, msgActions=True)
        with tempfile.TemporaryDirectory() as tmp:
            try:
                window.session_bridge = ui.SessionBridge(desktop_core, Path(tmp))
                window.set_mode("chat")
                window.chat_messages = []
                window.transcript.clear()
                self._turn(window, "first question")
                self._turn(window, "second question")
                user, reply = window.transcript._message_blocks[0], window.transcript._message_blocks[1]
                self.assertEqual(_action_labels(user), ["Edit", "Copy"])
                self.assertEqual(_action_labels(reply), ["Copy", "Regenerate", "Branch from here"])
                _button(reply.actions_row, "Copy").click()
                self.assertEqual(QApplication.clipboard().text(), "answer 1")
                # Edit the first question: everything after it is replaced.
                _button(window.transcript._message_blocks[0].actions_row, "Edit").click()
                self.assertFalse(window.edit_banner.isHidden())
                self.assertEqual(window.composer.toPlainText(), "first question")
                window.composer.setPlainText("first question, edited")
                window.submit()
                self.assertTrue(self._wait_until(lambda: window.active_kind is None))
                self.assertTrue(window.edit_banner.isHidden())
                self.assertEqual([m["content"] for m in core.sent[-1]], ["first question, edited"])
                self.assertEqual(len(window.chat_messages), 2)
                # Regenerate the reply: the same question goes again.
                _button(window.transcript._message_blocks[1].actions_row, "Regenerate").click()
                self.assertTrue(self._wait_until(lambda: window.active_kind is None))
                self.assertEqual([m["content"] for m in core.sent[-1]], ["first question, edited"])
                self.assertEqual(window.chat_messages[-1]["content"], f"answer {len(core.sent)}")
                # Branch from the reply: a new saved session with history up to it.
                _button(window.transcript._message_blocks[1].actions_row, "Branch from here").click()
                stored = desktop_core.SessionStore(Path(tmp)).list()
                self.assertEqual(len(stored), 1)
                self.assertTrue(stored[0].title.startswith("Branch of "))
                self.assertEqual(len(stored[0].messages), 2)
                self.assertIn("The original is unchanged.", window.status_label.text())
                window.set_feature("msgActions", False)
                self.assertTrue(all(_action_labels(b) == [] for b in window.transcript._message_blocks))
            finally:
                self._close(window)


class SessionSearchTests(V2Base):
    def _store(self, tmp):
        store = desktop_core.SessionStore(Path(tmp))
        a = Path(tmp) / "alpha"
        b = Path(tmp) / "beta"
        a.mkdir()
        b.mkdir()
        store.create(title="Login work", workspace=a,
                     messages=[{"role": "user", "content": "Fix the login redirect loop"}])
        store.create(title="Auth notes", workspace=b,
                     messages=[{"role": "assistant", "content": "The redirect lives in auth.py"}])
        return store

    def test_off_filter_only_matches_titles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            window = self._window()
            try:
                self._store(tmp)
                window.session_bridge = ui.SessionBridge(desktop_core, Path(tmp))
                window.left_rail.session_filter.setText("redirect")
                self.assertEqual(window.left_rail.session_filter.placeholderText(), "Filter sessions")
                self.assertTrue(window.left_rail.message_hits.isHidden())
            finally:
                self._close(window)

    def test_on_hits_span_projects_highlight_and_open(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            window = self._window(sessionSearch=True)
            try:
                self._store(tmp)
                window.session_bridge = ui.SessionBridge(desktop_core, Path(tmp))
                self.assertEqual(window.left_rail.session_filter.placeholderText(), "Search sessions and messages")
                window.left_rail.session_filter.setText("redirect")
                hits = window.left_rail.message_hits
                self.assertFalse(hits.isHidden())
                self.assertEqual(hits.count(), 2)
                texts = [hits.item(i).data(Qt.ItemDataRole.AccessibleTextRole) for i in range(2)]
                self.assertTrue(any(t.startswith("alpha · Login work") for t in texts), texts)
                self.assertTrue(any(t.startswith("beta · Auth notes") for t in texts), texts)
                widget = hits.itemWidget(hits.item(0))
                self.assertIn("<b", widget.text())
                opened: list[str] = []
                window.load_session = opened.append
                window.left_rail.hitSelected.disconnect()
                window.left_rail.hitSelected.connect(window.load_session)
                hits.itemClicked.emit(hits.item(0))
                self.assertEqual(len(opened), 1)
                window.left_rail.session_filter.setText("r")
                self.assertTrue(hits.isHidden())
            finally:
                self._close(window)


class UndoTests(V2Base):
    def _repo(self, root: Path) -> None:
        run = lambda *a: subprocess.run(["git", *a], cwd=root, check=True, capture_output=True)
        run("init", "-q")
        run("config", "user.email", "t@example.invalid")
        run("config", "user.name", "t")
        (root / "a.txt").write_text("original\n", encoding="utf-8")
        (root / "b.txt").write_text("keep\n", encoding="utf-8")
        run("add", ".")
        run("commit", "-q", "-m", "init")
        (root / "a.txt").write_text("agent edit\n", encoding="utf-8")
        (root / "b.txt").write_text("also edited\n", encoding="utf-8")

    def _open(self, window, root) -> None:
        # Resolved, as select_workspace stores it: a Windows temp folder can
        # carry an 8.3 short name that would not match the snapshot's root.
        window.workspace = str(Path(root).resolve())
        window.refresh_workspace()
        self.assertTrue(self._wait_until(lambda: len((window.current_snapshot or {}).get("changes") or []) == 2))

    def test_off_changes_are_a_plain_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._repo(Path(tmp))
            window = self._window()
            try:
                self._open(window, Path(tmp))
                inspector = window.inspector
                self.assertIsNone(inspector.change_list.itemWidget(inspector.change_list.item(0)))
                self.assertTrue(inspector.revert_all_button.isHidden())
            finally:
                window.workspace = None
                self._close(window)

    def test_on_revert_one_file_then_undo_checked_on_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as data:
            root = Path(tmp)
            self._repo(root)
            window = self._window(undo=True)
            try:
                window.data_root = Path(data)
                self._open(window, root)
                inspector = window.inspector
                self.assertFalse(inspector.revert_all_button.isHidden())
                row = inspector.change_list.itemWidget(inspector.change_list.item(0))
                path = window.current_snapshot["changes"][0]["path"]
                row.findChild(ui.QToolButton).click()
                self.assertTrue(self._wait_until(
                    lambda: (root / path).read_text(encoding="utf-8") in ("original\n", "keep\n")))
                self.assertTrue(self._wait_until(lambda: inspector.undo_label.text() == f"Reverted {path}"),
                                inspector.undo_label.text())
                self.assertFalse(inspector.undo_bar.isHidden())
                status = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True)
                self.assertEqual(len(status.stdout.splitlines()), 1)
                inspector.undo_button.click()
                self.assertTrue(self._wait_until(lambda: inspector.undo_bar.isHidden()))
                self.assertTrue(self._wait_until(lambda: len(subprocess.run(
                    ["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True).stdout.splitlines()) == 2))
                self.assertTrue(self._wait_until(lambda: len(window.current_snapshot.get("changes") or []) == 2))
                inspector.revert_all_button.click()
                self.assertTrue(self._wait_until(lambda: subprocess.run(
                    ["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True).stdout == ""))
                # The files are restored before the job's result reaches the
                # window, so a clean tree can come a moment before the label.
                self.assertTrue(self._wait_until(lambda: inspector.undo_label.text() == "Reverted 2 files"),
                                inspector.undo_label.text())
            finally:
                window.workspace = None
                self._close(window)


class RulesTests(AgentStreamBase):
    def test_off_no_rules_tab_and_no_rule_decisions(self) -> None:
        window = self._window()
        try:
            self.assertNotIn("Agent rules", self._tab_labels(self._settings(window)))
            self.assertIsNone(window._project_rules() if window.feature("rules") else None)
        finally:
            self._close(window)

    def test_on_tab_saves_per_project_and_edit_tests_skips_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            window = self._window(rules=True, approvals=True)
            try:
                window.workspace = tmp
                dialog = self._settings(window)
                self.assertIn("Agent rules", self._tab_labels(dialog))
                dialog.rule_boxes["editTests"].click()
                dialog.rule_blocked.setText(".env, secrets/")
                dialog.rule_blocked.editingFinished.emit()
                rules = desktop_core.load_rules(window.settings_data, tmp)
                self.assertTrue(rules["editTests"])
                self.assertEqual(rules["blocked"], ".env, secrets/")
                self.assertEqual(desktop_core.load_rules(window.settings_data, tmp + "-other")["editTests"], False)
                process = _FakeProcess()
                window.harness_process = process
                window.active_kind = "agent"
                window._agent_stream = True
                window._agent_rules = window._project_rules()
                window.stream_block = window.transcript.add_message("assistant", "")
                request = json.loads(FIXTURE_LINES[3])
                request["request"]["input"]["file_path"] = str(Path(tmp) / "tests" / "test_a.py")
                window._handle_agent_line(json.dumps(request))
                self.assertEqual(_cards(window, "wants to edit"), [])
                self.assertEqual(process.written[-1]["response"]["response"]["behavior"], "allow")
                request["request_id"] = "r2"
                request["request"]["input"]["file_path"] = str(Path(tmp) / ".env")
                window._handle_agent_line(json.dumps(request))
                self.assertEqual(process.written[-1]["response"]["response"]["behavior"], "deny")
                denials = desktop_core.agent_rule_denials(rules)
                self.assertIn("edit(/.env)", denials)
            finally:
                window.active_kind = None
                window.workspace = None
                self._close(window)


class PresetsTests(V2Base):
    def _turn(self, window, text):
        window.composer.setPlainText(text)
        window.submit()
        self.assertTrue(self._wait_until(lambda: window.active_kind is None))

    def test_off_no_chip_and_no_system_prompt(self) -> None:
        core = _ChatCore()
        window = self._window(core=core)
        try:
            window.set_mode("chat")
            self.assertTrue(window.preset_chip.isHidden())
            self._turn(window, "hi")
            self.assertNotIn("system", [m["role"] for m in core.sent[-1]])
        finally:
            self._close(window)

    def test_on_chip_menu_editor_and_the_prompt_is_sent_not_stored(self) -> None:
        core = _ChatCore()
        window = self._window(core=core, presets=True)
        try:
            window.set_mode("chat")
            self.assertFalse(window.preset_chip.isHidden())
            self.assertEqual(window.preset_chip.text(), "Default")
            names = [a.text() for a in window.preset_menu.actions() if a.text()]
            self.assertIn("Edit presets…", names)
            self.assertTrue(any(n.startswith("Concise") for n in names))
            next(a for a in window.preset_menu.actions() if a.text().startswith("Concise")).trigger()
            self.assertEqual(window.preset_chip.text(), "Concise")
            self._turn(window, "hi")
            self.assertEqual(core.sent[-1][0]["role"], "system")
            self.assertTrue(core.sent[-1][0]["content"].startswith("Answer in as few words"))
            self.assertNotIn("system", [m["role"] for m in window.chat_messages])
            dialog = self._settings(window)
            self.assertIn("Presets", self._tab_labels(dialog))
            dialog.preset_editor.setPlainText("Only haiku.")
            self._turn(window, "again")
            self.assertEqual(core.sent[-1][0]["content"], "Only haiku.")
            dialog.preset_reset.click()
            self.assertTrue(dialog.preset_editor.toPlainText().startswith("Answer in as few words"))
            window.set_mode("agent")
            self.assertTrue(window.preset_chip.isHidden())
        finally:
            self._close(window)


def _fault_card(window):
    return window._fault_card


class ErrorsTests(V2Base):
    def test_off_failures_keep_the_retry_button_and_no_card(self) -> None:
        window = self._window()
        try:
            window.active_kind = "chat"
            window.stream_block = window.transcript.add_message("assistant", "")
            window._chat_failed("Ollama is not running on 127.0.0.1:11434. Start it, then retry.")
            self.assertIsNone(window._fault)
            self.assertFalse(window.retry_button.isHidden())
            self.assertTrue(window.fault_host.isHidden())
        finally:
            self._close(window)

    def test_on_each_real_failure_gets_its_card_and_blocks_sending(self) -> None:
        window = self._window(errors=True)
        try:
            for message, kind, title in (
                ("Ollama is not running on 127.0.0.1:11434. Start it, then retry.", "server", "The local model server stopped"),
                ('Ollama returned 500: {"error":"CUDA error: out of memory"}', "oom", "Not enough GPU memory for INFERNO"),
            ):
                window.active_kind = "chat"
                window.stream_block = window.transcript.add_message("assistant", "")
                window._chat_failed(message)
                self.assertEqual(window._fault, kind)
                self.assertEqual(_fault_card(window).title_label.text(), title)
                self.assertTrue(window.retry_button.isHidden())
                window.composer.setPlainText("hello")
                window.submit()
                self.assertEqual(window.status_label.text(), window.FAULTS[kind]["status"])
            window._report_error("x", "tor")
            self.assertEqual(_fault_card(window).title_label.text(), "The Tor circuit failed")
            window.set_mode("agent")
            _button(_fault_card(window), "Continue without search").click()
            self.assertIsNone(window._fault)
            window._report_error("Agent runtime crashed", "node")
            _button(_fault_card(window), "Switch to Chat").click()
            self.assertEqual(window.mode, "chat")
            self.assertTrue(window.fault_host.isHidden())
        finally:
            window.active_kind = None
            self._close(window)

    def test_restart_model_server_reprobes_for_real_and_clears(self) -> None:
        probes: list[bool] = []
        core = SimpleNamespace(MODEL="inferno", BACKEND="ollama", detect_backend=lambda: probes.append(True),
                               installed_models=lambda: ["inferno"])
        window = self._window(core=core, errors=True)
        try:
            window._report_error("No model server on 11434 (ollama) or 8080 (llama-server). Start one.")
            _button(_fault_card(window), "Restart model server").click()
            self.assertTrue(self._wait_until(lambda: window._fault is None))
            self.assertEqual(probes, [True])
            self.assertEqual(window.status_label.text(), "Model server restarted. Onionmind is ready.")
        finally:
            self._close(window)

    def test_out_of_memory_switches_to_a_lighter_installed_tier(self) -> None:
        window = self._window(errors=True)
        try:
            window._model_power = {"sizes": {"inferno": 17, "blaze": 6, "ember": 3}}
            window._report_error('Ollama returned 500: {"error":"out of memory"}')
            card = _fault_card(window)
            self.assertIn("Switch to BLAZE", [b.text() for b in card.findChildren(QPushButton)])
            _button(card, "Switch to BLAZE").click()
            self.assertEqual(window.current_model_id(), "blaze")
            self.assertIsNone(window._fault)
        finally:
            self._close(window)


class EmptyTests(V2Base):
    def _fresh(self, window):
        window.chat_messages = []
        window.new_task(save_current=False)

    def test_off_no_starters_banner_or_empty_texts(self) -> None:
        window = self._window()
        try:
            self._fresh(window)
            self.assertIsNone(window._starters)
            window._model_probe_complete(("ollama", []))
            self.assertTrue(window.fault_host.isHidden())
            window.left_rail.set_sessions([])
            self.assertTrue(window.left_rail.no_sessions.isHidden())
        finally:
            self._close(window)

    def test_on_starters_send_and_no_model_banner_blocks(self) -> None:
        window = self._window(empty=True)
        try:
            sent: list[str] = []
            window.set_mode("chat")
            window._start_chat = lambda allow_search=False: sent.append(window.chat_messages[-1]["content"])
            window.set_feature("webAsk", False)
            self._fresh(window)
            self.assertIsNotNone(window._starters)
            _button(window._starters, "Explain how this project is structured").click()
            self.assertEqual(sent, ["Explain how this project is structured"])
            window.left_rail.set_sessions([])
            self.assertFalse(window.left_rail.no_sessions.isHidden())
            window._model_probe_complete(("ollama", []))
            self.assertFalse(window.fault_host.isHidden())
            banner = window._no_model_banner
            self.assertIn("No model is installed yet", banner.title_label.text())
            window.active_kind = None
            window.composer.setPlainText("hi")
            window.submit()
            self.assertEqual(window.status_label.text(), "No model is installed yet. Choose one in Models.")
            window._model_probe_complete(("ollama", ["inferno"]))
            self.assertTrue(window.fault_host.isHidden())
            window.inspector.update_snapshot({"root": "/x", "is_git": True, "changes": [], "diff": ""})
            self.assertTrue(window.inspector.diff_view.toPlainText().startswith("Nothing has changed in this project yet."))
        finally:
            self._close(window)


class SetupTests(V2Base):
    def test_off_run_setup_explains_and_nothing_opens(self) -> None:
        window = self._window()
        try:
            window.open_setup()
            self.assertTrue(window.setup_overlay.isHidden())
            self.assertIn("First-launch setup", window.status_label.text())
        finally:
            self._close(window)

    def test_on_five_real_steps_and_finish(self) -> None:
        core = SimpleNamespace(MODEL="inferno", BACKEND="ollama", detect_backend=lambda: None,
                               tor_proxy_port=lambda: None)
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(desktop_core, "gpu_memory_mb", return_value=(100, 4096)), \
             mock.patch.object(desktop_core, "gpu_name", return_value="Test GPU"), \
             mock.patch.object(desktop_core, "node_version", return_value="v24.1.0"):
            window = self._window(core=core, setup=True)
            try:
                dialog = self._settings(window)
                self.assertFalse(dialog._feature_sections["setup"].isHidden())
                window.open_setup()
                overlay = window.setup_overlay
                self.assertFalse(overlay.isHidden())
                self.assertEqual(overlay.title.text(), "Welcome to Onionmind")
                overlay.next_button.click()
                self.assertTrue(self._wait_until(lambda: len(overlay.checks) == 4))
                texts = [row.text() for row in overlay.check_rows.values()]
                self.assertIn("✓  Graphics memory    4 GB GPU · Test GPU", texts)
                self.assertIn("✓  Agent runtime    Node.js 24.1.0 found", texts)
                self.assertIn("·  Tor    Not running yet. That’s fine.", texts)
                self.assertEqual(overlay.body.text(), "Everything Onionmind needs is on this machine.")
                overlay.next_button.click()
                self.assertEqual(overlay.title.text(), "Choose a model")
                overlay.next_button.click()
                self.assertEqual(overlay.next_button.text(), "Continue")   # demo Tor is verified
                overlay.next_button.click()
                self.assertEqual(overlay.title.text(), "Open a project")
                overlay.set_project(tmp)
                self.assertEqual(overlay.next_button.text(), f"Open {Path(tmp).name}")
                opened: list[str] = []
                window.select_workspace = opened.append
                overlay.next_button.click()
                self.assertEqual(opened, [tmp])
                self.assertTrue(overlay.isHidden())
                self.assertTrue(window.settings_data["setup_done"])
                self.assertEqual(window.status_label.text(), "Setup finished. Onionmind is ready.")
            finally:
                self._close(window)


class LockTests(V2Base):
    def test_off_no_lock_button_section_or_lock(self) -> None:
        window = self._window()
        try:
            window.resize(1440, 900)
            self.app.processEvents()
            self.assertTrue(window.lock_button.isHidden())
            self.assertTrue(self._settings(window)._feature_sections["lock"].isHidden())
            self.assertFalse(window.lock_now())
        finally:
            self._close(window)

    def test_on_passphrase_lock_unlock_idle_and_auto_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            window = self._window(lock=True)
            try:
                window.resize(1440, 900)
                self.app.processEvents()
                self.assertFalse(window.lock_button.isHidden())
                window.open_settings_tab = lambda label: None
                self.assertFalse(window.lock_now())            # no passphrase yet: no lock
                dialog = self._settings(window)
                self.assertFalse(dialog._feature_sections["lock"].isHidden())
                dialog.passphrase_field.setText("open sesame")
                dialog.passphrase_button.click()
                self.assertNotIn("open sesame", json.dumps(window.settings_data))
                self.assertTrue(window.lock_now())
                overlay = window.lock_overlay
                self.assertFalse(overlay.isHidden())
                overlay.unlock_button.click()
                self.assertEqual(overlay.error.text(), "Enter your passphrase to unlock.")
                overlay.passphrase.setText("wrong")
                overlay.unlock_button.click()
                self.assertFalse(overlay.isHidden())
                overlay.passphrase.setText("open sesame")
                overlay.unlock_button.click()
                self.assertTrue(overlay.isHidden())
                window.set_lock_setting("lock_after", 5)
                self.assertTrue(window.idle_timer.isActive())
                window._last_input -= 6 * 60
                window._check_idle()
                self.assertFalse(overlay.isHidden())
                overlay.passphrase.setText("open sesame")
                overlay.try_unlock()
                store = desktop_core.SessionStore(Path(tmp))
                old = store.create(title="Old", messages=[{"role": "user", "content": "x"}])
                payload = json.loads((Path(tmp) / f"{old.id}.json").read_text())
                payload["updated_at"] = "2020-01-01T00:00:00+00:00"
                (Path(tmp) / f"{old.id}.json").write_text(json.dumps(payload))
                store.create(title="New", messages=[{"role": "user", "content": "y"}])
                window.session_bridge = ui.SessionBridge(desktop_core, Path(tmp))
                dialog.auto_delete_combo.setCurrentIndex(dialog.auto_delete_combo.findData(7))
                self.assertEqual(dialog.auto_delete_note.text(),
                                 "1 conversation is older than 7 days and will be deleted.")
                dialog.delete_now.click()
                self.assertEqual([s.title for s in store.list()], ["New"])
            finally:
                window.set_lock_setting("lock_after", 0)
                self._close(window)


class AppearanceTests(V2Base):
    def test_off_dark_100_percent_and_no_notifications(self) -> None:
        window = self._window()
        try:
            self.assertEqual(ui.current_theme(), "dark")
            self.assertTrue(self._settings(window)._feature_sections["appearance"].isHidden())
            window._notify("Onionmind replied", "x")
            self.assertIsNone(window.last_notification)
        finally:
            self._close(window)

    def test_on_light_theme_size_and_a_real_notification(self) -> None:
        window = self._window(appearance=True)
        try:
            dialog = self._settings(window)
            self.assertFalse(dialog._feature_sections["appearance"].isHidden())
            dialog.theme_combo.setCurrentIndex(dialog.theme_combo.findData("light"))
            self.assertEqual(ui.current_theme(), "light")
            self.assertIn(ui.NOCTURNE_LIGHT["bg"], self.app.styleSheet())
            dialog.ui_scale_combo.setCurrentIndex(dialog.ui_scale_combo.findData(125))
            self.assertEqual(window._ui_scale_factor(), 1.25)
            dialog.notify_box.setChecked(True)
            with mock.patch.object(window, "isActiveWindow", return_value=False):
                window._notify("Agent run finished", "Task · demo")
            self.assertEqual(window.last_notification, ("Agent run finished", "Task · demo"))
            window.set_feature("appearance", False)
            self.assertEqual(ui.current_theme(), "dark")
            self.assertEqual(window._ui_scale_factor(), 1.0)
            self.assertNotIn(ui.NOCTURNE_LIGHT["bg"], self.app.styleSheet())
        finally:
            ui.set_theme("dark")
            self._close(window)


if __name__ == "__main__":
    unittest.main()
