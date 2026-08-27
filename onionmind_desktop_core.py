"""Pure desktop support for Onionmind.

This module deliberately contains no GUI imports.  It owns the filesystem and
process-shaped details that the native desktop interface needs, while exposing
small value-oriented interfaces that are straightforward to test.
"""

from __future__ import annotations

import copy
import ipaddress
import json
import os
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
from typing import Any, Iterable, Mapping
import urllib.parse
import urllib.request
from uuid import uuid4


__all__ = [
    "ONIONMIND_TIERS",
    "ModelDisplay",
    "describe_model",
    "model_displays",
    "SettingsStore",
    "ChatSession",
    "SessionStore",
    "WorkspaceChange",
    "WorkspaceSnapshot",
    "WorkspaceInspector",
    "AGENT_BOUNDARY",
    "AgentAvailability",
    "AgentCommand",
    "AgentSpec",
    "AgentStreamParser",
    "parse_terminal_command",
]


PathInput = str | os.PathLike[str]


ONIONMIND_TIERS: tuple[str, ...] = (
    "SPARK",
    "EMBER",
    "BLAZE",
    "INFERNO",
    "CINDER",
    "WILDFIRE",
    "FLASHPOINT",
    "PHOENIX",
    "NOVA",
    "PYRE",
)

_TIER_ALIASES: dict[str, str] = {
    # Shipped model names and their installer/size aliases.
    "spark": "SPARK",
    "lfm": "SPARK",
    "lfm2": "SPARK",
    "lfm2.5": "SPARK",
    "2.6b": "SPARK",
    "ember": "EMBER",
    "4b": "EMBER",
    "blaze": "BLAZE",
    "9b": "BLAZE",
    "inferno": "INFERNO",
    "27b": "INFERNO",
    # Reserved names remain first-class display tiers.
    "cinder": "CINDER",
    "wildfire": "WILDFIRE",
    "flashpoint": "FLASHPOINT",
    "phoenix": "PHOENIX",
    "nova": "NOVA",
    "pyre": "PYRE",
}


@dataclass(frozen=True)
class ModelDisplay:
    """Presentation data for one installed model without losing its identifier."""

    raw_id: str
    tier: str | None
    display_name: str
    tag: str | None


def _model_name_and_tag(raw_id: str) -> tuple[str, str | None]:
    last_slash = max(raw_id.rfind("/"), raw_id.rfind("\\"))
    last_colon = raw_id.rfind(":")
    if last_colon > last_slash:
        return raw_id[last_slash + 1 : last_colon], raw_id[last_colon + 1 :]
    return raw_id[last_slash + 1 :], None


def _tier_for_model(name: str, tag: str | None) -> str | None:
    lowered = name.casefold()
    if lowered in _TIER_ALIASES:
        return _TIER_ALIASES[lowered]

    # Vision and namespaced variants such as ``onionmind-inferno-vision`` keep
    # their Onionmind family name.  Dots stay intact so the ``2.6b`` alias can
    # still be recognized.
    for token in re.split(r"[-_\s]+", lowered):
        if token in _TIER_ALIASES:
            return _TIER_ALIASES[token]

    if tag:
        return _TIER_ALIASES.get(tag.casefold())
    return None


def describe_model(raw_id: str) -> ModelDisplay:
    """Describe an Ollama model while preserving its exact usable identifier.

    The returned ``raw_id`` is the string callers must pass to Ollama.  Friendly
    Onionmind tier aliases are presentation-only and never replace that value.
    """

    if not isinstance(raw_id, str) or not raw_id.strip():
        raise ValueError("model identifier must be a non-empty string")
    if "\x00" in raw_id:
        raise ValueError("model identifier cannot contain NUL")

    name, tag = _model_name_and_tag(raw_id)
    tier = _tier_for_model(name, tag)
    canonical = tier is not None and raw_id.casefold() == tier.casefold()
    display_name = tier if canonical else f"{tier} · {raw_id}" if tier else raw_id
    return ModelDisplay(raw_id=raw_id, tier=tier, display_name=display_name, tag=tag)


def model_displays(raw_ids: Iterable[str]) -> tuple[ModelDisplay, ...]:
    """Return stable, de-duplicated model choices in input order."""

    seen: set[str] = set()
    choices: list[ModelDisplay] = []
    for raw_id in raw_ids:
        if raw_id in seen:
            continue
        seen.add(raw_id)
        choices.append(describe_model(raw_id))
    return tuple(choices)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    """Write a JSON object next to its destination, then atomically replace it."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    handle = None
    try:
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        )
        temporary = Path(handle.name)
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        handle = None
        os.replace(temporary, path)
        temporary = None
        # fsync the containing directory as well on POSIX so the rename itself
        # survives a sudden power loss. Windows does not expose directory
        # handles through os.open, so the file flush above is the strongest
        # portable guarantee available there.
        if os.name != "nt":
            directory_fd: int | None = None
            try:
                directory_fd = os.open(
                    path.parent,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                )
                os.fsync(directory_fd)
            except OSError:
                # Some filesystems do not support directory fsync. The atomic
                # replacement has still completed successfully in that case.
                pass
            finally:
                if directory_fd is not None:
                    os.close(directory_fd)
    finally:
        if handle is not None:
            handle.close()
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _quarantine_corrupt(path: Path) -> Path | None:
    """Move unreadable local state aside so callers can recover immediately."""

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = path.with_name(f"{path.name}.corrupt-{stamp}-{uuid4().hex[:8]}")
    try:
        os.replace(path, destination)
    except OSError:
        return None
    return destination


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError:
        return None
    except (UnicodeError, json.JSONDecodeError):
        _quarantine_corrupt(path)
        return None

    if not isinstance(value, dict):
        _quarantine_corrupt(path)
        return None
    return value


class SettingsStore:
    """Atomic local JSON settings with default and corruption recovery."""

    def __init__(
        self,
        path: PathInput,
        defaults: Mapping[str, Any] | None = None,
    ) -> None:
        self.path = Path(path).expanduser()
        if defaults is not None and not isinstance(defaults, Mapping):
            raise TypeError("settings defaults must be a mapping")
        self._defaults = copy.deepcopy(dict(defaults or {}))

    def load(self) -> dict[str, Any]:
        loaded = _read_json_object(self.path)
        result = copy.deepcopy(self._defaults)
        if loaded is not None:
            result.update(loaded)
        return result

    def save(self, settings: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(settings, Mapping):
            raise TypeError("settings must be a mapping")
        result = copy.deepcopy(self._defaults)
        result.update(copy.deepcopy(dict(settings)))
        _atomic_write_json(self.path, result)
        return copy.deepcopy(result)


@dataclass
class ChatSession:
    """A persisted local conversation; messages remain ordinary JSON dicts."""

    id: str
    title: str
    model: str
    workspace: str | None
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)
    archived_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "id": self.id,
            "title": self.title,
            "model": self.model,
            "workspace": self.workspace,
            "messages": copy.deepcopy(self.messages),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "archived_at": self.archived_at,
        }


_SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _message_dicts(messages: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    copied: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        if not isinstance(message, Mapping):
            raise TypeError(f"message {index} must be a mapping")
        copied.append(copy.deepcopy(dict(message)))
    return copied


class SessionStore:
    """Own creation, persistence, discovery, and archiving of local sessions."""

    def __init__(self, root: PathInput) -> None:
        self.root = Path(root).expanduser()
        self.archive_root = self.root / "archive"

    @staticmethod
    def _validate_id(session_id: str) -> str:
        if not isinstance(session_id, str) or not _SAFE_SESSION_ID.fullmatch(session_id):
            raise ValueError("invalid session id")
        return session_id

    def _path(self, session_id: str, *, archived: bool = False) -> Path:
        safe_id = self._validate_id(session_id)
        parent = self.archive_root if archived else self.root
        return parent / f"{safe_id}.json"

    @staticmethod
    def _session_from_dict(
        value: Mapping[str, Any], *, expected_id: str
    ) -> ChatSession:
        session_id = value.get("id")
        if session_id != expected_id:
            raise ValueError("session id does not match its filename")

        title = value.get("title")
        model = value.get("model", "")
        workspace = value.get("workspace")
        messages = value.get("messages", [])
        created_at = value.get("created_at")
        updated_at = value.get("updated_at")
        archived_at = value.get("archived_at")

        if not isinstance(title, str) or not title:
            raise ValueError("session title is invalid")
        if not isinstance(model, str):
            raise ValueError("session model is invalid")
        if workspace is not None and not isinstance(workspace, str):
            raise ValueError("session workspace is invalid")
        if not isinstance(messages, list):
            raise ValueError("session messages are invalid")
        if not isinstance(created_at, str) or not isinstance(updated_at, str):
            raise ValueError("session timestamps are invalid")
        if archived_at is not None and not isinstance(archived_at, str):
            raise ValueError("session archive timestamp is invalid")

        return ChatSession(
            id=session_id,
            title=title,
            model=model,
            workspace=workspace,
            messages=_message_dicts(messages),
            created_at=created_at,
            updated_at=updated_at,
            archived_at=archived_at,
        )

    def create(
        self,
        *,
        title: str = "New session",
        model: str = "",
        workspace: PathInput | None = None,
        messages: Iterable[Mapping[str, Any]] = (),
    ) -> ChatSession:
        if not isinstance(title, str):
            raise TypeError("session title must be a string")
        if not isinstance(model, str):
            raise TypeError("session model must be a string")
        normalized_title = title.strip() or "New session"
        normalized_workspace = (
            str(Path(workspace).expanduser().resolve()) if workspace is not None else None
        )
        now = _utc_now()
        session = ChatSession(
            id=uuid4().hex,
            title=normalized_title,
            model=model,
            workspace=normalized_workspace,
            messages=_message_dicts(messages),
            created_at=now,
            updated_at=now,
        )
        _atomic_write_json(self._path(session.id), session.to_dict())
        return session

    def load(self, session_id: str, *, archived: bool = False) -> ChatSession | None:
        path = self._path(session_id, archived=archived)
        value = _read_json_object(path)
        if value is None:
            return None
        try:
            return self._session_from_dict(value, expected_id=session_id)
        except (TypeError, ValueError):
            _quarantine_corrupt(path)
            return None

    def save(self, session: ChatSession) -> ChatSession:
        if not isinstance(session, ChatSession):
            raise TypeError("session must be a ChatSession")
        self._validate_id(session.id)
        if not isinstance(session.title, str) or not session.title.strip():
            raise ValueError("session title must be non-empty")
        if not isinstance(session.model, str):
            raise TypeError("session model must be a string")
        if session.workspace is not None and not isinstance(session.workspace, str):
            raise TypeError("session workspace must be a string or None")
        if not isinstance(session.created_at, str) or not session.created_at:
            raise ValueError("session creation timestamp must be non-empty")
        if not isinstance(session.updated_at, str) or not session.updated_at:
            raise ValueError("session update timestamp must be non-empty")
        if session.archived_at is not None and not isinstance(session.archived_at, str):
            raise TypeError("session archive timestamp must be a string or None")

        normalized = replace(
            session,
            title=session.title.strip(),
            messages=_message_dicts(session.messages),
            updated_at=_utc_now(),
        )
        target = self._path(normalized.id, archived=normalized.archived_at is not None)
        _atomic_write_json(target, normalized.to_dict())

        # Keep the mutable value object useful to callers that retain it.
        session.title = normalized.title
        session.messages = copy.deepcopy(normalized.messages)
        session.updated_at = normalized.updated_at
        return session

    def list(self, *, archived: bool = False) -> list[ChatSession]:
        parent = self.archive_root if archived else self.root
        if not parent.is_dir():
            return []

        sessions: list[ChatSession] = []
        for path in parent.glob("*.json"):
            if not path.is_file():
                continue
            try:
                session = self.load(path.stem, archived=archived)
            except ValueError:
                _quarantine_corrupt(path)
                continue
            if session is not None:
                sessions.append(session)
        sessions.sort(key=lambda item: (item.updated_at, item.id), reverse=True)
        return sessions

    def archive(self, session_id: str) -> ChatSession | None:
        session = self.load(session_id)
        if session is None:
            return self.load(session_id, archived=True)

        now = _utc_now()
        session.updated_at = now
        session.archived_at = now
        destination = self._path(session.id, archived=True)
        _atomic_write_json(destination, session.to_dict())
        try:
            self._path(session.id).unlink()
        except FileNotFoundError:
            pass
        return session


@dataclass(frozen=True)
class WorkspaceChange:
    status: str
    path: str
    original_path: str | None = None


@dataclass(frozen=True)
class WorkspaceSnapshot:
    root: Path
    is_git: bool
    branch: str | None
    dirty: bool
    changes: tuple[WorkspaceChange, ...]
    agents_files: tuple[str, ...]
    file_tree: tuple[str, ...]
    tree_truncated: bool

    @property
    def change_summary(self) -> str:
        if not self.is_git:
            return "Not a Git repository"
        if not self.changes:
            return "Clean"

        staged = sum(
            change.status[0] not in {" ", "?", "!"} for change in self.changes
        )
        unstaged = sum(
            len(change.status) > 1 and change.status[1] not in {" ", "?", "!"}
            for change in self.changes
        )
        untracked = sum(change.status == "??" for change in self.changes)
        details: list[str] = []
        if staged:
            details.append(f"{staged} staged")
        if unstaged:
            details.append(f"{unstaged} unstaged")
        if untracked:
            details.append(f"{untracked} untracked")
        noun = "change" if len(self.changes) == 1 else "changes"
        suffix = f" · {', '.join(details)}" if details else ""
        return f"{len(self.changes)} {noun}{suffix}"


class WorkspaceInspector:
    """Inspect a selected directory without allowing a shell to reinterpret it."""

    _SKIP_DIRECTORIES = frozenset(
        {
            ".git",
            ".hg",
            ".svn",
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
            ".tox",
            ".venv",
            "__pycache__",
            "build",
            "dist",
            "node_modules",
            "target",
            "venv",
        }
    )

    def __init__(
        self,
        max_entries: int = 200,
        max_depth: int = 4,
        max_diff_chars: int = 200_000,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        if max_depth < 0:
            raise ValueError("max_depth cannot be negative")
        if max_diff_chars < 1:
            raise ValueError("max_diff_chars must be positive")
        self.max_entries = max_entries
        self.max_depth = max_depth
        self.max_diff_chars = max_diff_chars

    @staticmethod
    def _root(selected: PathInput) -> Path:
        try:
            root = Path(selected).expanduser().resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise ValueError(f"workspace does not exist: {selected}") from exc
        if not root.is_dir():
            raise ValueError(f"workspace is not a directory: {selected}")
        return root

    @staticmethod
    def _git(root: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
        environment = os.environ.copy()
        environment["GIT_OPTIONAL_LOCKS"] = "0"
        environment["LC_ALL"] = "C"
        try:
            return subprocess.run(
                [
                    "git",
                    "-c",
                    "core.fsmonitor=false",
                    "-c",
                    "core.untrackedCache=false",
                    *arguments,
                ],
                cwd=root,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                shell=False,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"could not run Git: {exc}") from exc

    @staticmethod
    def _parse_status(output: bytes) -> tuple[WorkspaceChange, ...]:
        records = output.split(b"\0")
        changes: list[WorkspaceChange] = []
        index = 0
        while index < len(records):
            record = records[index]
            index += 1
            if not record:
                continue
            if len(record) < 3:
                continue
            status = record[:2].decode("ascii", errors="replace")
            path = os.fsdecode(record[3:])
            if os.name == "nt":
                path = path.replace("\\", "/")
            original_path = None
            if "R" in status or "C" in status:
                if index < len(records) and records[index]:
                    original_path = os.fsdecode(records[index])
                    if os.name == "nt":
                        original_path = original_path.replace("\\", "/")
                    index += 1
            changes.append(
                WorkspaceChange(
                    status=status,
                    path=path,
                    original_path=original_path,
                )
            )
        return tuple(changes)

    def _tree(self, root: Path) -> tuple[tuple[str, ...], tuple[str, ...], bool]:
        entries: list[str] = []
        agents: list[str] = []
        truncated = False
        stack: list[tuple[Path, int]] = [(root, 0)]

        while stack:
            directory, depth = stack.pop()
            try:
                children = sorted(directory.iterdir(), key=lambda item: item.name.casefold())
            except OSError:
                continue

            directories: list[tuple[Path, int]] = []
            for child in children:
                if child.is_dir() and child.name in self._SKIP_DIRECTORIES:
                    continue
                relative = child.relative_to(root).as_posix()
                is_directory = child.is_dir()
                if len(entries) >= self.max_entries:
                    truncated = True
                    break
                entries.append(relative + ("/" if is_directory else ""))
                if child.name.casefold() == "agents.md" and child.is_file():
                    agents.append(relative)
                is_junction = bool(
                    getattr(child, "is_junction", lambda: False)()
                )
                if is_directory and not child.is_symlink() and not is_junction:
                    try:
                        child.resolve(strict=True).relative_to(root)
                    except (OSError, ValueError):
                        continue
                    if depth < self.max_depth:
                        directories.append((child, depth + 1))
                    else:
                        truncated = True
            if len(entries) >= self.max_entries:
                truncated = True
                break
            stack.extend(reversed(directories))

        return tuple(entries), tuple(agents), truncated

    def inspect(self, selected: PathInput) -> WorkspaceSnapshot:
        root = self._root(selected)
        file_tree, agents_files, tree_truncated = self._tree(root)

        try:
            inside = self._git(root, "rev-parse", "--is-inside-work-tree")
        except RuntimeError:
            inside = None
        is_git = bool(
            inside is not None
            and inside.returncode == 0
            and inside.stdout.strip() == b"true"
        )
        if not is_git:
            return WorkspaceSnapshot(
                root=root,
                is_git=False,
                branch=None,
                dirty=False,
                changes=(),
                agents_files=agents_files,
                file_tree=file_tree,
                tree_truncated=tree_truncated,
            )

        branch_result = self._git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
        if branch_result.returncode == 0:
            branch = os.fsdecode(branch_result.stdout).strip() or None
        else:
            detached = self._git(root, "rev-parse", "--short", "HEAD")
            branch = (
                f"detached@{os.fsdecode(detached.stdout).strip()}"
                if detached.returncode == 0 and detached.stdout.strip()
                else None
            )

        status_result = self._git(
            root,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=normal",
            "--ignore-submodules=all",
        )
        if status_result.returncode != 0:
            detail = os.fsdecode(status_result.stderr).strip() or "Git status failed"
            raise RuntimeError(detail)
        changes = self._parse_status(status_result.stdout)
        return WorkspaceSnapshot(
            root=root,
            is_git=True,
            branch=branch,
            dirty=bool(changes),
            changes=changes,
            agents_files=agents_files,
            file_tree=file_tree,
            tree_truncated=tree_truncated,
        )

    def diff(self, selected: PathInput, relative_path: PathInput | None = None) -> str:
        root = self._root(selected)
        path_arguments: list[str] = []
        relative_filter: str | None = None
        if relative_path is not None:
            candidate = Path(relative_path)
            candidate = candidate if candidate.is_absolute() else root / candidate
            candidate = candidate.resolve(strict=False)
            try:
                relative = candidate.relative_to(root)
            except ValueError as exc:
                raise ValueError("diff path must stay inside the workspace") from exc
            relative_filter = relative.as_posix()
            path_arguments = ["--", relative_filter]
        else:
            path_arguments = ["--"]

        result = self._git(
            root,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--ignore-submodules=all",
            "--no-color",
            "HEAD",
            *path_arguments,
        )
        if result.returncode != 0:
            # An unborn repository has no HEAD.  Its index and worktree can
            # still be inspected without executing user-controlled shell text.
            cached = self._git(
                root,
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--ignore-submodules=all",
                "--no-color",
                "--cached",
                *path_arguments,
            )
            working = self._git(
                root,
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--ignore-submodules=all",
                "--no-color",
                *path_arguments,
            )
            if cached.returncode != 0 or working.returncode != 0:
                message = os.fsdecode(result.stderr).strip() or "Git diff failed"
                raise RuntimeError(message)
            output = cached.stdout + working.stdout
        else:
            output = result.stdout

        text = output.decode("utf-8", errors="replace")
        untracked = self._untracked_previews(root, relative_filter)
        if untracked:
            if text and not text.endswith("\n"):
                text += "\n"
            text += untracked
        if len(text) > self.max_diff_chars:
            return text[: self.max_diff_chars] + "\n… diff truncated …\n"
        return text

    def _untracked_previews(self, root: Path, relative_filter: str | None) -> str:
        """Return bounded, filter-free previews for untracked regular files."""

        arguments = ["ls-files", "--others", "--exclude-standard", "-z", "--"]
        if relative_filter is not None:
            arguments.append(relative_filter)
        result = self._git(root, *arguments)
        if result.returncode != 0:
            return ""

        chunks: list[str] = []
        records = [record for record in result.stdout.split(b"\0") if record]
        for raw_path in records[:20]:
            relative_text = os.fsdecode(raw_path)
            if os.name == "nt":
                relative_text = relative_text.replace("\\", "/")
            relative = Path(relative_text)
            if relative.is_absolute() or ".." in relative.parts:
                continue

            candidate = root
            unsafe_link = False
            for component in relative.parts:
                candidate = candidate / component
                try:
                    is_junction = bool(
                        getattr(candidate, "is_junction", lambda: False)()
                    )
                    if candidate.is_symlink() or is_junction:
                        unsafe_link = True
                        break
                except OSError:
                    unsafe_link = True
                    break
            if unsafe_link:
                continue

            try:
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(root)
                if not resolved.is_file():
                    continue
                with resolved.open("rb") as handle:
                    payload = handle.read(32_769)
            except (OSError, ValueError):
                continue

            header = (
                f"\nUntracked file preview: {relative_text}\n"
                f"{'-' * min(72, max(24, len(relative_text) + 24))}\n"
            )
            if b"\0" in payload:
                body = "[binary content omitted]\n"
            else:
                truncated = len(payload) > 32_768
                body = payload[:32_768].decode("utf-8", errors="replace")
                if body and not body.endswith("\n"):
                    body += "\n"
                if truncated:
                    body += "… file preview truncated …\n"
            chunks.append(header + body)

        if len(records) > 20:
            chunks.append(f"\n… {len(records) - 20} more untracked files omitted …\n")
        return "".join(chunks)


AGENT_BOUNDARY = (
    "Onionmind Agent can read and make file edits inside the selected project. "
    "Shell commands, web tools, external providers, telemetry, and background "
    "update checks are disabled for this workflow. Stop ends the managed Agent "
    "process, then Onionmind refreshes the actual files and Git state on disk."
)

_QWEN_CODE_MIN_VERSION = (0, 22, 0)
_LOCAL_AGENT_BASE_URL = "http://127.0.0.1:11434/v1"
_LOCAL_AGENT_KEY = "onionmind-local"
_AGENT_SYSTEM_PROMPT = (
    "You are Onionmind Agent, a local coding agent. Work only in the current "
    "project. Complete the user task by inspecting and editing files with the "
    "available file tools. Never use shell, web, network, cloud, persistence, "
    "or subagents. Make minimal accurate changes. Do not merely describe an "
    "edit: call a file-edit tool. Stop when the task is complete."
)
_AGENT_EXCLUDED_TOOLS = (
    "run_shell_command",
    "web_fetch",
    "web_search",
    "image_gen",
    "save_memory",
    "agent",
    "skill",
    "ask_user_question",
    "cron_create",
    "cron_list",
    "cron_delete",
    "loop_wakeup",
    "create_sub_session",
    "list_agents",
    "task_create",
    "task_update",
    "task_stop",
    "team_create",
    "team_delete",
    "send_message",
    "monitor",
    "tool_search",
    "read_mcp_resource",
    "enter_worktree",
    "exit_worktree",
    "workflow",
    "computer_use__bring_to_front",
    "computer_use__check_for_update",
    "computer_use__check_permissions",
    "computer_use__click",
    "computer_use__double_click",
    "computer_use__drag",
    "computer_use__end_session",
    "computer_use__get_accessibility_tree",
    "computer_use__get_agent_cursor_state",
    "computer_use__get_config",
    "computer_use__get_cursor_position",
    "computer_use__get_recording_state",
    "computer_use__get_screen_size",
    "computer_use__get_window_state",
    "computer_use__hotkey",
    "computer_use__kill_app",
    "computer_use__launch_app",
    "computer_use__list_apps",
    "computer_use__list_windows",
    "computer_use__move_cursor",
    "computer_use__page",
    "computer_use__press_key",
    "computer_use__replay_trajectory",
    "computer_use__right_click",
    "computer_use__scroll",
    "computer_use__set_agent_cursor_enabled",
    "computer_use__set_agent_cursor_motion",
    "computer_use__set_agent_cursor_style",
    "computer_use__set_config",
    "computer_use__set_value",
    "computer_use__start_recording",
    "computer_use__start_session",
    "computer_use__stop_recording",
    "computer_use__type_text",
    "computer_use__zoom",
    "get_goal",
    "notebook_edit",
    "record_artifact",
    "todo_write",
    "update_goal",
    "zoom_image",
)
_AGENT_PROXY_VARIABLES = (
    "ALL_PROXY",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "all_proxy",
    "https_proxy",
    "http_proxy",
)


@dataclass(frozen=True)
class AgentAvailability:
    available: bool
    executable: str | None
    reason: str
    version: str | None = None
    boundary: str = AGENT_BOUNDARY


@dataclass(frozen=True)
class AgentCommand:
    argv: tuple[str, ...]
    cwd: Path
    environment: tuple[tuple[str, str], ...]
    unset_environment: tuple[str, ...] = _AGENT_PROXY_VARIABLES


def _loopback_base_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Agent base URL must be non-empty")
    parsed = urllib.parse.urlsplit(value.strip())
    try:
        address = ipaddress.ip_address(parsed.hostname or "")
    except ValueError as exc:
        raise ValueError("Agent base URL must use a numeric loopback address") from exc
    if (
        parsed.scheme != "http"
        or not address.is_loopback
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Agent base URL must be an HTTP loopback endpoint")
    path = parsed.path.rstrip("/")
    if path != "/v1":
        raise ValueError("Agent base URL must end in /v1 on a loopback endpoint")
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, path, "", "")
    )


def _is_inferno_model(value: object) -> bool:
    if not isinstance(value, str):
        return False
    model = value.strip().split("@", 1)[0].split(":", 1)[0]
    return model.casefold() == "inferno"


class AgentSpec:
    """Deep Module for safe, local Qwen Code process construction.

    The Interface exposes only readiness and command construction. Provider
    selection, isolated state, disabled network-capable tools, and unattended
    permission policy remain Local to this Implementation.
    """

    def __init__(
        self,
        *,
        state_root: PathInput | None = None,
        base_url: str = _LOCAL_AGENT_BASE_URL,
        executable: str = "qwen",
        launcher: Iterable[str] | None = None,
    ) -> None:
        if not isinstance(executable, str) or not executable.strip():
            raise ValueError("Agent executable must be non-empty")
        self.executable = executable.strip()
        self.base_url = _loopback_base_url(base_url)
        self.state_root = Path(
            state_root or (Path.home() / ".onionmind" / "agent")
        ).expanduser().resolve()
        explicit = tuple(launcher or ())
        if launcher is not None and (not explicit or any(not part for part in explicit)):
            raise ValueError("Agent launcher must contain non-empty arguments")
        self._launcher: tuple[str, ...] | None = explicit or None

    @property
    def boundary(self) -> str:
        return AGENT_BOUNDARY

    def _resolve_launcher(self) -> tuple[str, ...] | None:
        if self._launcher:
            return self._launcher
        executable = shutil.which(self.executable)
        if executable is None:
            return None
        path = Path(executable)
        if os.name == "nt" and path.suffix.casefold() in {".cmd", ".bat"}:
            node = shutil.which("node")
            entrypoint = (
                path.parent
                / "node_modules"
                / "@qwen-code"
                / "qwen-code"
                / "cli-entry.js"
            )
            if node is None or not entrypoint.is_file():
                return None
            return (node, str(entrypoint))
        return (executable,)

    def _prepare_settings(self) -> None:
        """Keep Qwen's own budgeting aligned with Onionmind's local model."""

        settings_path = self.state_root / "settings.json"
        settings = _read_json_object(settings_path) or {}
        model = settings.get("model")
        if not isinstance(model, dict):
            model = {}
        generation = model.get("generationConfig")
        if not isinstance(generation, dict):
            generation = {}
        sampling = generation.get("samplingParams")
        if not isinstance(sampling, dict):
            sampling = {}
        sampling["max_tokens"] = 2048
        generation["samplingParams"] = sampling
        generation["contextWindowSize"] = 32768
        generation["reasoning"] = False
        extra_body = generation.get("extra_body")
        if not isinstance(extra_body, dict):
            extra_body = {}
        extra_body["reasoning_effort"] = "none"
        generation["extra_body"] = extra_body
        model["generationConfig"] = generation
        settings["model"] = model
        _atomic_write_json(settings_path, settings)

    def build(self, *, model: str, task: str, cwd: PathInput) -> AgentCommand:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("Agent model must be non-empty")
        if not _is_inferno_model(model):
            raise ValueError("Onionmind Agent coding requires the INFERNO model")
        if not isinstance(task, str) or not task.strip():
            raise ValueError("Agent task must be non-empty")
        if "\x00" in model or "\x00" in task:
            raise ValueError("Agent arguments cannot contain NUL")
        working_directory = WorkspaceInspector._root(cwd)
        launcher = self._launcher or self._resolve_launcher()
        if launcher is None:
            raise RuntimeError(
                "Onionmind Agent runtime is not installed. Re-run Onionmind setup."
            )
        self._launcher = launcher
        runtime_root = self.state_root / "runtime"
        self.state_root.mkdir(parents=True, exist_ok=True)
        runtime_root.mkdir(parents=True, exist_ok=True)
        self._prepare_settings()
        environment = (
            ("QWEN_HOME", str(self.state_root)),
            ("QWEN_RUNTIME_DIR", str(runtime_root)),
            ("QWEN_USAGE_STATISTICS_ENABLED", "false"),
            ("QWEN_CODE_SKIP_UPDATE_CHECK_ONCE", "1"),
            ("OPENAI_API_KEY", _LOCAL_AGENT_KEY),
            ("OPENAI_BASE_URL", self.base_url),
            ("OPENAI_MODEL", model.strip()),
            ("NO_PROXY", "127.0.0.1,::1"),
            ("no_proxy", "127.0.0.1,::1"),
        )
        return AgentCommand(
            argv=(
                *launcher,
                "--prompt",
                task.strip(),
                "--system-prompt",
                _AGENT_SYSTEM_PROMPT,
                "--output-format",
                "stream-json",
                "--include-partial-messages",
                "--approval-mode",
                "auto-edit",
                "--auth-type",
                "openai",
                "--model",
                model.strip(),
                "--openai-api-key",
                _LOCAL_AGENT_KEY,
                "--openai-base-url",
                self.base_url,
                "--telemetry=false",
                "--chat-recording=false",
                "--safe-mode",
                "--exclude-tools",
                ",".join(_AGENT_EXCLUDED_TOOLS),
                "--max-wall-time",
                "30m",
                "--max-tool-calls",
                "200",
                "--channel",
                "desktop",
            ),
            cwd=working_directory,
            environment=environment,
        )

    def check(self) -> AgentAvailability:
        launcher = self._resolve_launcher()
        if launcher is None:
            return AgentAvailability(
                available=False,
                executable=None,
                reason=(
                    "Onionmind Agent runtime is not installed. Re-run Onionmind "
                    "setup, then restart the app."
                ),
            )
        try:
            result = subprocess.run(
                [*launcher, "--version"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                shell=False,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return AgentAvailability(
                available=False,
                executable=launcher[0],
                reason=f"Onionmind Agent runtime could not be checked: {exc}",
            )
        version_text = (result.stdout or result.stderr).decode(
            "utf-8", errors="replace"
        ).strip()
        match = re.search(r"(?<!\d)(\d+)\.(\d+)\.(\d+)(?!\d)", version_text)
        version = ".".join(match.groups()) if match else None
        supported = bool(
            result.returncode == 0
            and match is not None
            and tuple(int(part) for part in match.groups()) >= _QWEN_CODE_MIN_VERSION
        )
        if not supported:
            shown = version_text or "unknown version"
            return AgentAvailability(
                available=False,
                executable=launcher[0],
                version=version,
                reason=(
                    f"Onionmind Agent runtime is out of date ({shown}). Re-run "
                    "Onionmind setup to update Onionmind Agent."
                ),
            )

        request = urllib.request.Request(
            f"{self.base_url}/models",
            headers={"Authorization": f"Bearer {_LOCAL_AGENT_KEY}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                payload = json.loads(response.read(1_048_577).decode("utf-8"))
                status = getattr(response, "status", 200)
            if status != 200 or not isinstance(payload, dict) or not isinstance(
                payload.get("data"), list
            ):
                raise ValueError("unexpected readiness response")
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            return AgentAvailability(
                available=False,
                executable=launcher[0],
                version=version,
                reason=(
                    "Onionmind's local model service is not ready for Agent mode. "
                    f"Start Onionmind's local engine and try again ({exc})."
                ),
            )
        model_records = payload["data"]
        if not any(
            isinstance(record, dict)
            and _is_inferno_model(
                record.get("id") or record.get("model") or record.get("name")
            )
            for record in model_records
        ):
            return AgentAvailability(
                available=False,
                executable=launcher[0],
                version=version,
                reason=(
                    "INFERNO is required for Onionmind Agent coding but is not "
                    "installed. Re-run Onionmind setup with the INFERNO model."
                ),
            )
        self._launcher = launcher
        return AgentAvailability(
            available=True,
            executable=launcher[0],
            version=version,
            reason="Onionmind Agent is ready for local project edits.",
        )


class AgentStreamParser:
    """Turn Qwen Code's JSONL protocol into concise, user-facing text deltas."""

    def __init__(self) -> None:
        self._buffer = ""
        self._saw_partial_text = False

    @staticmethod
    def _content_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return ""
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") in {"text", "output_text"}:
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)

    def _parse_line(self, line: str) -> tuple[str, ...]:
        stripped = line.strip()
        if not stripped:
            return ()
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            return (line.rstrip("\r") + "\n",)
        if not isinstance(event, dict):
            return ()
        if event.get("type") == "stream_event":
            stream_event = event.get("event")
            if not isinstance(stream_event, dict):
                return ()
            if stream_event.get("type") == "content_block_delta":
                delta = stream_event.get("delta")
                if isinstance(delta, dict) and delta.get("type") == "text_delta":
                    text = delta.get("text")
                    if isinstance(text, str) and text:
                        self._saw_partial_text = True
                        return (text,)
            return ()
        if event.get("type") == "assistant" and not self._saw_partial_text:
            message = event.get("message")
            if isinstance(message, dict):
                text = self._content_text(message.get("content"))
                return (text,) if text else ()
        if event.get("type") == "result" and event.get("subtype") not in {
            None,
            "success",
        }:
            detail = event.get("error") or event.get("message")
            if isinstance(detail, str) and detail:
                return (f"\nAgent stopped: {detail}\n",)
        return ()

    def feed(self, chunk: str) -> tuple[str, ...]:
        if not isinstance(chunk, str):
            raise TypeError("Agent stream chunk must be text")
        self._buffer += chunk
        output: list[str] = []
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            output.extend(self._parse_line(line))
        return tuple(output)

    def finish(self) -> tuple[str, ...]:
        if not self._buffer:
            return ()
        line, self._buffer = self._buffer, ""
        return self._parse_line(line)


def _split_windows_commandline(command: str) -> tuple[str, ...]:
    """Split with the quoting rules used for native Windows process argv."""

    arguments: list[str] = []
    length = len(command)
    index = 0
    while index < length:
        while index < length and command[index] in " \t":
            index += 1
        if index >= length:
            break

        argument: list[str] = []
        quoted = False
        while index < length:
            if command[index] in " \t" and not quoted:
                break
            if command[index] == "\\":
                slash_start = index
                while index < length and command[index] == "\\":
                    index += 1
                slash_count = index - slash_start
                if index < length and command[index] == '"':
                    argument.extend("\\" * (slash_count // 2))
                    if slash_count % 2:
                        argument.append('"')
                        index += 1
                    else:
                        if quoted and index + 1 < length and command[index + 1] == '"':
                            argument.append('"')
                            index += 2
                        else:
                            quoted = not quoted
                            index += 1
                else:
                    argument.extend("\\" * slash_count)
                continue
            if command[index] == '"':
                if quoted and index + 1 < length and command[index + 1] == '"':
                    argument.append('"')
                    index += 2
                else:
                    quoted = not quoted
                    index += 1
                continue
            argument.append(command[index])
            index += 1
        arguments.append("".join(argument))
        while index < length and command[index] in " \t":
            index += 1
    return tuple(arguments)


def parse_terminal_command(
    command: str,
    *,
    interpreter: str = "direct",
) -> tuple[str, ...]:
    """Return argv suitable for ``subprocess`` with ``shell=False``.

    ``direct`` launches a native executable and parses only argv quoting.
    ``powershell``, ``cmd``, and ``sh`` explicitly wrap commands that require
    those interpreters, still passing an explicit argument list to the process
    API instead of enabling its implicit shell mode.
    """

    if not isinstance(command, str) or not command.strip():
        raise ValueError("terminal command must be non-empty")
    if "\x00" in command:
        raise ValueError("terminal command cannot contain NUL")

    if not isinstance(interpreter, str):
        raise TypeError("interpreter must be a string")
    mode = interpreter.casefold()
    if mode == "powershell":
        executable = "powershell.exe" if os.name == "nt" else "pwsh"
        return (
            executable,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            command,
        )
    if mode == "cmd":
        return ("cmd.exe", "/d", "/s", "/c", command)
    if mode == "sh":
        return ("/bin/sh", "-c", command)
    if mode != "direct":
        raise ValueError(
            "interpreter must be 'direct', 'powershell', 'cmd', or 'sh'"
        )

    arguments = (
        _split_windows_commandline(command)
        if os.name == "nt"
        else tuple(shlex.split(command, posix=True))
    )
    if not arguments or not arguments[0]:
        raise ValueError("terminal command must name an executable")
    return arguments
