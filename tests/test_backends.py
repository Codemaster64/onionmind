"""The llama-server backend adapter, exercised against a mock server.

Validates what the Android port depends on: OpenAI-format translation in both
directions - positional tool_call ids out, string-encoded tool arguments in -
and one full search turn through the real loop with web_search stubbed.

Run: python3 tests/test_backends.py   (needs: requests)
"""
import json, threading, http.server, sys, pathlib
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import onionmind

ROUNDS = [
    # round 1: the model wants to search. OpenAI ships arguments as a JSON *string*
    {"choices": [{"message": {"role": "assistant", "content": None,
                              "tool_calls": [{"id": "call_abc", "type": "function",
                                              "function": {"name": "web_search",
                                                           "arguments": '{"query": "test q"}'}}]}}]},
    # round 2: the final answer, behind thinking tokens like the real models
    {"choices": [{"message": {"role": "assistant",
                              "content": "<think>hmm</think>All over it."}}]},
]
captured = []


def api_response(payload):
    response = mock.Mock(status_code=200, ok=True)
    response.json.return_value = payload
    return response


class Mock(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        captured.append(body)
        data = json.dumps(ROUNDS[len(captured) - 1]).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def test_llama_backend():
    captured.clear()
    srv = http.server.HTTPServer(("127.0.0.1", 0), Mock)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    old_llama = onionmind.LLAMA
    old_backend = onionmind.BACKEND
    old_search = onionmind.web_search
    old_check = onionmind.tor_check
    try:
        onionmind.LLAMA = f"http://127.0.0.1:{srv.server_port}/v1/chat/completions"
        onionmind.BACKEND = "llama-server"
        onionmind.tor_check = lambda: None
        onionmind.web_search = lambda q, n=5: f"- stubbed result for {q!r}"   # no tor here

        answer = onionmind.turn([{"role": "user", "content": "search something"}],
                                allow_search=True)
        assert "All over it." in answer, answer
        assert captured[0]["max_tokens"] > 8192, captured[0]["max_tokens"]

        # the second request must carry the history translated to OpenAI shape
        wire = captured[1]
        roles = [m["role"] for m in wire["messages"]]
        assert roles == ["user", "assistant", "tool"], roles
        tool_msg = wire["messages"][2]
        assert tool_msg["tool_call_id"] == "tc0", tool_msg   # positional, self-consistent
        assistant = wire["messages"][1]
        assert assistant["tool_calls"][0]["function"]["arguments"] == '{"query": "test q"}'
    finally:
        onionmind.LLAMA = old_llama
        onionmind.BACKEND = old_backend
        onionmind.web_search = old_search
        onionmind.tor_check = old_check
    print("llama-server adapter OK: translation, string-args, positional ids, full turn")


def test_ollama_reasoning_budget_exceeds_old_ceiling():
    response = api_response({"message": {"role": "assistant", "content": "OK"}})
    with mock.patch.object(onionmind.requests, "post", return_value=response) as post:
        onionmind._ask_ollama([{"role": "user", "content": "hello"}])
    wire = post.call_args.kwargs["json"]
    assert wire["options"]["num_predict"] > 8192, wire["options"]["num_predict"]


def test_ollama_exhaustion_is_compressed_and_preserved():
    responses = [
        api_response({
            "message": {"role": "assistant", "content": "", "thinking": "worked out facts"},
            "done_reason": "length",
        }),
        api_response({
            "message": {"role": "assistant", "content": "The compact best-effort answer."},
            "done_reason": "stop",
        }),
    ]
    history = [{"role": "user", "content": "Solve this hard task"}]
    with mock.patch.object(onionmind, "BACKEND", "ollama"), \
         mock.patch.object(onionmind.requests, "post", side_effect=responses) as post:
        answer = onionmind.turn(history)

    assert answer == "The compact best-effort answer.", answer
    assert post.call_count == 2, post.call_count
    retry = post.call_args_list[1].kwargs["json"]
    assert retry["think"] is False, retry
    assert retry["options"]["num_predict"] < onionmind.NUM_PREDICT, retry
    assert any(m.get("thinking") == "worked out facts" for m in retry["messages"]), retry
    assert "best-effort" in retry["messages"][-1]["content"].lower(), retry
    assert history == [
        {"role": "user", "content": "Solve this hard task"},
        {"role": "assistant", "content": answer},
    ], history


def test_llama_exhaustion_continues_from_reasoning_content():
    responses = [
        api_response({"choices": [{
            "finish_reason": "length",
            "message": {"role": "assistant", "content": None,
                        "reasoning_content": "derived the important state"},
        }]}),
        api_response({"choices": [{
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": "Recovered from the saved state."},
        }]}),
    ]
    history = [{"role": "user", "content": "Continue carefully"}]
    with mock.patch.object(onionmind, "BACKEND", "llama-server"), \
         mock.patch.object(onionmind.requests, "post", side_effect=responses) as post:
        answer = onionmind.turn(history)

    assert answer == "Recovered from the saved state.", answer
    assert post.call_count == 2, post.call_count
    retry = post.call_args_list[1].kwargs["json"]
    assert retry["max_tokens"] < onionmind.NUM_PREDICT, retry
    assert retry["chat_template_kwargs"]["enable_thinking"] is False, retry
    assert any(m.get("reasoning_content") == "derived the important state"
               for m in retry["messages"]), retry
    assert history[-1] == {"role": "assistant", "content": answer}, history


def test_failed_recovery_keeps_a_marked_partial_answer():
    responses = [
        api_response({
            "message": {"role": "assistant",
                        "content": "<think>done</think>Useful partial result"},
            "done_reason": "length",
        }),
        api_response({
            "message": {"role": "assistant", "content": "", "thinking": "ran out again"},
            "done_reason": "length",
        }),
    ]
    history = [{"role": "user", "content": "Large request"}]
    with mock.patch.object(onionmind, "BACKEND", "ollama"), \
         mock.patch.object(onionmind.requests, "post", side_effect=responses) as post:
        answer = onionmind.turn(history)

    assert post.call_count == 2, post.call_count
    assert "Useful partial result" in answer, answer
    assert "incomplete" in answer.lower(), answer
    assert history[-1] == {"role": "assistant", "content": answer}, history


def test_double_exhaustion_keeps_resume_state_for_next_turn():
    responses = [
        api_response({
            "message": {"role": "assistant", "content": "", "thinking": "saved first-pass state"},
            "done_reason": "length",
        }),
        api_response({
            "message": {"role": "assistant", "content": "", "thinking": "retry also exhausted"},
            "done_reason": "length",
        }),
    ]
    history = [{"role": "user", "content": "Very large request"}]
    with mock.patch.object(onionmind, "BACKEND", "ollama"), \
         mock.patch.object(onionmind.requests, "post", side_effect=responses):
        answer = onionmind.turn(history)

    assert "unfinished state is saved" in answer.lower(), answer
    assert len(history) == 2, history
    assert history[-1]["thinking"] == "saved first-pass state", history
    assert onionmind._wire_messages(history)[-1]["thinking"] == "saved first-pass state"


def test_stream_exhaustion_recovers_and_compacts_history():
    seen = []

    def stream_reply(messages, on_text, *_args, **kwargs):
        seen.append((list(messages), kwargs))
        if len(seen) == 1:
            on_text("<think>unfinished work")
            return {"role": "assistant", "content": "<think>unfinished work",
                    "_done_reason": "length"}
        on_text("Streamed compact answer.")
        return {"role": "assistant", "content": "Streamed compact answer.",
                "_done_reason": "stop"}

    history = [{"role": "user", "content": "Long streaming task"}]
    with mock.patch.object(onionmind, "BACKEND", "ollama"), \
         mock.patch.object(onionmind, "_ask_ollama_stream", side_effect=stream_reply) as ask:
        answer = onionmind.turn_stream(history, lambda _chunk: None)

    assert answer == "Streamed compact answer.", answer
    assert ask.call_count == 2, ask.call_count
    assert seen[1][1]["think"] is False, seen
    assert history == [
        {"role": "user", "content": "Long streaming task"},
        {"role": "assistant", "content": answer},
    ], history


def test_ollama_stream_retains_cutoff_reasoning_and_reason():
    response = mock.Mock(status_code=200, ok=True)
    response.iter_lines.return_value = [
        json.dumps({"message": {"thinking": "saved "}, "done": False}),
        json.dumps({"message": {"thinking": "state"}, "done": True,
                    "done_reason": "length"}),
    ]
    with mock.patch.object(onionmind.requests, "post", return_value=response):
        message = onionmind._ask_ollama_stream(
            [{"role": "user", "content": "hard task"}], lambda _chunk: None)
    assert message["thinking"] == "saved state", message
    assert message["_done_reason"] == "length", message
    response.close.assert_called_once_with()


def test_stream_reports_tool_activity():
    """Native clients get semantic activity without parsing assistant prose."""
    replies = iter([
        {"role": "assistant", "content": "", "tool_calls": [{
            "function": {"name": "web_search", "arguments": {"query": "onions"}}
        }]},
        {"role": "assistant", "content": "Found it."},
    ])
    old_backend = onionmind.BACKEND
    old_stream = onionmind._ask_ollama_stream
    old_search = onionmind.web_search
    old_check = onionmind.tor_check
    try:
        onionmind.BACKEND = "ollama"
        onionmind._ask_ollama_stream = lambda *_args, **_kwargs: next(replies)
        onionmind.web_search = lambda query, n=5: f"result for {query}"
        onionmind.tor_check = lambda: setattr(onionmind, "_port", 9150)
        events = []
        answer = onionmind.turn_stream(
            [{"role": "user", "content": "search"}], lambda _chunk: None,
            on_event=events.append, allow_search=True)
        assert answer == "Found it.", answer
        assert [event["kind"] for event in events] == [
            "tool_started", "tor_verified", "tool_finished"], events
        assert events[0]["arguments"] == {"query": "onions"}, events
        assert events[1]["port"] == 9150, events
        assert events[2]["result"] == "result for onions", events
    finally:
        onionmind.BACKEND = old_backend
        onionmind._ask_ollama_stream = old_stream
        onionmind.web_search = old_search
        onionmind.tor_check = old_check


def test_search_tools_are_absent_without_explicit_permission():
    response = mock.Mock(status_code=200, ok=True)
    response.json.return_value = {"message": {"role": "assistant", "content": "local"}}
    with mock.patch.object(onionmind.requests, "post", return_value=response) as post:
        onionmind._ask_ollama([{"role": "user", "content": "hello"}])
        local_payload = post.call_args.kwargs["json"]
        assert "tools" not in local_payload
        assert onionmind.NUM_PREDICT > 8192
        assert local_payload["options"]["num_predict"] == onionmind.NUM_PREDICT
        assert local_payload["options"]["num_ctx"] >= onionmind.NUM_PREDICT
        onionmind._ask_ollama(
            [{"role": "user", "content": "hello"}], allow_search=True
        )
        assert post.call_args.kwargs["json"]["tools"] == onionmind.TOOLS


def test_spurious_search_call_is_refused_without_network():
    replies = iter([
        {"role": "assistant", "content": "", "tool_calls": [{
            "function": {"name": "web_search", "arguments": {"query": "private"}}
        }]},
        {"role": "assistant", "content": "Stayed local."},
    ])
    events = []
    with mock.patch.object(onionmind, "BACKEND", "ollama"), \
         mock.patch.object(onionmind, "_ask_ollama_stream", side_effect=lambda *_a, **_kw: next(replies)), \
         mock.patch.object(onionmind, "tor_check") as check, \
         mock.patch.object(onionmind, "web_search") as search:
        answer = onionmind.turn_stream(
            [{"role": "user", "content": "stay local"}], lambda _chunk: None,
            on_event=events.append,
        )
    assert answer == "Stayed local."
    check.assert_not_called()
    search.assert_not_called()
    assert [event["kind"] for event in events] == ["tool_refused"]


def test_old_python_uses_legacy_ui_without_importing_native_module():
    legacy = mock.Mock(return_value="legacy")
    with mock.patch.object(onionmind.sys, "version_info", (3, 9, 18)), \
         mock.patch.object(onionmind, "run_legacy_ui", legacy), \
         mock.patch.dict(sys.modules, {"onionmind_desktop": None}):
        assert onionmind.run_ui() == "legacy"
    legacy.assert_called_once_with()


if __name__ == "__main__":
    test_llama_backend()
    test_ollama_reasoning_budget_exceeds_old_ceiling()
    test_ollama_exhaustion_is_compressed_and_preserved()
    test_llama_exhaustion_continues_from_reasoning_content()
    test_failed_recovery_keeps_a_marked_partial_answer()
    test_double_exhaustion_keeps_resume_state_for_next_turn()
    test_stream_exhaustion_recovers_and_compacts_history()
    test_ollama_stream_retains_cutoff_reasoning_and_reason()
    test_stream_reports_tool_activity()
    test_search_tools_are_absent_without_explicit_permission()
    test_spurious_search_call_is_refused_without_network()
    test_old_python_uses_legacy_ui_without_importing_native_module()
    print("DONE_BACKEND_OK")
