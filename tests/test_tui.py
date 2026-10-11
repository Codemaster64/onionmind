"""Headless checks for the terminal front end (onionmind_tui.py).

Drives the real Textual app against a fake core - no model service, no
network. Skips when Textual is not installed.

    python tests/test_tui.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import textual  # noqa: F401
except ModuleNotFoundError:
    print("skip: textual is not installed (pip install textual)")
    raise SystemExit(0)

import onionmind as real_core
import onionmind_tui as tui


def fake_core(answer: str, fail: bool = False):
    """Just the core surface the TUI touches; turn_stream streams `answer`."""
    core = types.SimpleNamespace(MODEL="inferno", BACKEND="ollama", _port=None)
    core.calls = []
    core.strip_thinking = real_core.strip_thinking
    core.user_error = real_core.user_error
    core._save = real_core._save
    core.detect_backend = lambda: None
    core.installed_model_details = lambda: [{"name": "inferno:latest", "size": 1},
                                            {"name": "blaze:latest", "size": 1}]
    core.loaded_models = lambda: []
    core.tor_proxy_port = lambda: None
    core.tor_check = lambda: (_ for _ in ()).throw(AssertionError("tor_check at launch"))
    core.stop_managed_tor = lambda: None

    def start_tor_hidden(stop_event=None):
        core.calls.append("start_tor")
        return 9050
    core.start_tor_hidden = start_tor_hidden

    def turn_stream(messages, on_text, stop_event=None, on_event=None, allow_search=False):
        core.calls.append(("turn", core.MODEL, allow_search))
        if fail:
            raise SystemExit("No working Tor connection found.")
        for i in range(0, len(answer), 5):
            on_text(answer[i:i + 5])
        visible = real_core.strip_thinking(answer)
        messages.append({"role": "assistant", "content": visible})
        on_event({"kind": "stats", "eval_count": 10, "eval_duration": 1e9,
                  "tokens_per_second": 10.0})
        return visible
    core.turn_stream = turn_stream
    return core


async def chat_round_trip(data: Path) -> None:
    core = fake_core("<think>secret plan</think>Hello **there**.")
    app = tui.OnionmindTUI(core, data_root=data)
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause(0.3)
        assert app.online and app.query("#welcome-box"), "launch shows the welcome"
        await pilot.press(*"hi there", "enter")
        await app.workers.wait_for_complete()
        await pilot.pause(0.3)
        assert core.calls == [("turn", "inferno", False)], core.calls   # local unless armed
        screen = app.export_screenshot()
        assert "secret plan" not in screen, "private reasoning reached the screen"
        assert "Hello" in screen

        # Search is armed for exactly one turn, and starts Tor only then.
        await pilot.press("ctrl+s")
        assert app.search_armed
        await pilot.press(*"look it up", "enter")
        await app.workers.wait_for_complete()
        await pilot.pause(0.2)
        assert core.calls[1:] == ["start_tor", ("turn", "inferno", True)], core.calls
        assert not app.search_armed, "search must disarm after one turn"

        # /model switches the raw name the core sees; a label never does.
        await pilot.press(*"/model blaze", "enter")
        await pilot.pause(0.1)
        assert core.MODEL == "blaze"

    sessions = tui.desktop_core.SessionStore(data / "sessions").list()
    assert len(sessions) == 1 and sessions[0].title == "hi there"
    assert sessions[0].model == "blaze"
    assert "secret plan" not in str(sessions[0].messages)
    assert [m["role"] for m in sessions[0].messages] == ["user", "assistant"] * 2


async def failed_turn_leaves_no_half_history(data: Path) -> None:
    core = fake_core("", fail=True)
    app = tui.OnionmindTUI(core, data_root=data)
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause(0.3)
        await pilot.press(*"/search q", "enter")
        await app.workers.wait_for_complete()
        await pilot.pause(0.2)
        assert app.session.messages == [], app.session.messages
        assert app.query_one("#prompt").value == "q", "the question is handed back"
        notes = [str(n.render()) for n in app.query(tui.Note)]
        assert any("No working Tor connection" in n for n in notes), notes


def main() -> None:
    assert all(len(row) == len(tui.ONION[0]) for row in tui.ONION), "logo rows differ in width"
    assert tui.session_title("a " * 40, 10).endswith("…")
    assert tui.model_label("inferno") == "Qwen3.8 27B"
    with tempfile.TemporaryDirectory() as tmp:
        asyncio.run(chat_round_trip(Path(tmp) / "a"))
        asyncio.run(failed_turn_leaves_no_half_history(Path(tmp) / "b"))
    print("ok: tui")


if __name__ == "__main__":
    main()
