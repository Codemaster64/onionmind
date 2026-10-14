"""The network log and the agent's private scratch folder.

Part of onionmind_desktop_core, which re-exports every name defined here.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
import re
import tempfile
from typing import Any, Iterable, Mapping, Optional

from onionmind_desktop_vault import (
    PathInput,
    Vault,
    VaultError,
    _ENC_LINE,
    _as_text_core,
    _atomic_write_bytes,
    shred_file,
    shred_tree,
)


# netlog: what left this machine, written by the desktop when it happens.
# Times and destinations only - a prompt or a search query is never stored.

class EgressLog:
    ROUTES = ("Tor", "Direct", "Refused")

    def __init__(self, path: PathInput, vault: Any = None) -> None:
        self.path = Path(path).expanduser()
        self.vault = vault

    def append(self, route: str, what: str, when: Optional[datetime] = None) -> None:
        """Record one connection. Never raises: logging must not break the action."""
        entry = {
            "time": (when or datetime.now().astimezone()).isoformat(timespec="seconds"),
            "route": route if route in self.ROUTES else "Tor",
            "what": _as_text_core(what)[:200],
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(entry, ensure_ascii=False)
            if self.vault is not None:
                line = self.vault.encrypt_line(line)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass

    def entries(self, day: Optional[str] = None) -> list[dict[str, str]]:
        """Entries for one local day (YYYY-MM-DD), oldest first; all when day is None."""
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        rows = []
        for line in lines:
            try:
                if line.startswith(_ENC_LINE):
                    if self.vault is None:
                        continue
                    line = self.vault.decrypt_line(line)
                entry = json.loads(line)
            except (ValueError, VaultError):
                continue
            if not isinstance(entry, dict) or not isinstance(entry.get("time"), str):
                continue
            if day and not entry["time"].startswith(day):
                continue
            rows.append({"time": entry["time"], "route": _as_text_core(entry.get("route")),
                         "what": _as_text_core(entry.get("what"))})
        return rows

    def counts(self, day: Optional[str] = None) -> dict[str, int]:
        rows = self.entries(day)
        return {
            "search": sum(1 for row in rows if row["route"] == "Tor" and row["what"].startswith("Chat search")),
            "tor": sum(1 for row in rows if row["route"] == "Tor"),
            "direct": sum(1 for row in rows if row["route"] == "Direct"),
        }


_AGENT_NET_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) (.*)$")


def agent_net_entries(path: PathInput, day: Optional[str] = None) -> list[dict[str, str]]:
    """The agent bridge's own log (onionmind.py _net_log) as netlog rows.

    Its lines name the destination and whether it went over Tor or was
    refused; they never carry request bodies.
    """
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        match = _AGENT_NET_LINE.match(line.strip())
        if not match or (day and match.group(1) != day):
            continue
        detail = match.group(3).strip()
        lowered = detail.casefold()
        route = "Refused" if "refused" in lowered else ("Tor" if lowered.startswith("tor") else "Local")
        if route == "Local":
            continue                              # loopback traffic never left the machine
        rows.append({"time": f"{match.group(1)}T{match.group(2)}", "route": route,
                     "what": f"Agent network · {detail}"[:200]})
    return rows


def absorb_agent_net_log(path: PathInput, egress: Optional["EgressLog"]) -> int:
    """Move the agent's plaintext network log into the encrypted one, then shred it.

    The agent runs in its own process without the data key, so it can only
    write plaintext; the desktop takes the lines over after each run. With
    logging off (`egress` None) the lines are dropped - no log unless asked.
    Returns the rows moved."""
    target = Path(path)
    if not target.is_file():
        return 0
    rows = agent_net_entries(target)
    if egress is not None:
        for row in rows:
            try:
                when = datetime.fromisoformat(row["time"])
            except ValueError:
                when = None
            egress.append(row["route"], row["what"], when)
    shred_file(target)
    return len(rows) if egress is not None else 0


# qwen-code keeps its chats, tool output and todos under QWEN_RUNTIME_DIR, and
# reads them back to resume a run, so it needs them in plaintext while it runs.
# With a vault each run gets a private scratch folder - RAM-backed under
# XDG_RUNTIME_DIR where there is one - and afterwards its contents go into the
# vault as one encrypted archive per qwen session, and the plaintext is shredded.

AGENT_ARCHIVE_DIR = "agent-runtime"
AGENT_SCRATCH_PREFIX = "onionmind-agent-"
AGENT_NET_LOG_NAME = "agent-net.log"
_AGENT_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")


def agent_scratch_base() -> Path:
    """Where run scratch folders go: the per-user runtime dir (tmpfs on most
    Linux systems, cleared at logout) when there is one, else the temp dir."""
    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    if runtime and os.path.isdir(runtime) and os.access(runtime, os.W_OK):
        return Path(runtime)
    return Path(tempfile.gettempdir())


def _agent_archive(root: PathInput, session_id: str) -> Optional[Path]:
    if not _AGENT_SESSION_ID.fullmatch(session_id or ""):
        return None
    return Path(root).expanduser() / AGENT_ARCHIVE_DIR / f"{session_id}.tar"


def open_agent_scratch(root: PathInput, vault: Vault, resume: str = "") -> Path:
    """A new private (0700) scratch folder for one agent run. When resuming a
    qwen session, its sealed archive is decrypted back into it first."""
    scratch = Path(tempfile.mkdtemp(prefix=AGENT_SCRATCH_PREFIX, dir=agent_scratch_base()))
    archive = _agent_archive(root, resume) if resume else None
    if archive is not None and archive.is_file():
        import io
        import tarfile

        data = vault.decrypt(archive.read_bytes())
        with tarfile.open(fileobj=io.BytesIO(data), mode="r") as bundle:
            members = []
            for member in bundle.getmembers():
                name = Path(member.name)
                # Our own authenticated archive, but extract defensively anyway.
                if name.is_absolute() or ".." in name.parts or not (member.isfile() or member.isdir()):
                    continue
                members.append(member)
            if hasattr(tarfile, "data_filter"):
                bundle.extractall(scratch, members=members, filter="data")
            else:
                bundle.extractall(scratch, members=members)
    return scratch


def seal_agent_scratch(root: PathInput, vault: Vault, scratch: PathInput, session_id: str = "") -> bool:
    """Encrypt what qwen wrote during the run into the vault (replacing that
    session's earlier archive), then shred the scratch folder. The network log
    and the socket shims are not kept: the log was absorbed already. Returns
    True when an archive was written."""
    import io
    import tarfile

    folder = Path(scratch)
    archive = _agent_archive(root, session_id)
    sealed = False
    if archive is not None and folder.is_dir():
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as bundle:
            for path in sorted(folder.rglob("*")):
                rel = path.relative_to(folder)
                if path.is_symlink() or rel.parts[0] in (AGENT_NET_LOG_NAME, "shims"):
                    continue
                if path.is_file() or path.is_dir():
                    bundle.add(path, arcname=rel.as_posix(), recursive=False)
        archive.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_bytes(archive, vault.encrypt(buffer.getvalue()))
        sealed = True
    shred_tree(folder)
    return sealed


def sweep_agent_scratch(egress: Optional["EgressLog"] = None) -> int:
    """Scratch folders a crashed or killed run left behind: take their network
    log in (or drop it), then shred them. Returns the folders removed."""
    base = agent_scratch_base()
    removed = 0
    try:
        leftovers = [p for p in base.iterdir() if p.name.startswith(AGENT_SCRATCH_PREFIX) and p.is_dir()]
    except OSError:
        return 0
    for folder in leftovers:
        try:
            if folder.stat().st_uid != os.getuid():
                continue
        except (OSError, AttributeError):
            pass
        absorb_agent_net_log(folder / AGENT_NET_LOG_NAME, egress)
        shred_tree(folder)
        removed += 0 if folder.exists() else 1
    return removed


def network_log_csv(rows: Iterable[Mapping[str, Any]]) -> str:
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["time", "route", "what"])
    for row in rows:
        writer.writerow([row.get("time", ""), row.get("route", ""), row.get("what", "")])
    return buffer.getvalue()


def network_log_json(rows: Iterable[Mapping[str, Any]]) -> str:
    clean = [{"time": row.get("time", ""), "route": row.get("route", ""), "what": row.get("what", "")}
             for row in rows]
    return json.dumps(clean, indent=2, ensure_ascii=False) + "\n"
