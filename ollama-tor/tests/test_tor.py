import pytest
from helpers import FakeSocks

from ollama_tor import tor
from ollama_tor.errors import SocksProtocolError, TorUnavailable
from ollama_tor.tor import TorStatus, open_socks_connection, verify_tor


def test_socks_connection_sends_hostname_and_circuit_credentials_to_proxy():
    with FakeSocks() as proxy:
        stream = open_socks_connection(
            proxy.port,
            "example.com",
            443,
            username="fresh-circuit",
            password="x",
        )
        stream.close()
        assert proxy.event.wait(1)

    assert proxy.requests == [
        {
            "host": "example.com",
            "port": 443,
            "username": "fresh-circuit",
            "password": "x",
        }
    ]


def test_socks_connection_uses_address_types_for_ip_literals():
    with FakeSocks() as proxy:
        stream = open_socks_connection(proxy.port, "203.0.113.9", 80)
        stream.close()
        assert proxy.event.wait(1)
    assert proxy.requests[0]["host"] == "203.0.113.9"


def test_socks_failure_never_returns_a_direct_socket():
    with (
        FakeSocks(reply=5) as proxy,
        pytest.raises(SocksProtocolError, match="connection refused"),
    ):
        open_socks_connection(proxy.port, "example.com", 443)


def test_verify_tor_tries_ports_in_order(monkeypatch):
    attempted = []

    def probe(port, timeout):
        attempted.append((port, timeout))
        if port == 19051:
            return TorStatus(port, "198.51.100.7")
        raise OSError("closed")

    monkeypatch.setattr(tor, "_probe_port", probe)
    assert verify_tor((19050, 19051), timeout=4) == TorStatus(19051, "198.51.100.7")
    assert attempted == [(19050, 4), (19051, 4)]


def test_verify_tor_tries_the_next_port_after_a_failed_check(monkeypatch):
    attempted = []

    def probe(port, _timeout):
        attempted.append(port)
        if port == 19050:
            raise TorUnavailable("check endpoint unavailable")
        return TorStatus(port, "198.51.100.8")

    monkeypatch.setattr(tor, "_probe_port", probe)
    assert verify_tor((19050, 19051)).port == 19051
    assert attempted == [19050, 19051]


def test_verify_tor_refuses_an_unverified_listener(monkeypatch):
    monkeypatch.setattr(tor, "_probe_port", lambda _port, _timeout: None)
    with pytest.raises(
        TorUnavailable, match="listener answered, but the check was not Tor"
    ):
        verify_tor((19050,))


def test_verify_tor_refuses_an_empty_port_list():
    with pytest.raises(TorUnavailable, match="no SOCKS ports were configured"):
        verify_tor(())


def test_invalid_destination_is_rejected_before_the_socks_request():
    with pytest.raises(SocksProtocolError, match="invalid destination port"):
        open_socks_connection(9050, "example.com", 0)
