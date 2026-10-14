"""Remote compute: the model runs on another machine reached over SSH.

Part of onionmind_desktop_core, which re-exports every name defined here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
import re
import shutil
import subprocess
from typing import Any, Callable, Mapping, Optional

from onionmind_desktop_vault import (
    _NO_WINDOW,
    _as_text_core,
)


# --- Remote compute over SSH -------------------------------------------
#
# The model runs on another machine the user can SSH into; everything else -
# files, agent, terminal, Tor - stays here. An `ssh -L` tunnel lands that
# machine's Ollama on a free loopback port, so the core still only ever talks
# to 127.0.0.1 and the agent's loopback-only socket shims need no exception.
# The SSH connection itself is direct, not Tor: it is the user's own server,
# opened only when they press Connect (or opted into connecting at launch).

REMOTE_OLLAMA_PORT = 11434

_SSH_TARGET = re.compile(r"(?:[A-Za-z0-9._%+-]+@)?[A-Za-z0-9._-]+(?::(\d{1,5}))?")


@dataclass(frozen=True)
class SshTarget:
    destination: str              # user@host or a ~/.ssh/config alias
    port: Optional[int] = None

    @property
    def label(self) -> str:
        return self.destination if self.port is None else f"{self.destination}:{self.port}"


def parse_ssh_target(value: Any) -> Optional[SshTarget]:
    """`user@host`, `host`, an ssh-config alias, optionally `:port`; else None.

    Deliberately narrow: no spaces, no leading dash (it would be read as an
    ssh option), nothing a shell or ssh would interpret.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text.startswith("-"):
        return None
    match = _SSH_TARGET.fullmatch(text)
    if match is None:
        return None
    if match.group(1) is None:
        return SshTarget(text)
    port = int(match.group(1))
    if not 0 < port < 65536:
        return None
    return SshTarget(text.rsplit(":", 1)[0], port)


def ssh_tunnel_argv(target: SshTarget, local_port: int,
                    remote_port: int = REMOTE_OLLAMA_PORT,
                    executable: str = "ssh") -> list[str]:
    """The exact `ssh` command: a forward only, no shell, no prompts."""
    argv = [
        executable, "-N", "-T",
        # A GUI has nowhere to type a password or accept a host key, so ssh
        # must fail fast instead of waiting on a prompt nobody sees.
        "-o", "BatchMode=yes",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "ConnectTimeout=15",
        "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=3",
        "-L", f"127.0.0.1:{int(local_port)}:127.0.0.1:{int(remote_port)}",
    ]
    if target.port is not None:
        argv += ["-p", str(target.port)]
    return argv + ["--", target.destination]


def describe_ssh_failure(stderr: str, target_label: str = "the server") -> str:
    """ssh's last words, turned into the step that fixes them."""
    text = _as_text_core(stderr)
    lowered = text.lower()
    if "permission denied" in lowered:
        return (f"{target_label} refused the login. Onionmind cannot type a password: "
                f"set up key login once with `ssh-copy-id {target_label}` "
                "(or load your key into ssh-agent), then connect again.")
    if "host key verification failed" in lowered or "no matching host key" in lowered:
        return (f"{target_label}'s host key is not trusted yet. Run `ssh {target_label}` "
                "once in a terminal to check and accept it, then connect again.")
    if "could not resolve hostname" in lowered:
        return f"Could not find {target_label}. Check the name, or your VPN/Tailscale."
    if "connection refused" in lowered and "channel" not in lowered:
        return f"{target_label} refused the SSH connection. Is sshd running there?"
    if "timed out" in lowered:
        return f"{target_label} did not answer in time."
    if "forwarding" in lowered and ("failed" in lowered or "denied" in lowered):
        return f"{target_label} does not allow port forwarding (AllowTcpForwarding)."
    last = [line.strip() for line in text.splitlines() if line.strip()]
    return last[-1] if last else f"ssh to {target_label} exited without saying why."


def load_remote_settings(settings: Mapping[str, Any]) -> dict[str, Any]:
    """The `remote_compute` block of settings.json, validated; junk is dropped."""
    stored = settings.get("remote_compute") if isinstance(settings, Mapping) else None
    stored = stored if isinstance(stored, Mapping) else {}
    target = parse_ssh_target(stored.get("host"))
    return {
        "host": target.label if target else "",
        "autoconnect": bool(stored.get("autoconnect")) and target is not None,
    }


def _free_loopback_port() -> int:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _ollama_version(port: int, timeout: float = 5.0) -> Optional[str]:
    """The Ollama version answering on 127.0.0.1:<port>, or None."""
    import urllib.request
    # No proxy handler: this is loopback, and an HTTP(S)_PROXY in the
    # environment must not carry it anywhere else.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{int(port)}/api/version", timeout=timeout) as reply:
            data = json.loads(reply.read().decode("utf-8", errors="replace") or "{}")
    except Exception:
        return None
    version = data.get("version") if isinstance(data, dict) else None
    return str(version) if version else None


class SshTunnel:
    """One `ssh -L` child forwarding a remote Ollama to a local loopback port.

    `start()` blocks until Ollama answers through the tunnel (or raises with
    an actionable message), so callers run it off the UI thread.
    """

    def __init__(self, target: SshTarget, *, remote_port: int = REMOTE_OLLAMA_PORT,
                 executable: str = "ssh", ready_timeout: float = 25.0,
                 popen: Callable[..., Any] = subprocess.Popen,
                 which: Callable[[str], Optional[str]] = shutil.which) -> None:
        self.target = target
        self.remote_port = remote_port
        self.executable = executable
        self.ready_timeout = ready_timeout
        self._popen = popen
        self._which = which
        self.process: Any = None
        self.local_port: Optional[int] = None
        self.version: Optional[str] = None
        self.cancelled = False
        self._stderr_tail: list[str] = []

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def _drain(self, stream: Any) -> None:
        # ssh keeps a pipe it may write to for hours; reading it forever means
        # a chatty server can never fill the pipe and stall the tunnel.
        for raw in iter(stream.readline, b""):
            line = raw.decode("utf-8", errors="replace").strip()
            if line:
                self._stderr_tail = (self._stderr_tail + [line])[-20:]

    def last_error(self) -> str:
        return describe_ssh_failure("\n".join(self._stderr_tail), self.target.label)

    def start(self) -> int:
        import threading
        import time

        executable = self._which(self.executable)
        if executable is None:
            raise RuntimeError(
                "No `ssh` command found. Install the OpenSSH client "
                "(Windows: Settings > Optional features > OpenSSH Client)."
            )
        self.stop()
        port = _free_loopback_port()
        process = self._popen(
            ssh_tunnel_argv(self.target, port, self.remote_port, executable),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            creationflags=_NO_WINDOW,
        )
        self.process = process
        self._stderr_tail = []
        if getattr(process, "stderr", None) is not None:
            threading.Thread(target=self._drain, args=(process.stderr,), daemon=True).start()
        deadline = time.monotonic() + self.ready_timeout
        while time.monotonic() < deadline:
            if self.cancelled:
                self.process = process
                self.stop()
                raise RuntimeError("Cancelled.")
            if process.poll() is not None:
                time.sleep(0.1)          # let the drain thread catch ssh's last line
                self.process = None
                raise RuntimeError(self.last_error())
            version = _ollama_version(port, timeout=2.0)
            if version:
                self.local_port, self.version = port, version
                return port
            # ssh logs "channel N: open failed: connect failed" when the login
            # worked but nothing listens on the far side - no need to wait out
            # the whole timeout to say so.
            if any("open failed" in line for line in self._stderr_tail):
                break
            time.sleep(0.4)
        connected = self.alive
        self.stop()
        if connected:
            raise RuntimeError(
                f"Connected to {self.target.label}, but no Ollama answered on its "
                f"127.0.0.1:{self.remote_port}. Start it there (`systemctl status ollama`)."
            )
        raise RuntimeError(self.last_error())

    def cancel(self) -> None:
        """Stop from another thread, including mid-`start()` - nothing is left running."""
        self.cancelled = True
        self.stop()

    def stop(self) -> None:
        process, self.process = self.process, None
        self.local_port = self.version = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except Exception:
            process.kill()
