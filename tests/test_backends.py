"""The llama-server backend adapter, exercised against a mock server.

Validates what the Android port depends on: OpenAI-format translation in both
directions - positional tool_call ids out, string-encoded tool arguments in -
and one full search turn through the real loop with web_search stubbed.

Run: python3 tests/test_backends.py   (needs: requests)
"""
import json, threading, http.server, sys, pathlib

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
    srv = http.server.HTTPServer(("127.0.0.1", 0), Mock)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    onionmind.LLAMA = f"http://127.0.0.1:{srv.server_port}/v1/chat/completions"
    onionmind.BACKEND = "llama-server"
    onionmind.web_search = lambda q, n=5: f"- stubbed result for {q!r}"   # no tor here

    answer = onionmind.turn([{"role": "user", "content": "search something"}])
    assert "All over it." in answer, answer

    # the second request must carry the history translated to OpenAI shape
    wire = captured[1]
    roles = [m["role"] for m in wire["messages"]]
    assert roles == ["user", "assistant", "tool"], roles
    tool_msg = wire["messages"][2]
    assert tool_msg["tool_call_id"] == "tc0", tool_msg   # positional, self-consistent
    assistant = wire["messages"][1]
    assert assistant["tool_calls"][0]["function"]["arguments"] == '{"query": "test q"}'
    print("llama-server adapter OK: translation, string-args, positional ids, full turn")


if __name__ == "__main__":
    test_llama_backend()
    print("DONE_BACKEND_OK")
