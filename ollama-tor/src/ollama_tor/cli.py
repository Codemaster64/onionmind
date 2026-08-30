"""Command-line interface for the standalone Ollama Tor companion."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .errors import OllamaTorError
from .runner import default_log_path, run_routed
from .tor import DEFAULT_PORTS, verify_tor


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ollama-tor",
        description=(
            "Run an Ollama Launch integration behind a verified, fail-closed Tor proxy."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "--tor-port",
        type=int,
        action="append",
        dest="tor_ports",
        metavar="PORT",
        help="Tor SOCKS5 port to try (repeatable; default: 9050 then 9150)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30,
        metavar="SECONDS",
        help="Tor verification and connection timeout (default: 30)",
    )
    log_group = parser.add_mutually_exclusive_group()
    log_group.add_argument(
        "--log-file",
        type=Path,
        metavar="PATH",
        help="destination log path (default: platform state directory)",
    )
    log_group.add_argument(
        "--no-log",
        action="store_true",
        help="disable the destination log",
    )

    commands = parser.add_subparsers(dest="mode", required=True)
    commands.add_parser("check", help="verify that a configured SOCKS listener is Tor")

    launch = commands.add_parser(
        "launch",
        help="run `ollama launch ...` through Tor",
        description="All remaining arguments are passed to `ollama launch`.",
    )
    launch.add_argument("arguments", nargs=argparse.REMAINDER)

    execute = commands.add_parser(
        "exec",
        help="run any Ollama-backed command through Tor",
        description="Use `--` before the command when its options are ambiguous.",
    )
    execute.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def _strip_separator(values: Sequence[str]) -> list[str]:
    result = list(values)
    if result and result[0] == "--":
        result.pop(0)
    return result


def _ports(
    parsed: argparse.Namespace, parser: argparse.ArgumentParser
) -> tuple[int, ...]:
    raw_ports = parsed.tor_ports
    if raw_ports is None and os.environ.get("OLLAMA_TOR_PORT"):
        try:
            raw_ports = [
                int(value.strip()) for value in os.environ["OLLAMA_TOR_PORT"].split(",")
            ]
        except ValueError:
            parser.error("OLLAMA_TOR_PORT must contain one or more numeric ports")
    ports = tuple(DEFAULT_PORTS if raw_ports is None else raw_ports)
    if not ports or any(port < 1 or port > 65535 for port in ports):
        parser.error("Tor ports must be between 1 and 65535")
    return ports


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    parsed = parser.parse_args(argv)
    if parsed.timeout <= 0:
        parser.error("--timeout must be greater than zero")
    ports = _ports(parsed, parser)

    try:
        if parsed.mode == "check":
            status = verify_tor(ports, timeout=parsed.timeout)
            print(f"Tor verified on 127.0.0.1:{status.port} (exit {status.exit_ip})")
            return 0

        if parsed.mode == "launch":
            arguments = _strip_separator(parsed.arguments)
            if not arguments:
                parser.error("launch requires an Ollama integration name")
            command = ["ollama", "launch", *arguments]
        else:
            command = _strip_separator(parsed.command)
            if not command:
                parser.error("exec requires a command")

        log_path = None if parsed.no_log else (parsed.log_file or default_log_path())
        return run_routed(
            command,
            ports=ports,
            timeout=parsed.timeout,
            log_path=log_path,
        )
    except OllamaTorError as exc:
        print(f"ollama-tor: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nollama-tor: interrupted", file=sys.stderr)
        return 130
