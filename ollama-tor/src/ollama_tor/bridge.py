"""Ephemeral loopback HTTP proxy whose only remote dialer is Tor."""

from __future__ import annotations

import ipaddress
import secrets
import socket
import socketserver
import threading
import urllib.parse
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from .errors import OllamaTorError
from .tor import open_socks_connection

_MAX_REQUEST_LINE = 16_384
_MAX_HEADER_BYTES = 65_536
_PROXY_ONLY_HEADERS = {b"proxy-authorization", b"proxy-connection"}


def _display(value: object) -> str:
    return " ".join(str(value).split())[:500]


class DestinationLog:
    """Best-effort destination log; it records hosts and ports, never URL paths."""

    def __init__(self, path: Path | None):
        self.path = path
        self._lock = threading.Lock()

    def record(self, route: str, host: str, port: int, detail: object = "") -> None:
        if self.path is None:
            return
        suffix = f" {_display(detail)}" if detail else ""
        timestamp = (
            datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        )
        line = f"{timestamp} {route:<7} {_display(host)}:{port}{suffix}\n"
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(line)
        except OSError:
            # Routing remains fail-closed if audit storage is unavailable. Logging
            # is intentionally best-effort so a full disk cannot break the proxy.
            return


def _loopback_host(host: str) -> str | None:
    candidate = host.strip().strip("[]")
    if candidate.lower().rstrip(".") == "localhost":
        return "127.0.0.1"
    if "%" in candidate:
        address, _, zone = candidate.partition("%")
        if zone and address == "::1":
            return "::1"
        return None
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return None
    return str(address) if address.is_loopback else None


class _TorDialer:
    def __init__(self, socks_port: int, log: DestinationLog, timeout: float):
        self.socks_port = socks_port
        self.log = log
        self.timeout = timeout

    def connect(self, host: str, port: int) -> socket.socket:
        local = _loopback_host(host)
        if local is not None:
            stream = socket.create_connection((local, port), self.timeout)
            stream.settimeout(None)
            self.log.record("local", host, port)
            return stream

        stream = open_socks_connection(
            self.socks_port,
            host,
            port,
            username=secrets.token_hex(16),
            password="ollama-tor",
            timeout=self.timeout,
        )
        stream.settimeout(None)
        self.log.record("tor", host, port)
        return stream


def _pipe(source: socket.socket, destination: socket.socket) -> None:
    try:
        while True:
            chunk = source.recv(65_536)
            if not chunk:
                break
            destination.sendall(chunk)
    except OSError:
        pass
    try:
        destination.shutdown(socket.SHUT_WR)
    except OSError:
        pass


def _authority(value: str, default_port: int) -> tuple[str, int]:
    try:
        parsed = urllib.parse.urlsplit("//" + value)
        host = parsed.hostname
        port = parsed.port or default_port
    except ValueError as exc:
        raise ValueError(f"invalid proxy destination {value!r}") from exc
    if not host or not 1 <= port <= 65535:
        raise ValueError(f"invalid proxy destination {value!r}")
    return host, port


def _header(headers: Iterable[bytes], wanted: bytes) -> str | None:
    for line in headers:
        name, separator, value = line.partition(b":")
        if separator and name.strip().lower() == wanted:
            return value.decode("latin1").strip()
    return None


def _forward_headers(headers: Iterable[bytes]) -> bytes:
    selected = []
    for line in headers:
        name = line.partition(b":")[0].strip().lower()
        if name not in _PROXY_ONLY_HEADERS:
            selected.append(line)
    return b"".join(selected) + b"\r\n"


class _ProxyHandler(socketserver.BaseRequestHandler):
    def _error(self, status: int, reason: str) -> None:
        try:
            self.request.sendall(
                f"HTTP/1.1 {status} {reason}\r\n"
                "Content-Length: 0\r\n"
                "Connection: close\r\n\r\n".encode("ascii")
            )
        except OSError:
            pass

    def _headers(self, reader: socket.SocketIO) -> list[bytes]:
        headers: list[bytes] = []
        total = 0
        while True:
            line = reader.readline(_MAX_REQUEST_LINE)
            if line in (b"\r\n", b"\n", b""):
                return headers
            total += len(line)
            if total > _MAX_HEADER_BYTES:
                raise ValueError("proxy request headers are too large")
            headers.append(line)

    def handle(self) -> None:
        client: socket.socket = self.request
        client.settimeout(None)
        reader = client.makefile("rb", buffering=0)
        request_line = reader.readline(_MAX_REQUEST_LINE)
        if not request_line:
            return
        parts = request_line.rstrip(b"\r\n").split()
        if len(parts) != 3:
            self._error(400, "Bad Request")
            return
        method_bytes, target_bytes, version = parts
        method = method_bytes.decode("ascii", errors="replace").upper()
        target = target_bytes.decode("latin1")

        try:
            headers = self._headers(reader)
            if method == "CONNECT":
                host, port = _authority(target, 443)
                first = b""
            else:
                parsed = urllib.parse.urlsplit(target)
                if parsed.scheme:
                    if parsed.scheme.lower() != "http" or not parsed.hostname:
                        raise ValueError("plain proxy requests must use an http URL")
                    host = parsed.hostname
                    port = parsed.port or 80
                    path = urllib.parse.urlunsplit(
                        ("", "", parsed.path or "/", parsed.query, "")
                    )
                else:
                    host_header = _header(headers, b"host")
                    if not target.startswith("/") or not host_header:
                        raise ValueError("the proxy request has no destination")
                    host, port = _authority(host_header, 80)
                    path = target
                first = (
                    method_bytes
                    + b" "
                    + path.encode("latin1")
                    + b" "
                    + version
                    + b"\r\n"
                    + _forward_headers(headers)
                )
        except (UnicodeError, ValueError):
            self._error(400, "Bad Request")
            return

        dialer: _TorDialer = self.server.dialer  # type: ignore[attr-defined]
        try:
            upstream = dialer.connect(host, port)
        except (OSError, ValueError, OllamaTorError) as exc:
            dialer.log.record("refused", host, port, exc)
            self._error(502, "Bad Gateway")
            return

        try:
            if method == "CONNECT":
                client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            else:
                upstream.sendall(first)
            download = threading.Thread(
                target=_pipe,
                args=(upstream, client),
                name="ollama-tor-download",
                daemon=True,
            )
            download.start()
            _pipe(client, upstream)
            download.join(timeout=1)
        finally:
            upstream.close()


class _ProxyServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


class TorBridge:
    """Context manager for one short-lived, loopback-only Tor HTTP proxy."""

    def __init__(
        self,
        socks_port: int,
        *,
        log_path: Path | None,
        connect_timeout: float = 30,
    ):
        self._log = DestinationLog(log_path)
        self._dialer = _TorDialer(socks_port, self._log, connect_timeout)
        self._server: _ProxyServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        if self._server is None:
            raise RuntimeError("the Tor bridge has not been started")
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def start(self) -> TorBridge:
        if self._server is not None:
            return self
        server = _ProxyServer(("127.0.0.1", 0), _ProxyHandler)
        server.dialer = self._dialer  # type: ignore[attr-defined]
        thread = threading.Thread(
            target=server.serve_forever,
            name="ollama-tor-proxy",
            daemon=True,
        )
        thread.start()
        self._server = server
        self._thread = thread
        return self

    def close(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._server = None
        self._thread = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.close()
