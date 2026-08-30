from ollama_tor import cli
from ollama_tor.errors import TorUnavailable
from ollama_tor.tor import TorStatus


def test_check_reports_verified_port(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "verify_tor", lambda ports, timeout: TorStatus(9150, "1.2.3.4")
    )
    assert cli.main(["--tor-port", "9150", "check"]) == 0
    assert "Tor verified on 127.0.0.1:9150 (exit 1.2.3.4)" in capsys.readouterr().out


def test_launch_prefixes_the_supported_ollama_command(monkeypatch, tmp_path):
    seen = {}

    def run(command, **options):
        seen.update(command=command, **options)
        return 17

    monkeypatch.setattr(cli, "run_routed", run)
    result = cli.main(
        [
            "--tor-port",
            "19050",
            "--timeout",
            "8",
            "--log-file",
            str(tmp_path / "net.log"),
            "launch",
            "codex",
            "--model",
            "qwen",
        ]
    )
    assert result == 17
    assert seen["command"] == ["ollama", "launch", "codex", "--model", "qwen"]
    assert seen["ports"] == (19050,)
    assert seen["timeout"] == 8
    assert seen["log_path"] == tmp_path / "net.log"


def test_exec_strips_the_option_separator_and_can_disable_logging(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        cli,
        "run_routed",
        lambda command, **options: seen.update(command=command, **options) or 0,
    )
    assert cli.main(["--no-log", "exec", "--", "agent", "--private"]) == 0
    assert seen["command"] == ["agent", "--private"]
    assert seen["log_path"] is None


def test_tor_failure_is_a_clean_nonzero_result(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "verify_tor",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TorUnavailable("not Tor")),
    )
    assert cli.main(["check"]) == 2
    assert "ollama-tor: not Tor" in capsys.readouterr().err


def test_environment_can_supply_multiple_ports(monkeypatch):
    seen = {}
    monkeypatch.setenv("OLLAMA_TOR_PORT", "19050, 19051")
    monkeypatch.setattr(
        cli,
        "verify_tor",
        lambda ports, timeout: seen.update(ports=ports) or TorStatus(19051, "x"),
    )
    assert cli.main(["check"]) == 0
    assert seen["ports"] == (19050, 19051)
