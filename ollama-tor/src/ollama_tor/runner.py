"""Launch one process tree behind the Tor routing module."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TextIO

from .bridge import TorBridge
from .containment import routed_environment
from .tor import verify_tor


def default_log_path(environment: Mapping[str, str] | None = None) -> Path:
    values = os.environ if environment is None else environment
    if os.name == "nt":
        root = values.get("LOCALAPPDATA")
        base = Path(root) if root else Path.home() / "AppData" / "Local"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Logs"
    else:
        root = values.get("XDG_STATE_HOME")
        base = Path(root) if root else Path.home() / ".local" / "state"
    return base / "ollama-tor" / "network.log"


def run_routed(
    command: Sequence[str],
    *,
    ports: Sequence[int],
    timeout: float,
    log_path: Path | None,
    environment: Mapping[str, str] | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Verify Tor, start the bridge, and run ``command`` with routed children."""

    if not command:
        raise ValueError("a child command is required")
    output = sys.stderr if stderr is None else stderr
    status = verify_tor(ports, timeout=timeout)
    print(
        f"[ollama-tor] verified Tor on 127.0.0.1:{status.port} (exit {status.exit_ip})",
        file=output,
    )

    with TorBridge(
        status.port,
        log_path=log_path,
        connect_timeout=timeout,
    ) as bridge:
        child_environment = routed_environment(
            bridge.url,
            status.port,
            log_path=log_path,
            base=environment,
        )
        print(
            "[ollama-tor] remote HTTP(S) uses Tor; direct Python/Node sockets "
            "are blocked",
            file=output,
        )
        if log_path is not None:
            print(f"[ollama-tor] destination log: {log_path}", file=output)
        try:
            return subprocess.call(list(command), env=child_environment)
        except FileNotFoundError:
            print(f"ollama-tor: command not found: {command[0]}", file=output)
            return 127
        except OSError as exc:
            print(f"ollama-tor: could not launch {command[0]}: {exc}", file=output)
            return 126
