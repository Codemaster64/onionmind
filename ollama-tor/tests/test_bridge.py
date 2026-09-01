import http.server
import socket
import tempfile
from pathlib import Path
from typing import ClassVar

from helpers import EchoHandler, RunningServer

from ollama_tor.bridge import DestinationLog, TorBridge, _TorDialer


class CaptureHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    paths: ClassVar[list[str]] = []

    def do_GET(self):
        type(self).paths.append(self.path)
        body = self.path.encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def response_head(stream: socket.socket) -> bytes:
    data = bytearray()
    while b"\r\n\r\n" not in data:
        data.extend(stream.recv(4096))
    return bytes(data)


def test_plain_http_is_rewritten_and_forwarded_to_loopback():
    CaptureHandler.paths = []
    with (
        RunningServer(CaptureHandler) as origin,
        TorBridge(1, log_path=None) as bridge,
        socket.create_connection(
            ("127.0.0.1", int(bridge.url.rsplit(":", 1)[1]))
        ) as client,
    ):
        client.sendall(
            f"GET http://127.0.0.1:{origin.port}/hello?q=tor HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{origin.port}\r\n"
            "Proxy-Connection: keep-alive\r\n"
            "Connection: close\r\n\r\n".encode()
        )
        reply = bytearray()
        while True:
            chunk = client.recv(4096)
            if not chunk:
                break
            reply.extend(chunk)

    assert b"200 OK" in reply
    assert bytes(reply).endswith(b"/hello?q=tor")
    assert CaptureHandler.paths == ["/hello?q=tor"]


def test_connect_tunnel_can_reach_local_ollama_style_endpoint():
    with (
        RunningServer(EchoHandler) as origin,
        TorBridge(1, log_path=None) as bridge,
        socket.create_connection(
            ("127.0.0.1", int(bridge.url.rsplit(":", 1)[1]))
        ) as client,
    ):
        client.sendall(
            f"CONNECT 127.0.0.1:{origin.port} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{origin.port}\r\n\r\n".encode()
        )
        assert b"200 Connection Established" in response_head(client)
        client.sendall(b"local-ollama")
        assert client.recv(64) == b"local-ollama"


def test_remote_dial_uses_socks_credentials_instead_of_direct_connect(monkeypatch):
    left, right = socket.socketpair()
    seen = {}

    def fake_socks(socks_port, host, port, **options):
        seen.update(socks_port=socks_port, host=host, port=port, **options)
        return left

    monkeypatch.setattr("ollama_tor.bridge.open_socks_connection", fake_socks)
    dialer = _TorDialer(19050, DestinationLog(None), 7)
    stream = dialer.connect("example.com", 443)
    stream.close()
    right.close()

    assert seen["socks_port"] == 19050
    assert seen["host"] == "example.com"
    assert seen["port"] == 443
    assert seen["username"]
    assert seen["password"] == "ollama-tor"


def test_each_remote_connection_gets_distinct_circuit_credentials(monkeypatch):
    usernames = []
    peers = []

    def fake_socks(_socks_port, _host, _port, **options):
        left, right = socket.socketpair()
        peers.append(right)
        usernames.append(options["username"])
        return left

    monkeypatch.setattr("ollama_tor.bridge.open_socks_connection", fake_socks)
    dialer = _TorDialer(19050, DestinationLog(None), 7)
    first = dialer.connect("example.com", 443)
    second = dialer.connect("example.com", 443)
    first.close()
    second.close()
    for peer in peers:
        peer.close()

    assert len(usernames) == 2
    assert usernames[0] != usernames[1]


def test_failed_remote_dial_returns_502_without_fallback():
    with TorBridge(1, log_path=None) as bridge:
        bridge._dialer.connect = lambda *_args: (_ for _ in ()).throw(OSError("no Tor"))
        with socket.create_connection(
            ("127.0.0.1", int(bridge.url.rsplit(":", 1)[1]))
        ) as client:
            client.sendall(
                b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n"
            )
            assert b"502 Bad Gateway" in response_head(client)


def test_destination_log_excludes_url_paths():
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "network.log"
        log = DestinationLog(path)
        log.record("tor", "example.com", 443)
        contents = path.read_text(encoding="utf-8")
    assert "example.com:443" in contents
    assert "http" not in contents
