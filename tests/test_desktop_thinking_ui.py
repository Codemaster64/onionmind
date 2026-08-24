"""Qt contracts for the in-transcript thinking indicator."""

from __future__ import annotations

import os
import random
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
    def test_plain_answer_streams_on_the_first_chunk(self) -> None:
        stream_filter = desktop.ThinkingStreamFilter()

        self.assertEqual(stream_filter.feed("  Plain"), "  Plain")
        self.assertEqual(stream_filter.feed(" answer"), " answer")

    def test_opening_and_closing_tags_can_split_at_every_boundary(self) -> None:
        opening = "<think>"
        closing = "</think>"
        for opening_cut in range(len(opening) + 1):
            for closing_cut in range(len(closing) + 1):
                with self.subTest(opening_cut=opening_cut, closing_cut=closing_cut):
                    stream_filter = desktop.ThinkingStreamFilter()
                    chunks = (
                        " \n" + opening[:opening_cut],
                        opening[opening_cut:] + "private reasoning" + closing[:closing_cut],
                        closing[closing_cut:] + "Answer",
                    )
                    visible = "".join(stream_filter.feed(chunk) for chunk in chunks)
                    stream_filter.finish()
                    self.assertEqual(visible, "Answer")

    def test_character_sized_chunks_never_expose_reasoning_markup(self) -> None:
        stream_filter = desktop.ThinkingStreamFilter()
        payload = "<think>private reasoning</think>Visible answer"

        emitted = [stream_filter.feed(character) for character in payload]

        self.assertEqual("".join(emitted), "Visible answer")
        self.assertTrue(all("<" not in chunk and ">" not in chunk for chunk in emitted))

    def test_variant_tags_survive_every_three_chunk_boundary(self) -> None:
        payload = "< THINK data-mode='private' >SECRET</ tHiNk\t>Answer"
        for first_cut in range(len(payload) + 1):
            for second_cut in range(first_cut, len(payload) + 1):
                with self.subTest(first_cut=first_cut, second_cut=second_cut):
                    stream_filter = desktop.ThinkingStreamFilter()
                    chunks = (
                        payload[:first_cut],
                        payload[first_cut:second_cut],
                        payload[second_cut:],
                    )
                    visible = "".join(stream_filter.feed(chunk) for chunk in chunks)
                    stream_filter.finish()
                    self.assertEqual(visible, "Answer")
                    self.assertNotIn("SECRET", visible)

    def test_false_opening_prefix_is_released_as_ordinary_text(self) -> None:
        stream_filter = desktop.ThinkingStreamFilter()

        self.assertEqual(stream_filter.feed("\n<thi"), "")
        self.assertEqual(stream_filter.feed("s is ordinary"), "\n<this is ordinary")

        stream_filter = desktop.ThinkingStreamFilter()
        self.assertEqual(stream_filter.feed("Visible "), "Visible ")
        self.assertEqual(stream_filter.feed("<thi"), "")
        self.assertEqual(stream_filter.feed("s is still ordinary"), "<this is still ordinary")

    def test_new_reasoning_block_is_hidden_after_visible_pre_tool_text(self) -> None:
        stream_filter = desktop.ThinkingStreamFilter()

        self.assertEqual(stream_filter.feed("I will search."), "I will search.")
        self.assertEqual(stream_filter.feed("<thi"), "")
        self.assertEqual(stream_filter.feed("nk>SECRET</think>Final"), "Final")

    def test_later_reasoning_block_survives_adversarial_chunk_boundaries(self) -> None:
        payload = "Visible before tool.<think>private reasoning</think>Final answer"
        expected = "Visible before tool.Final answer"

        for first_cut in range(len(payload) + 1):
            for second_cut in range(first_cut, len(payload) + 1):
                with self.subTest(first_cut=first_cut, second_cut=second_cut):
                    stream_filter = desktop.ThinkingStreamFilter()
                    chunks = (
                        payload[:first_cut],
                        payload[first_cut:second_cut],
                        payload[second_cut:],
                    )
                    visible = "".join(stream_filter.feed(chunk) for chunk in chunks)
                    stream_filter.finish()
                    self.assertEqual(visible, expected)

        generator = random.Random(1729)
        for case in range(100):
            with self.subTest(random_case=case):
                stream_filter = desktop.ThinkingStreamFilter()
                cursor = 0
                visible_parts: list[str] = []
                while cursor < len(payload):
                    chunk_size = generator.randint(1, 9)
                    visible_parts.append(stream_filter.feed(payload[cursor:cursor + chunk_size]))
                    cursor += chunk_size
                stream_filter.finish()
                self.assertEqual("".join(visible_parts), expected)

    def test_multiple_visible_and_reasoning_rounds_share_one_filter(self) -> None:
        stream_filter = desktop.ThinkingStreamFilter()
        chunks = (
            "Round one. <thi",
            "nk>first secret</think>",
            "Round two. <think>second secret",
            "</thi",
            "nk>Final answer",
        )

        visible = "".join(stream_filter.feed(chunk) for chunk in chunks)

        self.assertEqual(visible, "Round one. Round two. Final answer")
        self.assertNotIn("secret", visible)

    def test_multiple_variant_blocks_and_nested_blocks_remain_private(self) -> None:
        payload = (
            "Round one.< THINK source=first>SECRET ONE</ THINK >"
            "Round two.<think>< THINK nested=yes>SECRET TWO</ THINK ></think>Answer"
        )
        stream_filter = desktop.ThinkingStreamFilter()
        visible = "".join(stream_filter.feed(character) for character in payload)
        stream_filter.finish()

        self.assertEqual(visible, "Round one.Round two.Answer")
        self.assertNotIn("SECRET", visible)

    def test_variant_truncation_is_dropped_but_ordinary_less_than_text_streams(self) -> None:
        stream_filter = desktop.ThinkingStreamFilter()
        self.assertEqual(stream_filter.feed("Visible< THI"), "Visible")
        stream_filter.finish()
        self.assertEqual(stream_filter.feed("NK mode=x>SECRET"), "")

        stream_filter = desktop.ThinkingStreamFilter()
        visible = "".join(stream_filter.feed(character) for character in "2 < 3 and <this is ordinary")
        stream_filter.finish()
        self.assertEqual(visible, "2 < 3 and <this is ordinary")

    def test_completion_drops_an_undecided_opening_prefix(self) -> None:
        stream_filter = desktop.ThinkingStreamFilter()
        self.assertEqual(stream_filter.feed("<thi"), "")

        stream_filter.finish()

        self.assertEqual(stream_filter.feed("nk>private"), "")

    def test_abort_drops_an_unterminated_reasoning_block(self) -> None:
        stream_filter = desktop.ThinkingStreamFilter()
        self.assertEqual(stream_filter.feed("<think>private reasoning</thi"), "")

        stream_filter.abort()

        self.assertEqual(stream_filter.feed("nk>must not appear"), "")

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

    def test_first_streamed_text_replaces_the_indicator_in_the_same_block(self) -> None:
        block = desktop.MessageBlock("assistant", "")
        block.show()
        block.start_thinking()
        self.app.processEvents()
        self.assertFalse(block.thinking.isHidden())
        self.assertTrue(block.body.isHidden())

        host = SimpleNamespace(
            stream_block=block,
            transcript=SimpleNamespace(_scroll_later=lambda: None),
        )
        desktop.OnionmindWindow._append_stream(host, "Ready")
        self.app.processEvents()

        self.assertTrue(block.thinking.isHidden())
        self.assertFalse(block.body.isHidden())
        self.assertEqual(block.text, "Ready")
        self.assertEqual(block.body.text(), "Ready")
        self.assertEqual(block.accessibleName(), "Onionmind message")
        block.close()

    def test_pending_label_tracks_real_work_without_losing_thinking_state(self) -> None:
        block = desktop.MessageBlock("assistant", "")
        block.start_thinking("Starting Tor")
        block.set_pending_label("Thinking")
        self.assertEqual(block.thinking.label.text(), "Thinking")
        self.assertEqual(block.accessibleName(), "Onionmind is thinking")
        block.stop_thinking()

    def test_chat_stream_hides_later_thinking_tags_split_across_chunks(self) -> None:
        emitted: list[str] = []

        class Signal:
            def __init__(self, sink: list | None = None) -> None:
                self.sink = sink

            def emit(self, value) -> None:
                if self.sink is not None:
                    self.sink.append(value)

            def connect(self, callback) -> None:
                del callback

        signals = SimpleNamespace(
            text=Signal(emitted),
            event=Signal(),
            result=Signal(),
            error=Signal(),
        )

        def turn_stream(history, on_text, **kwargs):
            del history, kwargs
            for chunk in (
                "I will search.",
                "<thi",
                "nk>private",
                " reasoning</thi",
                "nk>Answer",
            ):
                on_text(chunk)
            return "Answer"

        core = SimpleNamespace(BACKEND="ollama", turn_stream=turn_stream)
        block = SimpleNamespace(start_thinking=lambda text: None)
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
            _start_worker=lambda job: SimpleNamespace(
                signals=signals,
                payload=job(signals),
            ),
            _append_stream=lambda text: None,
            _chat_event=lambda event: None,
            _chat_complete=lambda payload: None,
            _chat_failed=lambda message: None,
        )

        desktop.OnionmindWindow._start_chat(host)

        self.assertEqual(emitted, ["I will search.", "Answer"])


if __name__ == "__main__":
    unittest.main()
