import io
from typing import ClassVar

import pytest

from ollama_tor import runner
from ollama_tor.errors import TorUnavailable
from ollama_tor.tor import TorStatus


class FakeBridge:
    created: ClassVar[list] = []

    def __init__(self, port, *, log_path, connect_timeout):
        self.port = port
        self.log_path = log_path
        self.connect_timeout = connect_timeout
        self.url = "http://127.0.0.1:19080"
        type(self).created.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        pass


def test_runner_verifies_before_building_environment_or_spawning(monkeypatch):
    called = []
    monkeypatch.setattr(
        runner,
        "verify_tor",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TorUnavailable("no Tor")),
    )
    monkeypatch.setattr(
        runner.subprocess, "call", lambda *_args, **_kwargs: called.append(True)
    )

    with pytest.raises(TorUnavailable, match="no Tor"):
        runner.run_routed(
            ["ollama", "launch", "codex"],
            ports=(9050,),
            timeout=3,
            log_path=None,
        )
    assert called == []


def test_runner_passes_routed_environment_to_exact_child_command(monkeypatch, tmp_path):
    FakeBridge.created = []
    seen = {}
    monkeypatch.setattr(
        runner, "verify_tor", lambda *_a, **_kw: TorStatus(9150, "x.x.x.x")
    )
    monkeypatch.setattr(runner, "TorBridge", FakeBridge)
    monkeypatch.setattr(
        runner,
        "routed_environment",
        lambda proxy, port, **kwargs: {
            "proxy": proxy,
            "port": str(port),
            "base": str(kwargs["base"]["BASE"]),
        },
    )
    monkeypatch.setattr(
        runner.subprocess,
        "call",
        lambda command, env: seen.update(command=command, env=env) or 23,
    )
    output = io.StringIO()

    result = runner.run_routed(
        ["ollama", "launch", "codex", "--model", "local"],
        ports=(9150,),
        timeout=7,
        log_path=tmp_path / "network.log",
        environment={"BASE": "kept"},
        stderr=output,
    )

    assert result == 23
    assert seen["command"] == ["ollama", "launch", "codex", "--model", "local"]
    assert seen["env"] == {
        "proxy": "http://127.0.0.1:19080",
        "port": "9150",
        "base": "kept",
    }
    assert FakeBridge.created[0].connect_timeout == 7
    assert "verified Tor" in output.getvalue()


def test_missing_child_command_returns_shell_style_127(monkeypatch):
    monkeypatch.setattr(runner, "verify_tor", lambda *_a, **_kw: TorStatus(9150, "x"))
    monkeypatch.setattr(runner, "TorBridge", FakeBridge)
    monkeypatch.setattr(runner, "routed_environment", lambda *_a, **_kw: {})
    monkeypatch.setattr(
        runner.subprocess,
        "call",
        lambda *_a, **_kw: (_ for _ in ()).throw(FileNotFoundError()),
    )
    output = io.StringIO()
    assert (
        runner.run_routed(
            ["missing-agent"], ports=(9150,), timeout=1, log_path=None, stderr=output
        )
        == 127
    )
    assert "command not found: missing-agent" in output.getvalue()
