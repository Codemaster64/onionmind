"""Minimal SOCKS5 client and fail-closed Tor verification."""

from __future__ import annotations

import http.client
import ipaddress
import json
import os
import socket
import ssl
import struct
from collections.abc import Iterable
from dataclasses import dataclass

from .errors import SocksProtocolError, TorUnavailable

DEFAULT_PORTS = (9050, 9150)
CHECK_HOST = "check.torproject.org"
CHECK_PATH = "/api/ip"
_SOCKS_ERRORS = {
    1: "general SOCKS server failure",
    2: "connection not allowed by the SOCKS ruleset",
    3: "network unreachable",
    4: "host unreachable",
    5: "connection refused",
    6: "TTL expired",
    7: "SOCKS command not supported",
    8: "SOCKS address type not supported",
}


@dataclass(frozen=True)
class TorStatus:
    port: int
    exit_ip: str


def _read_exact(stream: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = stream.recv(size - len(chunks))
        if not chunk:
            raise SocksProtocolError("the SOCKS listener closed the connection early")
        chunks.extend(chunk)
    return bytes(chunks)


def _socks_address(host: str) -> bytes:
    normalized = host.strip().strip("[]")
    if not normalized:
        raise SocksProtocolError("a destination host is required")
    if "%" in normalized:
        raise SocksProtocolError("scoped IPv6 destinations are not supported by Tor")
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        try:
            encoded = normalized.encode("idna")
        except UnicodeError as exc:
            raise SocksProtocolError(f"invalid destination host: {host!r}") from exc
        if len(encoded) > 255:
            raise SocksProtocolError("the destination hostname is too long for SOCKS5")
        # ATYP 3 leaves hostname resolution to Tor. Resolving it locally first
        # would reveal the destination to the machine's normal DNS resolver.
        return b"\x03" + bytes([len(encoded)]) + encoded
    if address.version == 4:
        return b"\x01" + address.packed
    return b"\x04" + address.packed


def _discard_bound_address(stream: socket.socket, address_type: int) -> None:
    if address_type == 1:
        _read_exact(stream, 4)
    elif address_type == 4:
        _read_exact(stream, 16)
    elif address_type == 3:
        _read_exact(stream, _read_exact(stream, 1)[0])
    else:
        raise SocksProtocolError(f"invalid SOCKS5 address type {address_type}")
    _read_exact(stream, 2)


def open_socks_connection(
    socks_port: int,
    host: str,
    port: int,
    *,
    username: str | None = None,
    password: str = "",
    timeout: float = 30,
) -> socket.socket:
    """Connect to ``host:port`` through a loopback Tor SOCKS5 listener.

    Supplying a username forces RFC 1929 authentication. Tor accepts arbitrary
    credentials and uses distinct values to isolate streams onto fresh circuits.
    """

    if not 1 <= int(socks_port) <= 65535:
        raise SocksProtocolError(f"invalid SOCKS port: {socks_port}")
    if not 1 <= int(port) <= 65535:
        raise SocksProtocolError(f"invalid destination port: {port}")

    stream = socket.create_connection(("127.0.0.1", int(socks_port)), timeout)
    stream.settimeout(timeout)
    try:
        auth_method = b"\x02" if username is not None else b"\x00"
        stream.sendall(b"\x05\x01" + auth_method)
        version, selected = _read_exact(stream, 2)
        if version != 5:
            raise SocksProtocolError("the listener is not a SOCKS5 proxy")
        if selected == 0xFF:
            raise SocksProtocolError("the SOCKS5 listener rejected authentication")
        if selected != auth_method[0]:
            raise SocksProtocolError(
                f"the SOCKS5 listener selected unexpected auth method {selected}"
            )

        if username is not None:
            user_bytes = username.encode("utf-8")
            password_bytes = password.encode("utf-8")
            if not user_bytes or len(user_bytes) > 255 or len(password_bytes) > 255:
                raise SocksProtocolError("SOCKS5 credentials must be 1-255 bytes")
            stream.sendall(
                b"\x01"
                + bytes([len(user_bytes)])
                + user_bytes
                + bytes([len(password_bytes)])
                + password_bytes
            )
            auth_version, auth_status = _read_exact(stream, 2)
            if auth_version != 1 or auth_status != 0:
                raise SocksProtocolError(
                    "the SOCKS5 listener rejected circuit credentials"
                )

        stream.sendall(
            b"\x05\x01\x00" + _socks_address(host) + struct.pack("!H", int(port))
        )
        reply_version, reply, reserved, address_type = _read_exact(stream, 4)
        if reply_version != 5 or reserved != 0:
            raise SocksProtocolError("the listener returned an invalid SOCKS5 reply")
        if reply != 0:
            detail = _SOCKS_ERRORS.get(reply, f"unknown SOCKS error {reply}")
            raise SocksProtocolError(detail)
        _discard_bound_address(stream, address_type)
        return stream
    except BaseException:
        stream.close()
        raise


def _probe_port(port: int, timeout: float) -> TorStatus | None:
    raw = open_socks_connection(port, CHECK_HOST, 443, timeout=timeout)
    context = ssl.create_default_context()
    try:
        secure = context.wrap_socket(raw, server_hostname=CHECK_HOST)
    except BaseException:
        raw.close()
        raise
    try:
        request = (
            f"GET {CHECK_PATH} HTTP/1.1\r\n"
            f"Host: {CHECK_HOST}\r\n"
            "Accept: application/json\r\n"
            "Accept-Encoding: identity\r\n"
            "Connection: close\r\n"
            "User-Agent: ollama-tor/0.1\r\n\r\n"
        )
        secure.sendall(request.encode("ascii"))
        response = http.client.HTTPResponse(secure)
        response.begin()
        body = response.read(65_536)
        if response.status != 200:
            raise TorUnavailable(f"Tor check returned HTTP {response.status}")
        payload = json.loads(body.decode("utf-8"))
        if payload.get("IsTor") is not True:
            return None
        return TorStatus(port=port, exit_ip=str(payload.get("IP") or "unknown"))
    finally:
        secure.close()


def verify_tor(
    ports: Iterable[int] = DEFAULT_PORTS,
    *,
    timeout: float = 30,
) -> TorStatus:
    """Return the first verified Tor listener, or fail without spawning a child."""

    attempted: list[str] = []
    for raw_port in dict.fromkeys(ports):
        try:
            port = int(raw_port)
            status = _probe_port(port, timeout)
        except (
            OSError,
            ValueError,
            UnicodeError,
            http.client.HTTPException,
            TorUnavailable,
        ) as exc:
            attempted.append(f"{raw_port}: {type(exc).__name__}: {exc}")
            continue
        except SocksProtocolError as exc:
            attempted.append(f"{raw_port}: {exc}")
            continue
        if status is not None:
            return status
        attempted.append(f"{port}: listener answered, but the check was not Tor")

    if os.name == "nt":
        guidance = (
            "Start Tor Browser, click Connect, and leave it open (normally port 9150)."
        )
    else:
        guidance = (
            "Start Tor Browser (normally port 9150) or a tor daemon "
            "(normally port 9050)."
        )
    details = "; ".join(attempted) if attempted else "no SOCKS ports were configured"
    raise TorUnavailable(f"No verified Tor proxy. {guidance} Tried: {details}")
