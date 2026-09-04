"""Pure desktop support for Onionmind.

This module deliberately contains no GUI imports.  It owns the filesystem and
process-shaped details that the native desktop interface needs, while exposing
small value-oriented interfaces that are straightforward to test.
"""

from __future__ import annotations

import copy
import hashlib
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
from typing import Any, Callable, Iterable, Mapping, Optional
from uuid import uuid4
import zipfile


__all__ = [
    "ONIONMIND_TIERS",
    "ModelDisplay",
    "describe_model",
    "model_displays",
    "SettingsStore",
    "PREFERENCE_DEFAULTS",
    "load_preferences",
    "text_scale_factor",
    "resolve_startup_mode",
    "animations_enabled",
    "ChatSession",
    "SessionStore",
    "strip_thinking",
    "sanitize_messages",
    "WorkspaceChange",
    "WorkspaceSnapshot",
    "WorkspaceInspector",
    "HARNESS_LIMITATION",
    "TOR_CONTAINMENT_CEILING",
    "HarnessAvailability",
    "HarnessCommand",
    "HarnessSpec",
    "parse_terminal_command",
    "UPDATE_REVISION_FILENAME",
    "UPDATE_FEED_URL",
    "UpdateManifest",
    "BundleUpdateError",
    "parse_update_manifest",
    "installed_revision",
    "short_revision",
    "update_state",
    "BundleUpdater",
    "pending_staging_dir",
    "prune_update_workdir",
]


PathInput = str | os.PathLike[str]


# The desktop app runs under pythonw.exe, which has no console; without this
# flag Windows gives every helper process (git, node, the engine) its own
# flashing cmd window.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


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


# What each tier actually is, and what it costs to run. The tier names are
# branding; the picker states the real model plus its weight class so the cost
# of a switch is visible before it is made.
_TIER_MODELS: dict[str, tuple[str, str]] = {
    "SPARK": ("LFM2.5 2.6B", "very light - ~2 GB, fine on CPU"),
    "EMBER": ("Qwen3.5 4B", "light - ~3 GB VRAM"),
    "BLAZE": ("Qwen3.5 9B", "moderate - ~7 GB VRAM"),
    "INFERNO": ("Qwen3.8 27B", "heavy - ~12-16 GB VRAM"),
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


def _display_name_for(tier: str | None, name: str, raw_id: str) -> str:
    """The real model name and its weight class; the raw id when we know neither."""

    entry = _TIER_MODELS.get(tier or "")
    if entry is None:
        return raw_id
    model, weight = entry
    tokens = re.split(r"[-_\s]+", name.casefold())
    # A model already named after what it is keeps that name; only the branded
    # tier names are translated back.
    if not any(token.upper() in _TIER_MODELS for token in tokens):
        return f"{raw_id} · {weight}"
    for variant in ("vision", "code"):
        if variant in tokens:
            model = f"{model} {variant}"
            break
    return f"{model} · {weight}"


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
    return ModelDisplay(
        raw_id=raw_id,
        tier=tier,
        display_name=_display_name_for(tier, name, raw_id),
        tag=tag,
    )


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


# --- Workbench preferences -------------------------------------------
#
# Presentation-only knobs persisted in settings.json. Everything defaults to
# the shipped workbench, and the Tor boundary plus the updater permission are
# deliberately not part of this surface: privacy behavior is never a theme.

PREFERENCE_DEFAULTS: dict[str, Any] = {
    "text_scale": "system",
    "enter_sends": True,
    "show_terminal_on_launch": False,
    "startup_mode": "remember",
    "reduce_motion": "system",
}

_PREFERENCE_CHOICES: dict[str, tuple[str, ...]] = {
    "text_scale": ("system", "compact", "comfortable"),
    "startup_mode": ("remember", "chat", "agent"),
    "reduce_motion": ("system", "reduced", "full"),
}

TEXT_SCALE_FACTORS: dict[str, float] = {
    "system": 1.0,
    "compact": 0.9,
    "comfortable": 1.15,
}


def load_preferences(settings: Mapping[str, Any]) -> dict[str, Any]:
    """A validated preference view over raw settings; junk falls back silently."""
    preferences = dict(PREFERENCE_DEFAULTS)
    for key, default in PREFERENCE_DEFAULTS.items():
        value = settings.get(key)
        if value is None:
            continue
        if isinstance(default, bool):
            preferences[key] = bool(value)
        elif isinstance(value, str) and value in _PREFERENCE_CHOICES[key]:
            preferences[key] = value
    return preferences


def text_scale_factor(text_scale: str) -> float:
    return TEXT_SCALE_FACTORS.get(text_scale, 1.0)


def resolve_startup_mode(startup_mode: str, last_mode: str) -> str:
    """The composer mode to open in: an explicit choice, else the last used."""
    if startup_mode in {"chat", "agent"}:
        return startup_mode
    return last_mode if last_mode in {"chat", "agent"} else "agent"


def animations_enabled(reduce_motion: str, system_enabled: bool) -> bool:
    """The preference decides when it has an opinion; the system decides otherwise."""
    if reduce_motion == "reduced":
        return False
    if reduce_motion == "full":
        return True
    return bool(system_enabled)


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
            "messages": sanitize_messages(self.messages),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "archived_at": self.archived_at,
        }


_SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_THINK_TAG_PATTERN = r"<\s*(/?)\s*think(?:\s[^>]*)?>"
_THINK_TAG = re.compile(_THINK_TAG_PATTERN, re.IGNORECASE)
_REASONING_FIELDS = frozenset(
    {"analysis", "reasoning", "reasoning_content", "thinking"}
)


def _think_tag_candidate(candidate: str) -> tuple[str, bool]:
    """Classify text beginning with ``<`` against prefixes of _THINK_TAG."""
    if not candidate.startswith("<"):
        return "invalid", False
    index, size = 1, len(candidate)
    while index < size and candidate[index].isspace():
        index += 1
    if index == size:
        return "prefix", False

    closing = candidate[index] == "/"
    if closing:
        index += 1
        while index < size and candidate[index].isspace():
            index += 1
        if index == size:
            return "prefix", True

    for expected in "think":
        if index == size:
            return "prefix", closing
        if re.fullmatch(expected, candidate[index], re.IGNORECASE) is None:
            return "invalid", closing
        index += 1
    if index == size:
        return "prefix", closing
    if candidate[index] == ">":
        match = _THINK_TAG.fullmatch(candidate[:index + 1])
        return ("complete", bool(match.group(1))) if match else ("invalid", closing)
    if not candidate[index].isspace():
        return "invalid", closing
    index += 1
    while index < size:
        if candidate[index] == ">":
            match = _THINK_TAG.fullmatch(candidate[:index + 1])
            return ("complete", bool(match.group(1))) if match else ("invalid", closing)
        index += 1
    return "prefix", closing


def _partial_think_tag(text: str) -> tuple[int, bool] | None:
    start = text.find("<")
    while start >= 0:
        state, closing = _think_tag_candidate(text[start:])
        if state == "prefix":
            return start, closing
        start = text.find("<", start + 1)
    return None


def strip_thinking(text: str) -> str:
    """Remove all model reasoning blocks from completed assistant text.

    A response can contain reasoning before a tool call and another block after
    the tool result.  Treat an unmatched closing tag as the end of an implicit
    leading reasoning block, and an unmatched opening tag as reasoning through
    end-of-response.  This deliberately fails closed for legacy transcripts.
    """

    if not isinstance(text, str):
        raise TypeError("assistant content must be a string")

    visible: list[str] = []
    cursor = 0
    depth = 0
    for tag in _THINK_TAG.finditer(text):
        closing = bool(tag.group(1))
        if closing:
            if depth:
                depth -= 1
                if depth == 0:
                    cursor = tag.end()
            else:
                # Some local reasoning models omit the opening tag. Preserve
                # the established fail-closed behavior and discard that prefix.
                visible.clear()
                cursor = tag.end()
            continue

        if depth == 0:
            visible.append(text[cursor:tag.start()])
        depth += 1

    if depth == 0:
        tail = text[cursor:]
        partial = _partial_think_tag(tail)
        if partial is None:
            visible.append(tail)
        elif partial[1]:
            visible.clear()
        else:
            # Keep completed visible text before an unfinished opening tag.
            visible.append(tail[:partial[0]])
    return "".join(visible).strip()


def sanitize_messages(
    messages: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Deep-copy messages and remove reasoning from every assistant entry.

    Non-content fields such as ``tool_calls`` are retained verbatim so a live
    turn can finish its tool protocol before the sanitized history is stored.
    """

    copied: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        if not isinstance(message, Mapping):
            raise TypeError(f"message {index} must be a mapping")
        item = copy.deepcopy(dict(message))
        if item.get("role") == "assistant":
            for key in list(item):
                if isinstance(key, str) and key.casefold() in _REASONING_FIELDS:
                    item.pop(key, None)
            content = item.get("content")
            item["content"] = _sanitize_assistant_content(content)
        copied.append(item)
    return copied


def _sanitize_assistant_content(value: Any) -> Any:
    if isinstance(value, str):
        return strip_thinking(value)
    if isinstance(value, Mapping):
        return {key: _sanitize_assistant_content(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_assistant_content(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_assistant_content(item) for item in value)
    return copy.deepcopy(value)


def _message_dicts(messages: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return sanitize_messages(messages)


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

    def delete(self, session_id: str) -> bool:
        """Permanently remove every stored copy of a session from this machine."""

        deleted = False
        for archived in (False, True):
            path = self._path(session_id, archived=archived)
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            deleted = True
        return deleted


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
                creationflags=_NO_WINDOW,
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


# The honest ceiling on the Tor boundary, said in the user's face rather than
# only in TECHNICAL.md. Every layer that routes the agent through Tor - the proxy
# variables, the loopback bridge, the python and node socket shims - is
# environment handed to a process running as the user, so anything that does not
# read that environment is not covered. Only the kernel can cover it.
TOR_CONTAINMENT_CEILING = (
    "Tor is enforced by the environment the agent runs in, not by the operating "
    "system. Proxy variables and the injected Python and Node socket shims cover "
    "every runtime the agent normally reaches for, but a compiled binary, "
    "python -S, or a tool that ignores proxies outright (ping, nslookup, "
    "traceroute) can still reach the network directly. Closing that needs an OS "
    "egress rule - a firewall rule for this user, a container, a network "
    "namespace - or the Matchstick live USB, whose nftables ruleset already does it."
)


HARNESS_LIMITATION = (
    "Onionmind Agent is an early-access local coding workflow. It starts in the "
    "selected working directory, while its own tools govern what it can access. "
    "Approvals are on by default: it asks before a protected action, and where "
    "there is nobody to ask it stops instead of continuing. Ticking YOLO lets it "
    "edit files and run commands unattended - that widens what it may do to this "
    "machine, and moves the network boundary not at all. "
    "The agent reaches the web only through Tor: it verifies "
    "a circuit before it starts and refuses to run without one.\n\n"
    + TOR_CONTAINMENT_CEILING
)


@dataclass(frozen=True)
class HarnessAvailability:
    available: bool
    executable: str | None
    reason: str
    limitation: str = HARNESS_LIMITATION


@dataclass(frozen=True)
class HarnessCommand:
    argv: tuple[str, ...]
    cwd: Path


class HarnessSpec:
    """Build and preflight the public Ollama DeepSeek Harness launcher."""

    def __init__(self, executable: str = "ollama") -> None:
        if not isinstance(executable, str) or not executable.strip():
            raise ValueError("harness executable must be non-empty")
        self.executable = executable

    @property
    def limitation(self) -> str:
        return HARNESS_LIMITATION

    def build(self, *, model: str, task: str, cwd: PathInput) -> HarnessCommand:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("harness model must be non-empty")
        if not isinstance(task, str) or not task.strip():
            raise ValueError("harness task must be non-empty")
        if "\x00" in model or "\x00" in task:
            raise ValueError("harness arguments cannot contain NUL")
        working_directory = WorkspaceInspector._root(cwd)
        return HarnessCommand(
            argv=(
                self.executable,
                "launch",
                "dsh",
                "--model",
                model,
                "--",
                "--profile",
                "headless",
                task,
            ),
            cwd=working_directory,
        )

    def check(self) -> HarnessAvailability:
        executable = shutil.which(self.executable)
        if executable is None:
            return HarnessAvailability(
                available=False,
                executable=None,
                reason=(
                    "Onionmind's local engine is not ready. Re-run Onionmind setup "
                    "or start its local model service, then try Agent mode again."
                ),
            )
        try:
            result = subprocess.run(
                [executable, "--version"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                shell=False,
                timeout=5,
                creationflags=_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return HarnessAvailability(
                available=False,
                executable=executable,
                reason=f"Onionmind's local engine could not be started: {exc}",
            )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).decode(
                "utf-8", errors="replace"
            ).strip()
            return HarnessAvailability(
                available=False,
                executable=executable,
                reason=detail or "Onionmind's local engine did not pass its readiness check.",
            )

        node = shutil.which("node")
        if node is None:
            return HarnessAvailability(
                available=False,
                executable=executable,
                reason=(
                    "Onionmind Agent prerequisites are incomplete. Re-run Onionmind "
                    "setup, then try Agent mode again."
                ),
            )
        try:
            node_result = subprocess.run(
                [node, "--version"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                shell=False,
                timeout=5,
                creationflags=_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return HarnessAvailability(
                available=False,
                executable=executable,
                reason=f"Onionmind Agent prerequisites could not be checked: {exc}",
            )
        node_text = (node_result.stdout or node_result.stderr).decode(
            "utf-8", errors="replace"
        ).strip()
        version_match = re.search(r"v?(\d+)\.(\d+)(?:\.\d+)?", node_text)
        supported = bool(
            node_result.returncode == 0
            and version_match is not None
            and (
                int(version_match.group(1)) >= 24
                or (
                    int(version_match.group(1)) == 22
                    and int(version_match.group(2)) >= 19
                )
            )
        )
        if not supported:
            shown = node_text or "unknown version"
            return HarnessAvailability(
                available=False,
                executable=executable,
                reason=(
                    f"Onionmind Agent needs a newer local runtime; found {shown}. "
                    "Re-run Onionmind setup, then try Agent mode again."
                ),
            )
        return HarnessAvailability(
            available=True,
            executable=executable,
            reason="Onionmind Agent is ready and will start on demand.",
        )


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


# --- Tor-routed self-update -------------------------------------------------
#
# The installed workbench is a Nuitka standalone bundle: its code is compiled
# into Onionmind.exe, so an update means a whole new bundle directory. The
# feed is a plain GitHub release-asset URL (no api.github.com call, which is
# aggressively rate-limited for shared Tor exit addresses) whose small JSON
# manifest carries the source revision plus the size and SHA-256 of the zip.
# Every request - manifest and bundle alike - goes through the local Tor SOCKS
# port with fresh credentials, so each fetch rides its own circuit. A failed
# Tor check fails closed; there is no direct-network fallback anywhere in this path.

UPDATE_REPO = "Codemaster64/onionmind"
UPDATE_FEED_TAG = "desktop-latest"
UPDATE_MANIFEST_ASSET = "onionmind-update.json"
UPDATE_REVISION_FILENAME = ".onionmind-source-revision"
UPDATE_FEED_URL = (
    f"https://github.com/{UPDATE_REPO}/releases/download/"
    f"{UPDATE_FEED_TAG}/{UPDATE_MANIFEST_ASSET}"
)
_UPDATE_ASSET_HOSTS = ("github.com", "githubusercontent.com", "github.io")


class BundleUpdateError(RuntimeError):
    """A user-facing update failure. Never retried outside Tor."""


@dataclass(frozen=True)
class UpdateManifest:
    revision: str
    version: str
    asset_name: str
    asset_url: str
    size: int
    sha256: str


def short_revision(revision: Optional[str]) -> str:
    """Seven hex characters for display; honest fallbacks for odd values."""

    if not revision:
        return "unknown"
    text = revision.strip()
    return text[:7] if re.fullmatch(r"[0-9a-fA-F]{7,40}", text) else text[:12]


def parse_update_manifest(text: str) -> UpdateManifest:
    """Validate the release manifest strictly - it decides what gets executed.

    A sloppily parsed manifest is the one file an attacker controlling the feed
    could use to point the updater at an arbitrary URL, so asset names must be
    plain filenames, URLs must be GitHub hosts over HTTPS, and the digest must
    be a full lowercase SHA-256.
    """

    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise BundleUpdateError(f"update manifest is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise BundleUpdateError("update manifest must be a JSON object")

    revision = payload.get("revision")
    version = payload.get("version")
    asset_name = payload.get("asset")
    asset_url = payload.get("asset_url")
    size = payload.get("size")
    sha256 = payload.get("sha256")

    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{7,40}", revision):
        raise BundleUpdateError("update manifest has no valid revision")
    if not isinstance(version, str) or not version.strip() or not version.isprintable():
        raise BundleUpdateError("update manifest has no valid version")
    if not isinstance(asset_name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", asset_name):
        raise BundleUpdateError("update manifest has no valid asset name")
    if (
        not isinstance(asset_url, str)
        or not asset_url.startswith("https://")
        or not any(
            asset_url[len("https://") :].split("/", 1)[0].endswith(host)
            for host in _UPDATE_ASSET_HOSTS
        )
    ):
        raise BundleUpdateError("update manifest asset URL is not a GitHub HTTPS URL")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise BundleUpdateError("update manifest has no valid asset size")
    if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise BundleUpdateError("update manifest has no valid SHA-256 digest")

    return UpdateManifest(
        revision=revision,
        version=version,
        asset_name=asset_name,
        asset_url=asset_url,
        size=size,
        sha256=sha256,
    )


def installed_revision(install_dir: PathInput) -> Optional[str]:
    """The revision marker written into the bundle at build time, if present."""

    marker = Path(install_dir) / UPDATE_REVISION_FILENAME
    try:
        text = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def update_state(installed: Optional[str], manifest: Optional[UpdateManifest]) -> str:
    """'current', 'available', or 'development' - the honest tri-state.

    Without git metadata the app cannot order revisions, so any difference
    between the local marker and the feed is reported as an available update;
    the dialog always shows both revisions and the user decides.
    """

    if manifest is None:
        return "unavailable"
    if installed is None:
        return "development"
    if installed == manifest.revision:
        return "current"
    return "available"


# The swap itself runs outside the app: a running Windows executable cannot be
# replaced in place, so this script waits for the app to exit, renames the old
# bundle to a dated backup beside itself (the naming the installer already
# uses), moves the verified staging directory into place, and relaunches. Any
# failure rolls the backup back before giving up, so a half-applied update
# cannot leave the machine without a working Onionmind.
_APPLY_SCRIPT_TEMPLATE = r"""
param(
  [Parameter(Mandatory=$true)][int]$ParentPid,
  [Parameter(Mandatory=$true)][string]$InstallDir,
  [Parameter(Mandatory=$true)][string]$StagingDir,
  [Parameter(Mandatory=$true)][string]$LogFile
)

$ErrorActionPreference = 'Stop'
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Write-ApplyLog([string]$Message) {
  try {
    [IO.File]::AppendAllText($LogFile, ("{0} {1}`n" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message), $Utf8NoBom)
  } catch { }
}

function Get-MarkerRevision([string]$Directory) {
  $Marker = Join-Path $Directory '@MARKER@'
  try { return [string](Get-Content -LiteralPath $Marker -ErrorAction Stop | Select-Object -First 1) }
  catch { return '' }
}

try {
  if (-not (Test-Path -LiteralPath (Join-Path $StagingDir '@EXE_NAME@') -PathType Leaf)) {
    throw "Staging directory has no @EXE_NAME@; refusing to swap."
  }

  # 1. The caller exits right after spawning this script; give it time to die
  #    so the old executable stops being locked.
  $Deadline = (Get-Date).AddSeconds(120)
  while ($true) {
    if (-not (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue)) { break }
    if ((Get-Date) -gt $Deadline) { throw "Onionmind (pid $ParentPid) did not exit within 120s." }
    Start-Sleep -Milliseconds 500
  }
  Start-Sleep -Milliseconds 800

  $Parent = Split-Path -Parent $InstallDir
  $Leaf = Split-Path -Leaf $InstallDir
  $OldRevision = Get-MarkerRevision $InstallDir
  $OldShort = if ($OldRevision) { $OldRevision.Substring(0, [Math]::Min(7, $OldRevision.Length)) } else { 'unknown' }
  $Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
  $Backup = Join-Path $Parent ("{0}.backup-before-{1}-{2}" -f $Leaf, $OldShort, $Stamp)

  # 2. Swap. Rename first so the move lands at the exact original path.
  Rename-Item -LiteralPath $InstallDir -NewName (Split-Path -Leaf $Backup)
  Write-ApplyLog "renamed old bundle to $(Split-Path -Leaf $Backup)"
  try {
    Move-Item -LiteralPath $StagingDir -Destination $InstallDir
    Write-ApplyLog "moved staged bundle into place"
  } catch {
    Rename-Item -LiteralPath $Backup -NewName $Leaf
    Write-ApplyLog "move failed; restored the previous bundle"
    throw
  }

  # 3. Keep the two newest backups only; older swaps otherwise pile up forever.
  Get-ChildItem -LiteralPath $Parent -Directory -Filter ($Leaf + '.backup-before-*') -ErrorAction SilentlyContinue |
    Sort-Object CreationTime -Descending |
    Select-Object -Skip 2 |
    ForEach-Object {
      try { Remove-Item -LiteralPath $_.FullName -Recurse -Force; Write-ApplyLog "pruned old backup $($_.Name)" }
      catch { Write-ApplyLog "could not prune old backup $($_.Name): $($_.Exception.Message)" }
    }

  # 4. Relaunch the freshly installed workbench. By this point the update is
  #    applied regardless, so a relaunch problem is logged, not fatal.
  try {
    Start-Process -FilePath (Join-Path $InstallDir '@EXE_NAME@') -WorkingDirectory $InstallDir
    Write-ApplyLog "relaunched the new workbench"
  } catch {
    Write-ApplyLog ("could not relaunch automatically; start Onionmind by hand: " + $_.Exception.Message)
  }
  Write-ApplyLog ("update applied; now running revision " + (Get-MarkerRevision $InstallDir))
  exit 0
} catch {
  Write-ApplyLog ("FAILED: " + $_.Exception.Message)
  exit 1
}
"""


class BundleUpdater:
    """Tor-only download and staging for the installed standalone bundle.

    ``proxies_factory`` mirrors ``onionmind._proxies``: it receives the SOCKS
    port and returns a requests proxy mapping, and it is called once per
    request so every fetch gets a fresh isolated circuit.
    """

    def __init__(
        self,
        install_dir: PathInput,
        work_dir: PathInput,
        proxies_factory: Callable[[int], dict[str, str]],
        user_agent: str,
        session: Optional[Any] = None,
    ) -> None:
        self.install_dir = Path(install_dir)
        self.work_dir = Path(work_dir)
        self.proxies_factory = proxies_factory
        self.user_agent = user_agent
        self._session = session

    def _request(self, method: str, url: str, port: int, **kwargs: Any) -> Any:
        import requests  # deferred: the pure module stays importable without it

        proxies = self.proxies_factory(port)
        if not proxies:
            raise BundleUpdateError("No verified Tor proxy is pinned for this update.")
        request = self._session or requests
        kwargs.setdefault("timeout", 90)
        kwargs.setdefault(
            "headers", {"User-Agent": self.user_agent, "Accept": "application/octet-stream"}
        )
        kwargs["proxies"] = proxies
        try:
            return request.request(method, url, **kwargs)
        except BundleUpdateError:
            raise
        except Exception as exc:
            raise BundleUpdateError(
                f"Could not reach the update feed over Tor: {exc}"
            ) from exc

    def fetch_manifest(self, port: int) -> UpdateManifest:
        response = self._request("GET", UPDATE_FEED_URL, port)
        self._raise_for_status(response)
        try:
            body = response.content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BundleUpdateError("Update manifest is not UTF-8 text.") from exc
        return parse_update_manifest(body)

    @staticmethod
    def _raise_for_status(response: Any) -> None:
        status = getattr(response, "status_code", 0)
        if status == 200:
            return
        detail = ""
        text = getattr(response, "text", "")
        if text:
            detail = ": " + text[:160].strip()
        raise BundleUpdateError(f"Update feed returned HTTP {status}{detail}")

    def download(
        self,
        port: int,
        manifest: UpdateManifest,
        progress: Optional[Callable[[Optional[float], str], None]] = None,
        stop_event: Optional[Any] = None,
    ) -> Path:
        """Stream the bundle zip through Tor into the work directory.

        The archive lands under a ``.part`` name and is verified against the
        manifest size and SHA-256 before it is allowed to keep the final name,
        so a truncated or tampered download can never be staged.
        """

        downloads = self.work_dir / "downloads"
        downloads.mkdir(parents=True, exist_ok=True)
        final_path = downloads / f"{manifest.asset_name}"
        part_path = downloads / (manifest.asset_name + ".part")

        response = self._request(
            "GET",
            manifest.asset_url,
            port,
            stream=True,
            timeout=(60, 300),
        )
        self._raise_for_status(response)
        total = manifest.size
        digest = hashlib.sha256()
        done = 0
        last_note = -1.0
        try:
            with part_path.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=262144):
                    if stop_event is not None and stop_event.is_set():
                        raise BundleUpdateError("Update download was stopped.")
                    if not chunk:
                        continue
                    handle.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    if done > total:
                        raise BundleUpdateError(
                            "Downloaded bundle is larger than the manifest announced."
                        )
                    if progress is not None and (done - last_note >= 1048576 or done == total):
                        last_note = float(done)
                        note = f"{done // 1048576} MB of {total // 1048576} MB over Tor"
                        progress(done / total if total else None, note)
        except BundleUpdateError:
            part_path.unlink(missing_ok=True)
            raise
        except OSError as exc:
            part_path.unlink(missing_ok=True)
            raise BundleUpdateError(f"Could not write the update download: {exc}") from exc

        if done != total:
            part_path.unlink(missing_ok=True)
            raise BundleUpdateError(
                f"Download stopped early at {done} of {total} bytes; nothing was installed."
            )
        if digest.hexdigest() != manifest.sha256:
            part_path.unlink(missing_ok=True)
            raise BundleUpdateError(
                "Downloaded bundle failed the SHA-256 check; nothing was installed."
            )
        part_path.replace(final_path)
        return final_path

    def stage(self, manifest: UpdateManifest, archive_path: PathInput) -> Path:
        """Unpack a verified archive into a staging directory beside the install.

        Extraction is guarded against zip-slip (every member must stay inside
        the staging root) and the result must contain both the executable and a
        revision marker matching the manifest, so what gets swapped in is
        exactly what the feed described.
        """

        staging = self.work_dir / f"staging-{manifest.revision[:12]}"
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)

        root = staging.resolve()
        try:
            with zipfile.ZipFile(archive_path) as archive:
                for member in archive.namelist():
                    resolved = (root / member).resolve()
                    if resolved != root and root not in resolved.parents:
                        raise BundleUpdateError(
                            f"Update archive entry escapes the staging directory: {member}"
                        )
                archive.extractall(root)
        except BundleUpdateError:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        except (OSError, zipfile.BadZipFile) as exc:
            shutil.rmtree(staging, ignore_errors=True)
            raise BundleUpdateError(f"Update archive could not be unpacked: {exc}") from exc

        if not (staging / "Onionmind.exe").is_file():
            shutil.rmtree(staging, ignore_errors=True)
            raise BundleUpdateError("Update archive has no Onionmind.exe; nothing was installed.")
        staged_revision = installed_revision(staging)
        if staged_revision != manifest.revision:
            shutil.rmtree(staging, ignore_errors=True)
            raise BundleUpdateError(
                "Update archive revision marker does not match the manifest; nothing was installed."
            )
        return staging

    def write_apply_script(self) -> Path:
        """Materialise the post-exit swap script and return its path."""

        self.work_dir.mkdir(parents=True, exist_ok=True)
        script_path = self.work_dir / "apply-onionmind-update.ps1"
        text = _APPLY_SCRIPT_TEMPLATE
        for token, value in (
            ("@MARKER@", UPDATE_REVISION_FILENAME),
            ("@EXE_NAME@", "Onionmind.exe"),
        ):
            text = text.replace(token, value)
        # BOM so Windows PowerShell 5.1 reads the script as UTF-8 regardless
        # of the system code page.
        script_path.write_text(text, encoding="utf-8-sig")
        return script_path

    def apply_command(self, staging_dir: PathInput) -> list[str]:
        """The detached command that finishes the update after the app exits."""

        script = self.write_apply_script()
        return [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-ParentPid",
            str(os.getpid()),
            "-InstallDir",
            str(self.install_dir),
            "-StagingDir",
            str(Path(staging_dir)),
            "-LogFile",
            str(self.work_dir / "apply.log"),
        ]


def pending_staging_dir(work_dir: PathInput) -> Optional[Path]:
    """A previously staged, still-verified bundle waiting to be applied."""

    work = Path(work_dir)
    if not work.is_dir():
        return None
    candidates = sorted(
        (entry for entry in work.iterdir() if entry.is_dir() and entry.name.startswith("staging-")),
        key=lambda entry: entry.stat().st_mtime,
        reverse=True,
    )
    for candidate in candidates:
        if (candidate / "Onionmind.exe").is_file() and installed_revision(candidate):
            return candidate
    return None


def prune_update_workdir(
    work_dir: PathInput,
    running_revision: Optional[str] = None,
    max_age_days: int = 14,
) -> None:
    """Drop long-lived downloads and stale staging directories.

    Staging directories whose revision matches the running bundle are stale by
    definition - the update they hold is already installed - so they go first.
    """

    work = Path(work_dir)
    if not work.is_dir():
        return
    cutoff = datetime.now().timestamp() - max_age_days * 86400
    downloads = work / "downloads"
    if downloads.is_dir():
        for entry in downloads.iterdir():
            try:
                if entry.is_file() and entry.stat().st_mtime < cutoff:
                    entry.unlink(missing_ok=True)
            except OSError:
                continue
    for entry in work.iterdir():
        if not entry.is_dir() or not entry.name.startswith("staging-"):
            continue
        try:
            if installed_revision(entry) == running_revision or entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            continue
