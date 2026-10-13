"""End to end: the real window over the real inference core and desktop core,
against a fake Ollama speaking HTTP on a loopback port.

Nothing between the composer and the socket is stubbed: model discovery
(/api/version, /api/tags), a streamed chat turn (/api/chat NDJSON) and the
reply landing in the transcript all run the shipped code. Agent runs need
qwen-code and a Tor circuit, so they are covered by the stream fixtures in
test_desktop_v2_ui.py instead.

Run: python -m unittest tests/test_e2e_smoke.py
"""

from __future__ import annotations

import http.server
import json
import os
from pathlib import Path
import sys
import threading
import time
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QStandardPaths
    from PySide6.QtWidgets import QApplication

    import onionmind
    import onionmind_desktop as ui
    import onionmind_desktop_core as desktop_core

    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False

REPLY = ("Hello ", "from the ", "fake engine.")


class FakeOllama(http.server.BaseHTTPRequestHandler):
    chats: list[dict] = []

    def _json(self, payload: dict) -> None:
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/api/version":
            self._json({"version": "0.0.0-fake"})
        elif self.path == "/api/tags":
            self._json({"models": [{"name": "inferno:latest", "size": 3 * 1024 ** 3}]})
        elif self.path == "/api/ps":
            self._json({"models": []})
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
        if self.path != "/api/chat":
            self._json({})
            return
        FakeOllama.chats.append(body)
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        for part in REPLY:
            self.wfile.write(json.dumps({"message": {"role": "assistant", "content": part},
                                         "done": False}).encode() + b"\n")
            self.wfile.flush()
        self.wfile.write(json.dumps({"message": {"role": "assistant", "content": ""}, "done": True,
                                     "done_reason": "stop", "eval_count": 3,
                                     "eval_duration": 30_000_000, "prompt_eval_count": 40}).encode() + b"\n")

    def log_message(self, *args) -> None:
        pass


@unittest.skipUnless(QT_AVAILABLE, "PySide6 desktop runtime is not installed")
class ChatEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        QStandardPaths.setTestModeEnabled(True)
        cls.app = QApplication.instance() or QApplication([])
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.saved = (onionmind.OLLAMA_PORT, onionmind.MODEL, onionmind.BACKEND)
        onionmind.use_ollama_port(cls.server.server_port)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        port, onionmind.MODEL, onionmind.BACKEND = cls.saved
        onionmind.use_ollama_port(port)

    def wait_until(self, predicate, seconds: float = 15) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.02)
        return bool(predicate())

    def test_discover_the_model_then_chat_through_the_real_stream(self) -> None:
        FakeOllama.chats.clear()
        window = ui.OnionmindWindow(onionmind, desktop_core, demo=True)
        window.save_current_session = lambda: True
        window.show()
        try:
            # A demo window (no settings file touched) skips the launch probe;
            # this is the same probe a real launch runs.
            window._probe_models()
            self.assertTrue(self.wait_until(lambda: window.model_status.label.text() == "Ready"),
                            window.status_label.text())
            self.assertEqual(onionmind.BACKEND, "ollama")
            window.set_mode("chat")
            before = len(window.chat_messages)
            window.composer.setPlainText("Say hello")
            window.submit()
            self.assertTrue(self.wait_until(lambda: window.active_kind is None and any(
                m.get("role") == "assistant" for m in window.chat_messages[before:])),
                window.status_label.text())
            reply = [m["content"] for m in window.chat_messages[before:] if m.get("role") == "assistant"][-1]
            self.assertIn("Hello from the fake engine.", reply)
            self.assertEqual(len(FakeOllama.chats), 1)
            sent = FakeOllama.chats[0]
            self.assertEqual(sent["model"], "inferno")
            self.assertTrue(sent["stream"])
            self.assertEqual(sent["messages"][-1]["content"], "Say hello")
            self.assertNotIn("tools", sent)            # no search was allowed this turn
        finally:
            window.close()
            window.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
