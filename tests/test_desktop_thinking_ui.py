"""Qt contracts for the in-transcript thinking indicator."""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest import mock


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication
    import onionmind_desktop as desktop
except ModuleNotFoundError:
    QApplication = None
    desktop = None


@unittest.skipIf(desktop is None, "PySide6 is not installed in this test environment")
class ThinkingStreamFilterTests(unittest.TestCase):
    def test_plain_answer_stays_buffered_until_finish(self) -> None:
        stream_filter = desktop.ThinkingStreamFilter()
        self.assertEqual(stream_filter.feed("  Plain"), "")
        self.assertEqual(stream_filter.feed(" answer  "), "")

        self.assertEqual(stream_filter.finish(), "Plain answer")
        self.assertEqual(stream_filter.finish(), "")
        self.assertEqual(stream_filter.feed("must not appear"), "")
        self.assertEqual(stream_filter._chunks, [])
        self.assertEqual(stream_filter._characters, 0)

    def test_variant_tags_survive_every_three_chunk_boundary(self) -> None:
        payload = (
            "< THINK data-mode='private' ><think>SECRET</think></ tHiNk\t>Answer"
        )
        for first_cut in range(len(payload) + 1):
            for second_cut in range(first_cut, len(payload) + 1):
                with self.subTest(first_cut=first_cut, second_cut=second_cut):
                    stream_filter = desktop.ThinkingStreamFilter()
                    for chunk in (
                        payload[:first_cut],
                        payload[first_cut:second_cut],
                        payload[second_cut:],
                    ):
                        self.assertEqual(stream_filter.feed(chunk), "")
                    self.assertEqual(stream_filter.finish(), "Answer")

    def test_omitted_opening_closer_never_emits_at_any_boundary(self) -> None:
        payload = "SECRET REASONING</ THINK data-model=x>Final answer"
        for first_cut in range(len(payload) + 1):
            for second_cut in range(first_cut, len(payload) + 1):
                with self.subTest(first_cut=first_cut, second_cut=second_cut):
                    stream_filter = desktop.ThinkingStreamFilter()
                    for chunk in (
                        payload[:first_cut],
                        payload[first_cut:second_cut],
                        payload[second_cut:],
                    ):
                        self.assertEqual(stream_filter.feed(chunk), "")
                    self.assertEqual(stream_filter.finish(), "Final answer")

    def test_partial_closer_fails_closed_for_every_split(self) -> None:
        for payload in ("SECRET</thi", "SECRET</ THI", "SECRET</ THINK data=x"):
            for cut in range(len(payload) + 1):
                with self.subTest(payload=payload, cut=cut):
                    stream_filter = desktop.ThinkingStreamFilter()
                    self.assertEqual(stream_filter.feed(payload[:cut]), "")
                    self.assertEqual(stream_filter.feed(payload[cut:]), "")
                    self.assertEqual(stream_filter.finish(), "")

    def test_multiple_tool_rounds_and_ordinary_less_than_text_are_sanitized_once(self) -> None:
        payload = (
            "I will search. 2 < 3."
            "< THINK source=first>SECRET ONE</ THINK >"
            "Tool complete.<think>< THINK nested=yes>SECRET TWO</ THINK ></think>"
            "Final answer with <this ordinary text."
        )
        stream_filter = desktop.ThinkingStreamFilter()
        for character in payload:
            self.assertEqual(stream_filter.feed(character), "")

        self.assertEqual(
            stream_filter.finish(),
            "I will search. 2 < 3.Tool complete.Final answer with <this ordinary text.",
        )

    def test_abort_drops_an_unterminated_reasoning_block(self) -> None:
        stream_filter = desktop.ThinkingStreamFilter()
        self.assertEqual(stream_filter.feed("<think>private reasoning</thi"), "")

        stream_filter.abort()

        self.assertEqual(stream_filter.feed("nk>must not appear"), "")
        self.assertEqual(stream_filter.finish(), "")
        self.assertEqual(stream_filter._chunks, [])

    def test_privacy_buffer_has_a_hard_limit_and_clears_on_overflow(self) -> None:
        stream_filter = desktop.ThinkingStreamFilter(max_characters=8)
        self.assertEqual(stream_filter.feed("12345678"), "")
        with self.assertRaisesRegex(RuntimeError, "privacy buffer limit"):
            stream_filter.feed("9")

        self.assertEqual(stream_filter.finish(), "")
        self.assertEqual(stream_filter._chunks, [])
        self.assertEqual(stream_filter._characters, 0)

    def test_completion_sanitizes_every_assistant_tool_round_before_save(self) -> None:
        saved: list[list[dict]] = []
        final_block = SimpleNamespace(set_text=mock.Mock())
        probe = mock.Mock(return_value=None)
        host = SimpleNamespace(
            core=SimpleNamespace(tor_proxy_port=probe),
            stream_block=final_block,
            chat_messages=[],
            _show_local_tor_state=mock.Mock(),
            set_status=mock.Mock(),
            inspector=SimpleNamespace(append_activity=mock.Mock()),
            _set_active=mock.Mock(),
            save_current_session=lambda: saved.append(host.chat_messages) or True,
        )
        tool_calls = [{"function": {"name": "web_search", "arguments": {}}}]
        history = [
            {"role": "user", "content": "Question"},
            {
                "role": "assistant",
                "content": "<think>EARLIER SECRET</think>I will use a tool.",
                "reasoning": "STRUCTURED SECRET",
                "tool_calls": tool_calls,
            },
            {"role": "tool", "content": "result"},
            {
                "role": "assistant",
                "content": "<think>FINAL SECRET</think>Visible answer",
            },
        ]

        desktop.OnionmindWindow._chat_complete(
            host, {"answer": "<think>FINAL SECRET</think>Visible answer", "history": history}
        )

        self.assertEqual(final_block.set_text.call_args.args[0], "Visible answer")
        self.assertEqual(host.chat_messages[1]["content"], "I will use a tool.")
        self.assertEqual(host.chat_messages[1]["tool_calls"], tool_calls)
        self.assertNotIn("reasoning", host.chat_messages[1])
        self.assertEqual(host.chat_messages[-1]["content"], "Visible answer")
        self.assertEqual(saved, [host.chat_messages])
        self.assertNotIn("SECRET", repr(host.chat_messages))

    def test_export_formatter_defensively_sanitizes_legacy_assistant_content(self) -> None:
        markdown = desktop._conversation_markdown(
            "Legacy",
            "inferno:latest",
            None,
            [
                {"role": "user", "content": "Question"},
                {
                    "role": "assistant",
                    "content": (
                        "Before< THINK data-origin=legacy>EXPORT SECRET</ THINK >"
                        " after< THI"
                    ),
                },
            ],
        )

        self.assertIn("Before after", markdown)
        self.assertNotIn("EXPORT SECRET", markdown)
        self.assertNotIn("< THINK", markdown)


@unittest.skipIf(QApplication is None, "PySide6 is not installed in this test environment")
class ThinkingIndicatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_reduced_motion_keeps_a_static_textual_indicator(self) -> None:
        with mock.patch.object(desktop, "_ui_animations_enabled", return_value=False):
            indicator = desktop.ThinkingIndicator()
        indicator.show()
        indicator.start()
        self.app.processEvents()
        self.assertEqual(indicator.label.text(), "Thinking")
        self.assertEqual(indicator.accessibleName(), "Onionmind is thinking")
        self.assertFalse(indicator.timer.isActive())
        indicator.close()

    def test_completed_sanitized_text_replaces_the_indicator_in_the_same_block(self) -> None:
        block = desktop.MessageBlock("assistant", "")
        block.show()
        block.start_thinking()
        self.app.processEvents()
        self.assertFalse(block.thinking.isHidden())
        self.assertTrue(block.body.isHidden())

        host = SimpleNamespace(
            core=SimpleNamespace(tor_proxy_port=lambda: None),
            stream_block=block,
            chat_messages=[],
            _show_local_tor_state=lambda port: None,
            set_status=lambda text: None,
            inspector=SimpleNamespace(append_activity=lambda text: None),
            _set_active=lambda value: None,
            save_current_session=lambda: True,
        )
        desktop.OnionmindWindow._chat_complete(
            host,
            {
                "answer": "PRIVATE REASONING</ THINK >Ready",
                "history": [
                    {
                        "role": "assistant",
                        "content": "PRIVATE REASONING</ THINK >Ready",
                    }
                ],
            },
        )
        self.app.processEvents()

        self.assertTrue(block.thinking.isHidden())
        self.assertFalse(block.body.isHidden())
        self.assertEqual(block.text, "Ready")
        self.assertEqual(block.body.text(), "Ready")
        self.assertEqual(block.accessibleName(), "Onionmind message")
        block.close()

    def test_plain_chunks_stay_behind_the_same_indicator_until_completion(self) -> None:
        block = desktop.MessageBlock("assistant", "")
        block.show()
        payloads: list[dict] = []

        class Signal:
            def emit(self, value) -> None:
                del value

            def connect(self, callback) -> None:
                del callback

        signals = SimpleNamespace(event=Signal(), result=Signal(), error=Signal())

        def turn_stream(history, on_text, **kwargs):
            del history, kwargs
            on_text("Plain ")
            on_text("answer")
            return "Plain answer"

        def start_worker(job):
            payloads.append(job(signals))
            return SimpleNamespace(signals=signals)

        host = SimpleNamespace(
            stop_event=None,
            current_model_id=lambda: "model",
            chat_messages=[],
            transcript=SimpleNamespace(add_message=lambda role, text: block),
            stream_block=None,
            set_status=lambda text: None,
            _describe_model=lambda model: model,
            inspector=SimpleNamespace(append_activity=lambda text: None),
            core=SimpleNamespace(
                BACKEND="ollama",
                turn_stream=turn_stream,
                tor_proxy_port=lambda: None,
            ),
            _start_worker=start_worker,
            _chat_event=lambda event: None,
            _chat_complete=lambda payload: None,
            _chat_failed=lambda message: None,
            _show_local_tor_state=lambda port: None,
            _set_active=lambda value: None,
            save_current_session=lambda: True,
        )

        desktop.OnionmindWindow._start_chat(host)
        self.app.processEvents()

        self.assertEqual(payloads[0]["answer"], "Plain answer")
        self.assertIs(host.stream_block, block)
        self.assertFalse(block.thinking.isHidden())
        self.assertTrue(block.body.isHidden())
        self.assertEqual(block.text, "")

        desktop.OnionmindWindow._chat_complete(host, payloads[0])
        self.app.processEvents()

        self.assertTrue(block.thinking.isHidden())
        self.assertFalse(block.body.isHidden())
        self.assertEqual(block.text, "Plain answer")
        block.close()

    def test_pending_label_tracks_real_work_without_losing_thinking_state(self) -> None:
        block = desktop.MessageBlock("assistant", "")
        block.start_thinking("Starting Tor")
        block.set_pending_label("Thinking")
        self.assertEqual(block.thinking.label.text(), "Thinking")
        self.assertEqual(block.accessibleName(), "Onionmind is thinking")
        block.stop_thinking()

    def test_chat_job_buffers_all_chunks_and_prefers_the_sanitized_return(self) -> None:
        payloads: list[dict] = []

        class Signal:
            def __init__(self) -> None:
                self.emitted: list = []
                self.connected: list = []

            def emit(self, value) -> None:
                self.emitted.append(value)

            def connect(self, callback) -> None:
                self.connected.append(callback)

        signals = SimpleNamespace(
            text=Signal(),
            event=Signal(),
            result=Signal(),
            error=Signal(),
        )

        def turn_stream(history, on_text, **kwargs):
            del history, kwargs
            for chunk in (
                "STREAM SECRET",
                "</ THI",
                "NK >Buffered answer",
            ):
                on_text(chunk)
            return "RETURN SECRET</ THINK data-source=return>Returned answer"

        core = SimpleNamespace(BACKEND="ollama", turn_stream=turn_stream)
        block = SimpleNamespace(start_thinking=lambda text: None)

        def start_worker(job):
            payloads.append(job(signals))
            return SimpleNamespace(signals=signals)

        host = SimpleNamespace(
            stop_event=None,
            current_model_id=lambda: "model",
            chat_messages=[],
            transcript=SimpleNamespace(add_message=lambda role, text: block),
            stream_block=None,
            set_status=lambda text: None,
            _describe_model=lambda model: model,
            inspector=SimpleNamespace(append_activity=lambda text: None),
            core=core,
            _start_worker=start_worker,
            _chat_event=lambda event: None,
            _chat_complete=lambda payload: None,
            _chat_failed=lambda message: None,
        )

        desktop.OnionmindWindow._start_chat(host)

        self.assertEqual(signals.text.emitted, [])
        self.assertEqual(signals.text.connected, [])
        self.assertEqual(payloads[0]["answer"], "Returned answer")

    def test_chat_job_uses_only_sanitized_buffer_when_returned_answer_is_empty(self) -> None:
        payloads: list[dict] = []

        class Signal:
            def emit(self, value) -> None:
                del value

            def connect(self, callback) -> None:
                del callback

        signals = SimpleNamespace(event=Signal(), result=Signal(), error=Signal())

        def turn_stream(history, on_text, **kwargs):
            del history, kwargs
            on_text("BUFFER SECRET</think>Buffered answer")
            return "<think>returned reasoning only</think>"

        core = SimpleNamespace(BACKEND="ollama", turn_stream=turn_stream)
        block = SimpleNamespace(start_thinking=lambda text: None)

        def start_worker(job):
            payloads.append(job(signals))
            return SimpleNamespace(signals=signals)

        host = SimpleNamespace(
            stop_event=None,
            current_model_id=lambda: "model",
            chat_messages=[],
            transcript=SimpleNamespace(add_message=lambda role, text: block),
            stream_block=None,
            set_status=lambda text: None,
            _describe_model=lambda model: model,
            inspector=SimpleNamespace(append_activity=lambda text: None),
            core=core,
            _start_worker=start_worker,
            _chat_event=lambda event: None,
            _chat_complete=lambda payload: None,
            _chat_failed=lambda message: None,
        )

        desktop.OnionmindWindow._start_chat(host)

        self.assertEqual(payloads[0]["answer"], "Buffered answer")


if __name__ == "__main__":
    unittest.main()
