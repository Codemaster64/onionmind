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
import stat
import subprocess
import sys
import tempfile
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import urlparse
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
    "CONTEXT_WINDOW_PRESETS",
    "context_window_tokens",
    "parse_context_window",
    "context_window_warning",
    "resolve_startup_mode",
    "animations_enabled",
    "UNCENSORED_MARKERS",
    "uncensored_marker",
    "quant_from_filename",
    "normalize_model_reference",
    "CatalogModel",
    "parse_hf_catalog",
    "format_downloads",
    "HF_CATALOG_URL",
    "fetch_hf_catalog",
    "machine_memory_mb",
    "gpu_vram_mb",
    "parameter_billions",
    "catalog_fit",
    "catalog_description",
    "shred_file",
    "shred_tree",
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
    "FEATURE_FLAGS",
    "FEATURE_DEFAULTS",
    "load_features",
    "needs_web",
    "web_query",
    "PRESET_DEFAULTS",
    "load_presets",
    "active_preset",
    "with_system_prompt",
    "RULE_FLAGS",
    "RULE_DEFAULTS",
    "load_rules",
    "store_rules",
    "rule_decision",
    "agent_rule_denials",
    "parse_agent_line",
    "agent_initialize_message",
    "agent_task_message",
    "agent_permission_response",
    "agent_interrupt_message",
    "AGENT_CONTEXT_REQUEST",
    "agent_context_usage_message",
    "agent_context_usage",
    "describe_tool_request",
    "edit_size",
    "estimate_tokens",
    "format_tokens",
    "classify_error",
    "search_sessions",
    "sessions_older_than",
    "hash_passphrase",
    "verify_passphrase",
    "EgressLog",
    "agent_net_entries",
    "network_log_csv",
    "network_log_json",
    "RevertRecord",
    "revert_changes",
    "undo_revert",
    "DiffHunk",
    "diff_hunks",
    "revert_hunks",
    "change_stats",
    "gpu_memory_mb",
    "ServerStart",
    "model_server_start_plan",
    "gpu_name",
    "model_fit",
    "node_version",
    "node_supports_agent",
    "REMOTE_OLLAMA_PORT",
    "SshTarget",
    "parse_ssh_target",
    "ssh_tunnel_argv",
    "describe_ssh_failure",
    "load_remote_settings",
    "SshTunnel",
    "VAULT_FILE",
    "Vault",
    "VaultError",
    "VaultLocked",
    "WrongPassword",
    "KeystoreUnavailable",
    "encryption_available",
    "open_vault",
    "vault_header",
    "vault_needs_password",
    "encrypt_data_folder",
    "erase_data_folder",
    "keystore_for",
    "is_encrypted",
    "read_data_file",
    "write_data_file",
    "EXPORT_SUFFIX",
    "ExportError",
    "export_bytes",
    "read_export_bytes",
    "export_payload",
    "write_export",
    "read_export",
    "import_sessions",
    "imported_settings",
    "absorb_agent_net_log",
    "open_agent_scratch",
    "seal_agent_scratch",
    "sweep_agent_scratch",
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


def _atomic_write_json(path: Path, value: Mapping[str, Any], vault: Any = None) -> None:
    """Write a JSON object next to its destination, then atomically replace it.

    With a vault the file is written encrypted. Without one, an encrypted file
    is never overwritten with plaintext: that would be a silent downgrade."""

    if vault is not None:
        text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        _atomic_write_bytes(path, vault.encrypt(text.encode("utf-8")))
        return
    try:
        with path.open("rb") as existing:
            if is_encrypted(existing.read(len(_ENC_MAGIC))):
                raise VaultError(f"{path.name} is encrypted; it can only be written through the open vault")
    except OSError:
        pass
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


def _read_json_object(path: Path, vault: Any = None) -> dict[str, Any] | None:
    try:
        raw = path.read_bytes()
    except (FileNotFoundError, IsADirectoryError):
        return None
    if is_encrypted(raw):
        # Without the right key the file is unreadable, not corrupt: leave it.
        if vault is None:
            return None
        try:
            raw = vault.decrypt(raw)
        except VaultError:
            return None
    try:
        value = json.loads(raw.decode("utf-8"))
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
        vault: Any = None,
    ) -> None:
        self.path = Path(path).expanduser()
        self.vault = vault
        if defaults is not None and not isinstance(defaults, Mapping):
            raise TypeError("settings defaults must be a mapping")
        self._defaults = copy.deepcopy(dict(defaults or {}))

    def load(self) -> dict[str, Any]:
        loaded = _read_json_object(self.path, self.vault)
        result = copy.deepcopy(self._defaults)
        if loaded is not None:
            result.update(loaded)
        return result

    def save(self, settings: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(settings, Mapping):
            raise TypeError("settings must be a mapping")
        result = copy.deepcopy(self._defaults)
        result.update(copy.deepcopy(dict(settings)))
        _atomic_write_json(self.path, result, self.vault)
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
    "save_history": True,
    "remember_drafts": True,
    "clear_on_exit": False,
    "context_window": 16384,
}

_PREFERENCE_CHOICES: dict[str, tuple[str, ...]] = {
    "text_scale": ("system", "compact", "comfortable"),
    "startup_mode": ("remember", "chat", "agent"),
    "reduce_motion": ("system", "reduced", "full"),
}

# Offered as presets; any token count is accepted, these are just the stops
# worth having one click away.
CONTEXT_WINDOW_PRESETS: tuple[int, ...] = (4096, 8192, 16384, 32768, 65536, 131072)

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
        elif isinstance(default, int):
            # ponytail: context_window is the only free-number preference, so
            # its parser is named here rather than behind a per-key table.
            tokens = parse_context_window(value)
            if tokens is not None:
                preferences[key] = tokens
        elif isinstance(value, str) and value in _PREFERENCE_CHOICES[key]:
            preferences[key] = value
    return preferences


def parse_context_window(value: Any) -> Optional[int]:
    """Tokens from whatever the field holds: 24000, "24000", "24k", "16 K".

    Returns None for anything that is not a positive count, so a typo falls
    back to the stored value instead of silently becoming a number.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    text = _as_text_core(value).strip().lower().replace(",", "").replace(" ", "")
    if text.endswith("tokens"):
        text = text[: -len("tokens")]
    multiplier = 1
    if text.endswith("k"):
        multiplier, text = 1024, text[:-1]
    try:
        tokens = int(float(text) * multiplier)
    except ValueError:
        return None
    return tokens if tokens > 0 else None


def context_window_warning(tokens: int) -> str:
    """What is worth saying about a hand-typed context window, or "".

    The real ceiling is the model's own trained window and this machine's KV
    cache, neither of which is known here - so this warns about the ranges
    that go wrong rather than pretending to a number it cannot compute.
    """
    if tokens < 1024:
        return (
            "Below 1k tokens the system prompt alone may not fit; replies get "
            "cut short."
        )
    if tokens > 131072:
        return (
            "Above 128k tokens is beyond what nearly any local model was trained "
            "for; the backend will clamp it or fail to load the model."
        )
    if tokens > 32768:
        return (
            "Above 32k tokens the KV cache grows fast - if it outgrows VRAM the "
            "model spills onto the CPU and slows down."
        )
    return ""


def context_window_tokens(context_window: Any) -> int:
    """Tokens the backend is asked to hold. A bigger window remembers more of
    the conversation and costs proportionally more memory on this machine."""
    return parse_context_window(context_window) or PREFERENCE_DEFAULTS["context_window"]


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


# --- Model discovery ---------------------------------------------------
#
# Making an unknown model one paste away: references are normalized to what
# the pull API understands, and names are screened for the vocabulary of
# refusal-removed models so the workbench can say which is which.

UNCENSORED_MARKERS: tuple[str, ...] = (
    "abliterated",
    "abliterate",
    "uncensored",
    "unfiltered",
    "unaligned",
    "unhinged",
    "brainwash",
    "never-resist",
    "no-refusal",
)

_QUANT_PATTERN = re.compile(r"(?:IQ|Q)\d+[KMSL]?_(?:[A-Z0-9]+_)*[A-Z0-9]+|(?:IQ|Q)\d+[KMSL]?")


def uncensored_marker(*texts: Any) -> Optional[str]:
    """The first refusal-removal marker found in the given texts, if any.

    This is a name screen, not a guarantee: a marker in a model's name or tags
    is strong evidence its refusals were removed, and an absent marker says
    nothing. The UI must present it exactly that way.
    """
    for marker in UNCENSORED_MARKERS:
        for text in texts:
            if marker in _as_text_core(text).casefold():
                return marker
    return None


def _as_text_core(value: Any) -> str:
    return "" if value is None else str(value)


def quant_from_filename(filename: str) -> Optional[str]:
    """The quantization token in a GGUF filename, e.g. Q4_K_M from
    Model-Q4_K_M.gguf."""
    match = _QUANT_PATTERN.search(filename or "")
    return match.group(0) if match else None


def normalize_model_reference(text: str) -> str:
    """Accept an Ollama name, an hf.co path, a Hugging Face URL, or a bare
    user/repo path, and return what the pull API understands."""
    value = _as_text_core(text).strip()
    if not value:
        return value
    lowered = value.casefold()
    if lowered.startswith(("http://", "https://")):
        parsed = urlparse(value)
        host = parsed.netloc.casefold()
        if not (host == "huggingface.co" or host.endswith(".huggingface.co")):
            return value  # unknown hosts pass through; the service answers
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 2:
            return value
        user, repo = parts[0], parts[1].removesuffix(".gguf")
        reference = f"hf.co/{user}/{repo}"
        filename = parts[-1] if len(parts) > 2 else ""
        quant = quant_from_filename(filename)
        return f"{reference}:{quant}" if quant else reference
    if lowered.startswith("hf.co/"):
        return value
    if value.count("/") == 1 and " " not in value:
        user, repo = value.split("/", 1)
        repo = repo.removesuffix(".gguf")
        if user and repo and ":" not in repo:
            return f"hf.co/{user}/{repo}"
    return value


@dataclass(frozen=True)
class CatalogModel:
    """One popular model from the public Hugging Face catalog."""

    id: str
    downloads: int = 0
    likes: int = 0
    uncensored: Optional[str] = None
    gguf: bool = False
    task: str = ""


def parse_hf_catalog(payload: Any, limit: int = 24) -> list[CatalogModel]:
    """Parse the public huggingface.co models API response into catalog rows.

    Junk rows are skipped silently; ordering is the API's (most downloaded
    first) rather than ours.
    """
    if not isinstance(payload, list):
        return []
    entries: list[CatalogModel] = []
    rows = payload if limit is None else payload[: max(0, limit)]
    for item in rows:
        if not isinstance(item, Mapping):
            continue
        model_id = _as_text_core(item.get("id")).strip()
        if not model_id:
            continue
        tags = item.get("tags") if isinstance(item.get("tags"), list) else []
        entries.append(
            CatalogModel(
                id=model_id,
                downloads=int(item.get("downloads") or 0),
                likes=int(item.get("likes") or 0),
                uncensored=uncensored_marker(model_id, *tags),
                gguf=any("gguf" in _as_text_core(tag).casefold() for tag in tags)
                or model_id.casefold().endswith("gguf"),
                task=_as_text_core(item.get("pipeline_tag")).strip(),
            )
        )
    return entries


def format_downloads(count: int) -> str:
    """A compact human form for catalog counts: 1.2M, 340k, 900."""
    value = int(count or 0)
    for divisor, suffix in ((1_000_000, "M"), (1_000, "k")):
        if value >= divisor:
            trimmed = value / divisor
            return f"{trimmed:.1f}".rstrip("0").rstrip(".") + suffix
    return str(value)


HF_CATALOG_URL = "https://huggingface.co/api/models"


def fetch_hf_catalog(port: int, limit: int = 30, session: Any = None) -> list[CatalogModel]:
    """The popular GGUF list through the verified Tor proxy, or not at all.

    socks5h resolves the hostname inside Tor, and there is no direct
    fallback: an unreachable circuit is an honest failure, not a reason to
    leak the machine's address to huggingface.co.
    """
    import requests  # deferred: the pure module stays importable without it

    requester = session if session is not None else requests
    proxies = {
        "http": f"socks5h://127.0.0.1:{port}",
        "https": f"socks5h://127.0.0.1:{port}",
    }
    response = requester.get(
        HF_CATALOG_URL,
        params={
            "filter": "gguf",
            "sort": "downloads",
            "direction": "-1",
            "limit": str(limit),
        },
        proxies=proxies,
        timeout=60,
    )
    status = getattr(response, "status_code", 0)
    if status != 200:
        raise RuntimeError(f"huggingface.co returned HTTP {status} over Tor.")
    return parse_hf_catalog(response.json())


def machine_memory_mb() -> Optional[int]:
    """Total system RAM in MB, or None when the platform won't say."""
    try:
        if os.name == "nt":
            import ctypes

            class MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_uint64),
                    ("ullAvailPhys", ctypes.c_uint64),
                    ("ullTotalPageFile", ctypes.c_uint64),
                    ("ullAvailPageFile", ctypes.c_uint64),
                    ("ullTotalVirtual", ctypes.c_uint64),
                    ("ullAvailVirtual", ctypes.c_uint64),
                    ("ullAvailExtendedVirtual", ctypes.c_uint64),
                ]

            status = MemoryStatusEx()
            status.dwLength = ctypes.sizeof(MemoryStatusEx)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullTotalPhys // (1024 * 1024))
            return None
        with open("/proc/meminfo", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    return int(round(int(line.split()[1]) / 1024))
    except Exception:
        return None
    return None


def gpu_vram_mb() -> Optional[int]:
    """The GPU memory models can use, in MB, or None without a GPU we can read.

    Same probes as gpu_memory_mb: NVIDIA, AMD, Intel, then Apple unified memory.
    """
    return gpu_memory_mb()[1]


_PARAM_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*[bB](?![a-zA-Z0-9])")


def parameter_billions(text: str) -> Optional[float]:
    """The parameter count in a model name: 7 from Qwen2.5-7B-Instruct-GGUF."""
    match = _PARAM_PATTERN.search(text or "")
    return float(match.group(1)) if match else None


def catalog_description(entry: Any, fit_label: str = "") -> str:
    """One plain line describing a catalog row: what it does, how big it is,
    how popular it is. Hugging Face's list API carries no prose description,
    so this is composed from the fields it does return."""
    task = _as_text_core(getattr(entry, "task", "")).replace("-", " ").strip()
    params = parameter_billions(_as_text_core(getattr(entry, "id", "")))
    parts = [task.capitalize() if task else "Local model"]
    if params is not None:
        parts.append(f"{params:g}B parameters")
    if fit_label:
        parts.append(fit_label)
    parts.append(f"{format_downloads(getattr(entry, 'downloads', 0) or 0)} downloads")
    likes = int(getattr(entry, "likes", 0) or 0)
    if likes:
        parts.append(f"{format_downloads(likes)} likes")
    return " · ".join(parts)


def catalog_fit(id_or_name: str, vram_mb: Optional[int], ram_mb: Optional[int]) -> tuple[int, str]:
    """(rank, label) for a catalog row against this machine's memory.

    rank 0 fits this machine, 1 is an unknown size, 2 is beyond it. A Q4 GGUF
    runs roughly 0.6 GB per billion parameters; the estimate carries the usual
    quantization slack (1.15x headroom) rather than pretending precision.
    """
    params = parameter_billions(id_or_name)
    budget = vram_mb if vram_mb else int((ram_mb or 0) * 0.85)
    if params is None or budget <= 0:
        return 1, "size unknown"
    estimated_gb = params * 0.6
    kind = "VRAM" if vram_mb else "RAM"
    shown = f"{estimated_gb:.1f}" if estimated_gb < 10 else f"{estimated_gb:.0f}"
    fits = estimated_gb * 1.15 <= budget / 1024
    if fits:
        return 0, f"~{shown}GB at Q4, fits this machine's {kind}"
    return 2, f"~{shown}GB at Q4, beyond this machine's {kind}"


def shred_file(path: PathInput) -> bool:
    """Overwrite a file's bytes, then remove it. True when it is gone.

    A symlink is unlinked, never written through - the file it points at is
    not this program's to destroy.

    ponytail: overwrite-then-unlink is as far as user space reaches. On an SSD,
    a copy-on-write filesystem, or any volume with snapshots, the original
    blocks can survive untouched; full-disk encryption, or Matchstick's
    RAM-only image, is the real guarantee. TECHNICAL.md states the same limit.
    """
    target = Path(path)
    if target.is_symlink():
        try:
            target.unlink()
            return True
        except OSError:
            return False
    try:
        size = target.stat().st_size
        with open(target, "r+b", buffering=0) as handle:
            written = 0
            while written < size:
                chunk = min(1 << 20, size - written)
                handle.write(os.urandom(chunk))
                written += chunk
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        pass                      # unwritable is not a reason to keep the file
    for attempt in (0, 1):
        try:
            target.unlink()
            return True
        except FileNotFoundError:
            return True
        except PermissionError:
            if attempt:
                return False
            try:
                os.chmod(target, stat.S_IWRITE)    # Windows read-only flag
            except OSError:
                return False
        except OSError:
            return False
    return False


def shred_tree(root: PathInput) -> int:
    """Shred every file under root and remove the directories. Returns the
    number of files removed; a directory that will not go is left behind
    rather than failing the whole wipe."""
    base = Path(root)
    if not base.exists() and not base.is_symlink():
        return 0
    if base.is_symlink() or base.is_file():
        return 1 if shred_file(base) else 0
    removed = 0
    for parent, directories, files in os.walk(base, topdown=False):
        for name in files:
            if shred_file(Path(parent) / name):
                removed += 1
        for name in directories:
            entry = Path(parent) / name
            try:
                entry.unlink() if entry.is_symlink() else entry.rmdir()
            except OSError:
                pass
    try:
        base.rmdir()
    except OSError:
        pass
    return removed


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

    def __init__(self, root: PathInput, vault: Any = None) -> None:
        self.root = Path(root).expanduser()
        self.archive_root = self.root / "archive"
        self.vault = vault

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
        _atomic_write_json(self._path(session.id), session.to_dict(), self.vault)
        return session

    def load(self, session_id: str, *, archived: bool = False) -> ChatSession | None:
        path = self._path(session_id, archived=archived)
        value = _read_json_object(path, self.vault)
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
        payload = normalized.to_dict()
        stored = _read_json_object(target, self.vault)
        if stored is not None and stored.get("messages") == payload["messages"]:
            # Sessions list newest-conversation-first, so a write that adds no
            # history - a rename, a model swap, re-saving one just opened -
            # must not promote it past sessions with newer conversation.
            kept = stored.get("updated_at")
            if isinstance(kept, str) and kept:
                normalized = replace(normalized, updated_at=kept)
                payload["updated_at"] = kept
        _atomic_write_json(target, payload, self.vault)

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
        _atomic_write_json(destination, session.to_dict(), self.vault)
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

    def adopt(self, value: Mapping[str, Any]) -> bool:
        """Store a session from an export as it is - id, timestamps, archive
        state. A session already here under that id, live or archived, is
        never overwritten: returns False and leaves it alone. Raises
        ValueError for a session that does not validate."""
        session_id = value.get("id") if isinstance(value, Mapping) else None
        session = self._session_from_dict(value, expected_id=self._validate_id(session_id))
        if any(self._path(session.id, archived=archived).exists() for archived in (False, True)):
            return False
        target = self._path(session.id, archived=session.archived_at is not None)
        _atomic_write_json(target, session.to_dict(), self.vault)
        return True


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
    "The agent's web traffic goes through Tor: it verifies a circuit before it "
    "starts and refuses to run without one. That covers proxy-aware traffic, not "
    "every command it may run - see below.\n\n"
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


# --- Workbench v2 ------------------------------------------------------
#
# The v2 workbench adds 24 features behind one flag dict. Everything below is
# the Qt-free half: the flag store, the copy the features share, and the pure
# helpers that turn real local state (sessions on disk, git, Ollama's own
# numbers, the agent's stream) into what the UI shows. Nothing here opens a
# network connection; the only processes it starts are git and nvidia-smi.

FEATURE_FLAGS: tuple[tuple[str, str, str], ...] = (
    ("palette", "Command palette", "Ctrl+K jumps to projects, sessions, models and commands."),
    ("boundary", "Boundary map", "A toolbar map of what stays on this machine and what leaves over Tor."),
    ("provenance", "Where replies came from", "Each reply says it was answered on this machine, and whether a Tor search was used."),
    ("webAsk", "Ask before searching the web", "When a question needs the web but Tor or Tor search is off, Onionmind asks first instead of guessing."),
    ("approvals", "Ask before protected actions", "Agent pauses for Allow or Deny when it wants to edit, instead of stopping."),
    ("runPanel", "Run tab", "The inspector gets a Run tab with the agent’s plan, status and output."),
    ("timeline", "Agent steps as a timeline", "Tool activity shows as a compact timeline instead of cards."),
    ("ladder", "Model power details", "The model menu shows what fits this GPU, and switching shows the model loading."),
    ("thinking", "Thinking details", "Adds the privacy-filter step and a “Thought for Ns” line on replies."),
    ("zones", "Activity by zone", "The Activity tab sorts events into Machine, Tor and Outside lanes."),
    ("keyboard", "Keyboard shortcuts page", "Adds a Keyboard tab in Settings. Press ? to open it."),
    ("setup", "First-launch setup", "A guided setup for model, Tor and project that you can run again."),
    ("errors", "Error and recovery", "Clear messages with fix-it buttons when the model server stops, the GPU runs out of memory or a Tor circuit fails."),
    ("msgActions", "Edit, regenerate and branch", "Edit and resend a message, ask again, copy, or start a new session from any reply."),
    ("sessionSearch", "Search past sessions", "The session filter also searches message text across every project."),
    ("contextMeter", "Context-window meter", "Shows how much of the model’s context this session uses, and warns before old messages are trimmed."),
    ("undo", "Undo Agent changes", "Revert one file or all of them from the Changes tab, with Undo."),
    ("rules", "Per-project Agent rules", "Settings tab for what Agent may do without asking, blocked paths and project instructions."),
    ("lock", "Auto-delete and app lock", "Delete old conversations automatically and lock the window with a passphrase."),
    ("netlog", "Network log export", "Export every connection that left this machine as JSON or CSV."),
    ("modelPlus", "Model per mode and live stats", "Remember a tier for Chat and one for Agent, and show speed and memory in the status bar."),
    ("presets", "Presets", "Pick a system prompt such as Code reviewer or Concise from the composer, and edit them in Settings."),
    ("appearance", "Light theme, size and notifications", "Adds a light theme, interface size and notifications to Settings."),
    ("empty", "Empty and first-use states", "Starter prompts in a new task, guidance when nothing has changed, and a no-model warning."),
)

# Every flag starts off except the one that makes the app ask before guessing.
FEATURE_DEFAULTS: dict[str, bool] = {key: key == "webAsk" for key, _name, _desc in FEATURE_FLAGS}


def load_features(settings: Mapping[str, Any]) -> dict[str, bool]:
    """The validated flag view over settings["features"]; junk falls back."""
    stored = settings.get("features") if isinstance(settings, Mapping) else None
    features = dict(FEATURE_DEFAULTS)
    if isinstance(stored, Mapping):
        for key in FEATURE_DEFAULTS:
            if isinstance(stored.get(key), bool):
                features[key] = stored[key]
    return features


# webAsk: the prompt shapes that need the web. A heuristic on the user's own
# words, run locally - it decides only whether to ASK, never to search.
WEB_ASK_PATTERN = re.compile(
    r"\b(latest|newest|newer|current(ly)?|today|this week|news|release[sd]?|version|"
    r"changelog|look (it )?up|search|online|price|docs? for)\b",
    re.IGNORECASE,
)
_WEB_QUERY_LEAD = re.compile(
    r"^(is there|are there|can you|could you|please|what('s| is)|do you know)\s+",
    re.IGNORECASE,
)


def needs_web(text: str) -> bool:
    return bool(WEB_ASK_PATTERN.search(_as_text_core(text)))


def web_query(text: str) -> str:
    """The one query the webAsk card shows: the prompt minus its framing."""
    query = re.sub(r"[?.!]+$", "", _as_text_core(text).strip())
    return _WEB_QUERY_LEAD.sub("", query)[:70]


# presets: the system prompt sent before the user's message. The default text
# is only sent while the presets feature is on; with it off the request is the
# same message list the workbench always sent.
PRESET_DEFAULTS: tuple[dict[str, str], ...] = (
    {"id": "default", "name": "Default", "short": "Balanced answers for everyday questions",
     "text": "You are Onionmind, a private assistant running on this machine. Answer clearly and say when you are unsure."},
    {"id": "review", "name": "Code reviewer", "short": "Looks for bugs, risks and missing tests",
     "text": "Review code like a careful senior engineer. Point out bugs, security risks and missing tests first, then style. Quote the lines you mean."},
    {"id": "concise", "name": "Concise", "short": "Short answers, no extra explanation",
     "text": "Answer in as few words as possible. Use a list only when there are several steps."},
    {"id": "teach", "name": "Explain step by step", "short": "Walks through the reasoning",
     "text": "Explain step by step as if to a developer new to this codebase. Define terms the first time you use them."},
)


def load_presets(settings: Mapping[str, Any]) -> list[dict[str, str]]:
    """The four presets with any edited text applied; ids and names are fixed."""
    edited = settings.get("preset_texts") if isinstance(settings, Mapping) else None
    edited = edited if isinstance(edited, Mapping) else {}
    presets = []
    for preset in PRESET_DEFAULTS:
        item = dict(preset)
        text = edited.get(preset["id"])
        if isinstance(text, str) and text.strip():
            item["text"] = text
        presets.append(item)
    return presets


def active_preset(settings: Mapping[str, Any]) -> dict[str, str]:
    presets = load_presets(settings)
    wanted = settings.get("preset") if isinstance(settings, Mapping) else None
    return next((p for p in presets if p["id"] == wanted), presets[0])


def with_system_prompt(messages: Iterable[Mapping[str, Any]], text: str) -> list[dict[str, Any]]:
    """Messages with one leading system message; an existing one is replaced."""
    items = [dict(m) for m in messages if m.get("role") != "system"]
    return ([{"role": "system", "content": text}] if text.strip() else []) + items


# rules: per-project Agent policy. Stored under settings["agent_rules"] keyed by
# the project's normalized path, so two spellings of one folder share rules.
RULE_FLAGS: tuple[tuple[str, str, str], ...] = (
    ("editTests", "Edit files in tests/ without asking", "Other edits still need your approval."),
    ("runTests", "Run the test suite without asking", "Commands such as pytest, npm test or bundle exec rake test."),
    ("readOnly", "Read-only: never edit files in this project", "Agent can read and run commands, but every edit is refused."),
    ("noNetwork", "No network for Agent in this project", "Even the Tor-only bridge stays closed."),
)
RULE_DEFAULTS: dict[str, Any] = {
    "editTests": False,
    "runTests": True,
    "readOnly": False,
    "noNetwork": False,
    "blocked": ".env, config/credentials.yml.enc",
    "notes": "",
}


def _rule_key(workspace: Any) -> str:
    text = _as_text_core(workspace).strip()
    if not text:
        return ""
    return os.path.normcase(os.path.normpath(os.path.abspath(os.path.expanduser(text))))


def load_rules(settings: Mapping[str, Any], workspace: Any) -> dict[str, Any]:
    stored = settings.get("agent_rules") if isinstance(settings, Mapping) else None
    entry = stored.get(_rule_key(workspace)) if isinstance(stored, Mapping) else None
    rules = dict(RULE_DEFAULTS)
    if isinstance(entry, Mapping):
        for key, default in RULE_DEFAULTS.items():
            value = entry.get(key)
            if isinstance(default, bool) and isinstance(value, bool):
                rules[key] = value
            elif isinstance(default, str) and isinstance(value, str):
                rules[key] = value
    return rules


def store_rules(settings: dict[str, Any], workspace: Any, rules: Mapping[str, Any]) -> dict[str, Any]:
    """Write one project's rules into settings (in place) and return settings."""
    key = _rule_key(workspace)
    if not key:
        return settings
    table = settings.get("agent_rules")
    table = dict(table) if isinstance(table, Mapping) else {}
    clean = dict(RULE_DEFAULTS)
    for name, default in RULE_DEFAULTS.items():
        value = rules.get(name, default)
        clean[name] = bool(value) if isinstance(default, bool) else _as_text_core(value)
    table[key] = clean
    settings["agent_rules"] = table
    return settings


def blocked_paths(rules: Mapping[str, Any]) -> list[str]:
    raw = _as_text_core(rules.get("blocked", ""))
    return [part.strip().replace("\\", "/") for part in raw.split(",") if part.strip()]


# The tools qwen-code treats as edits and as shell commands. A permission
# request names one of these; anything else is not an edit.
AGENT_EDIT_TOOLS = frozenset({"edit", "write_file", "notebook_edit", "replace"})
AGENT_SHELL_TOOLS = frozenset({"run_shell_command", "exec", "monitor"})
_TEST_COMMAND = re.compile(
    r"(^|[\s;&|/])(pytest|py\.test|unittest|tox|nox|npm (run )?test|yarn test|pnpm test|"
    r"go test|cargo test|mvn test|gradlew? test|dotnet test|rake test|bundle exec rake( test)?|"
    r"bundle exec rspec|rspec|phpunit|mix test|ctest|make (check|test))\b|(^|[\s/])tests?/",
    re.IGNORECASE,
)


def _request_path(tool_input: Mapping[str, Any]) -> str:
    for key in ("file_path", "path", "absolute_path", "notebook_path"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _relative_to(path: str, workspace: Any) -> str:
    """path relative to the project when it lies inside it, else as given.

    Compared as normalised strings first, so a forward-slash path from the
    agent still matches on Windows (where "/work/x" is not "absolute"), and
    case-insensitively there; then through the resolved filesystem paths."""
    if not path:
        return ""
    root = _as_text_core(workspace)
    text = path.replace("\\", "/")
    base = root.replace("\\", "/").rstrip("/")
    if base:
        fold = (lambda value: value.casefold()) if os.name == "nt" else (lambda value: value)
        if fold(text).startswith(fold(base) + "/"):
            return text[len(base) + 1:]
        try:
            candidate = Path(path)
            if candidate.is_absolute():
                return candidate.resolve(strict=False).relative_to(
                    Path(root).resolve(strict=False)).as_posix()
        except (OSError, ValueError):
            pass
    return text


def _strip_dot_slash(path: str) -> str:
    while path.startswith("./"):
        path = path[2:]
    return path.lstrip("/")


def _matches_blocked(relative: str, blocked: Iterable[str]) -> Optional[str]:
    import fnmatch

    relative = _strip_dot_slash(relative)
    for pattern in blocked:
        clean = _strip_dot_slash(pattern).rstrip("/")
        if not clean:
            continue
        if (
            relative == clean
            or relative.startswith(clean + "/")
            or fnmatch.fnmatch(relative, clean)
            or fnmatch.fnmatch(relative.rsplit("/", 1)[-1], clean)
        ):
            return pattern
    return None


def rule_decision(
    rules: Mapping[str, Any],
    tool_name: str,
    tool_input: Mapping[str, Any],
    workspace: Any,
) -> tuple[str, str]:
    """('allow' | 'deny' | 'ask', reason) for one agent permission request.

    Deny wins over allow: a blocked path or a read-only project refuses even an
    edit that editTests would otherwise let through. 'ask' means the person
    decides (or, with approvals off, the run stops safely as it always has).
    """
    tool_input = tool_input if isinstance(tool_input, Mapping) else {}
    relative = _relative_to(_request_path(tool_input), workspace)
    hit = _matches_blocked(relative, blocked_paths(rules)) if relative else None
    if hit:
        return "deny", f"{relative} is a blocked path in this project ({hit})"
    if tool_name in AGENT_SHELL_TOOLS:
        command = _as_text_core(tool_input.get("command"))
        for pattern in blocked_paths(rules):
            if pattern and pattern in command:
                return "deny", f"the command touches a blocked path ({pattern})"
        if rules.get("runTests") and _TEST_COMMAND.search(command):
            return "allow", "project rule: run the test suite without asking"
        return "ask", ""
    if tool_name in AGENT_EDIT_TOOLS:
        if rules.get("readOnly"):
            return "deny", "this project is read-only for Agent"
        parts = relative.split("/")
        if rules.get("editTests") and parts and parts[0] in ("tests", "test"):
            return "allow", "project rule: edits in tests/ allowed without asking"
        return "ask", ""
    return "ask", ""


def agent_rule_denials(rules: Mapping[str, Any]) -> list[str]:
    """qwen-code permission deny rules for a project's blocked paths.

    Read rules cover every read tool and edit rules every edit tool, so a
    blocked path is refused by the agent itself - even under YOLO, which never
    reaches the deny list. Patterns are anchored at the project root ("/x"),
    and a bare name also matches at any depth.
    """
    denials: list[str] = []
    for pattern in blocked_paths(rules):
        clean = _strip_dot_slash(pattern).rstrip("/")
        if not clean or any(ch in clean for ch in "()"):
            continue
        anchored = [f"/{clean}"] + ([f"/**/{clean}"] if "/" not in clean else [])
        for spec in anchored:
            denials += [f"read_file({spec})", f"edit({spec})"]
    return denials


# Agent stream: qwen-code's stream-json protocol, read line by line from the
# process the desktop already owns. Non-JSON lines (Onionmind's own banner,
# stderr) come back as text; nothing here trusts the agent's prose as proof.

def parse_agent_line(line: str) -> dict[str, Any]:
    """One stdout line as a typed event: {"kind": ..., ...}."""
    text = _as_text_core(line).strip()
    if not text:
        return {"kind": "blank"}
    if not text.startswith("{"):
        return {"kind": "text", "text": text}
    try:
        message = json.loads(text)
    except ValueError:
        return {"kind": "text", "text": text}
    if not isinstance(message, dict):
        return {"kind": "text", "text": text}
    kind = message.get("type")
    if kind == "control_request":
        request = message.get("request") or {}
        if request.get("subtype") == "can_use_tool":
            return {
                "kind": "permission",
                "request_id": _as_text_core(message.get("request_id")),
                "tool": _as_text_core(request.get("tool_name")),
                "tool_use_id": _as_text_core(request.get("tool_use_id")),
                "input": request.get("input") if isinstance(request.get("input"), dict) else {},
            }
        return {"kind": "control", "subtype": _as_text_core(request.get("subtype")),
                "request_id": _as_text_core(message.get("request_id"))}
    if kind == "control_response":
        response = message.get("response") or {}
        payload = response.get("response")
        return {"kind": "control_response",
                "request_id": _as_text_core(response.get("request_id")),
                "ok": response.get("subtype") == "success",
                "payload": payload if isinstance(payload, dict) else {}}
    if kind == "system":
        return {"kind": "system", "subtype": _as_text_core(message.get("subtype")),
                "session_id": _as_text_core(message.get("session_id"))}
    if kind in ("assistant", "user"):
        content = (message.get("message") or {}).get("content")
        blocks = content if isinstance(content, list) else [{"type": "text", "text": _as_text_core(content)}]
        events: list[dict[str, Any]] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text" and kind == "assistant":
                visible = strip_thinking(_as_text_core(block.get("text")))
                if visible:
                    events.append({"kind": "say", "text": visible})
            elif block_type == "tool_use":
                events.append({"kind": "tool_use", "id": _as_text_core(block.get("id")),
                               "tool": _as_text_core(block.get("name")),
                               "input": block.get("input") if isinstance(block.get("input"), dict) else {}})
            elif block_type == "tool_result":
                result = block.get("content")
                if isinstance(result, list):
                    result = " ".join(_as_text_core(item.get("text")) for item in result if isinstance(item, dict))
                events.append({"kind": "tool_result", "id": _as_text_core(block.get("tool_use_id")),
                               "error": bool(block.get("is_error")),
                               "text": _as_text_core(result)[:2000]})
        return {"kind": "batch", "events": events}
    if kind == "result":
        return {"kind": "result", "ok": not message.get("is_error"),
                "subtype": _as_text_core(message.get("subtype")),
                "text": strip_thinking(_as_text_core(message.get("result"))),
                "duration_ms": message.get("duration_ms"),
                "turns": message.get("num_turns")}
    return {"kind": "other", "type": _as_text_core(kind)}


def agent_initialize_message(request_id: str = "onionmind-init") -> str:
    """The handshake qwen-code waits for before it reads a task (it hangs without one)."""
    return json.dumps({"type": "control_request", "request_id": request_id,
                       "request": {"subtype": "initialize"}}) + "\n"


def agent_task_message(task: str, session_id: str = "onionmind") -> str:
    return json.dumps({"type": "user", "session_id": session_id, "parent_tool_use_id": None,
                       "message": {"role": "user", "content": task}}) + "\n"


def agent_permission_response(
    request_id: str, allow: bool, tool_input: Mapping[str, Any] | None = None, message: str = ""
) -> str:
    response: dict[str, Any] = {"behavior": "allow" if allow else "deny"}
    if allow:
        response["updatedInput"] = dict(tool_input or {})
    else:
        response["message"] = message or "Denied by the person running Onionmind."
    return json.dumps({"type": "control_response", "response": {
        "subtype": "success", "request_id": request_id, "response": response}}) + "\n"


AGENT_CONTEXT_REQUEST = "onionmind-ctx"


def agent_context_usage_message(request_id: str = AGENT_CONTEXT_REQUEST) -> str:
    """Ask qwen how full its context is (it advertises can_get_context_usage)."""
    return json.dumps({"type": "control_request", "request_id": request_id,
                       "request": {"subtype": "get_context_usage"}}) + "\n"


def agent_context_usage(payload: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """{used, window, overhead, estimated} from a get_context_usage answer, else None.

    overhead is what qwen itself adds to every run - its system prompt, tool
    schemas and skills - and so is missing from an estimate made before a run.
    Memory files (AGENTS.md) are left out of it: the estimate counts those already.
    """
    if not isinstance(payload, Mapping) or payload.get("subtype") != "get_context_usage":
        return None
    try:
        used, window = int(payload["totalTokens"]), int(payload["contextWindowSize"])
    except (KeyError, TypeError, ValueError):
        return None
    breakdown = payload.get("breakdown") if isinstance(payload.get("breakdown"), Mapping) else {}
    overhead = 0
    for key in ("systemPrompt", "builtinTools", "mcpTools", "skills"):
        try:
            overhead += max(0, int(breakdown.get(key) or 0))
        except (TypeError, ValueError):
            pass
    return {"used": max(0, used), "window": max(1, window), "overhead": overhead,
            "estimated": bool(payload.get("isEstimated"))}


def agent_interrupt_message(request_id: str = "onionmind-stop") -> str:
    return json.dumps({"type": "control_request", "request_id": request_id,
                       "request": {"subtype": "interrupt"}}) + "\n"


def describe_tool_request(tool: str, tool_input: Mapping[str, Any], workspace: Any = None) -> str:
    """Plain words for what the agent wants: 'edit src/app.py', 'run pytest -q'."""
    tool_input = tool_input if isinstance(tool_input, Mapping) else {}
    if tool in AGENT_SHELL_TOOLS:
        return f"run {_as_text_core(tool_input.get('command')).strip()[:160]}"
    path = _relative_to(_request_path(tool_input), workspace)
    if tool in AGENT_EDIT_TOOLS:
        return f"edit {path}" if path else "edit a file"
    if path:
        return f"{tool.replace('_', ' ')} {path}"
    return tool.replace("_", " ") or "use a tool"


def edit_size(tool: str, tool_input: Mapping[str, Any]) -> tuple[int, int]:
    """(+added, -removed) lines an edit request proposes, from its own input."""
    tool_input = tool_input if isinstance(tool_input, Mapping) else {}
    if tool == "write_file":
        return len(_as_text_core(tool_input.get("content")).splitlines()), 0
    if tool in ("edit", "replace"):
        old = _as_text_core(tool_input.get("old_string")).splitlines()
        new = _as_text_core(tool_input.get("new_string")).splitlines()
        return len(new), len(old)
    return 0, 0


# contextMeter: an honest estimate before a turn (characters / 4, the usual
# rule of thumb), replaced by the model's own prompt_eval_count after one.

def _character_count(part: Any) -> int:
    if isinstance(part, Mapping):
        return _character_count(part.get("content"))
    if isinstance(part, (list, tuple)):
        return sum(_character_count(item) for item in part)
    return len(_as_text_core(part)) if part else 0


def estimate_tokens(*parts: Any) -> int:
    return (_character_count(parts) + 3) // 4


def format_tokens(count: Optional[int]) -> str:
    """'3.1k', '32k': k is 1024 tokens, the unit context windows are sized in
    (and the one the workbench's own context slider already shows)."""
    if count is None:
        return "?"
    if count < 1000:
        return str(int(count))
    value = count / 1024
    return f"{value:.1f}k" if value < 10 else f"{value:.0f}k"


# errors: classify the real failure text the engine already produces.
_ERROR_CLASSES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("oom", re.compile(r"out of memory|cuda error|cudaMalloc|insufficient (video )?memory|"
                       r"requires more system memory|not enough memory|failed to allocate", re.IGNORECASE)),
    ("server", re.compile(r"not running on 127\.0\.0\.1|no model server|connection refused|"
                          r"model service is not running|could not reach the model", re.IGNORECASE)),
    ("tor", re.compile(r"\btor\b.*(circuit|verif|not reachable|unavailable|failed)|"
                       r"socks|could not verify", re.IGNORECASE)),
)


def classify_error(text: str) -> Optional[str]:
    """'server' | 'oom' | 'tor' | None for a chat failure message."""
    message = _as_text_core(text)
    for key, pattern in _ERROR_CLASSES:
        if pattern.search(message):
            return key
    return None


# sessions: search across every project, age-based deletion, branching.

def _session_value(session: Any, name: str, default: Any = None) -> Any:
    if isinstance(session, Mapping):
        return session.get(name, default)
    return getattr(session, name, default)


def search_sessions(
    sessions: Iterable[Any], query: str, limit: int = 8, before: int = 28, after: int = 40
) -> list[dict[str, Any]]:
    """Message hits across every saved session, newest session first.

    Each hit carries the snippet split around the match so the UI can
    highlight it without re-searching. Reasoning was stripped at save time,
    so only visible text is searched.
    """
    needle = _as_text_core(query).strip()
    if len(needle) < 2:
        return []
    folded = needle.casefold()
    hits: list[dict[str, Any]] = []
    for session in sessions:
        for index, message in enumerate(_session_value(session, "messages", ()) or ()):
            if not isinstance(message, Mapping) or message.get("role") not in ("user", "assistant"):
                continue
            content = message.get("content")
            if not isinstance(content, str):
                continue
            position = content.casefold().find(folded)
            if position < 0:
                continue
            start = max(0, position - before)
            end = min(len(content), position + len(needle) + after)
            hits.append({
                "session_id": _as_text_core(_session_value(session, "id")),
                "title": _as_text_core(_session_value(session, "title", "Session")),
                "workspace": _session_value(session, "workspace"),
                "message_index": index,
                "before": ("…" if start else "") + content[start:position].replace("\n", " "),
                "match": content[position:position + len(needle)],
                "after": content[position + len(needle):end].replace("\n", " ") + ("…" if end < len(content) else ""),
            })
            break                                # one hit per session keeps the list scannable
        if len(hits) >= limit:
            break
    return hits


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        stamp = value
    else:
        try:
            stamp = datetime.fromisoformat(_as_text_core(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def sessions_older_than(sessions: Iterable[Any], days: int, now: Optional[datetime] = None) -> list[Any]:
    """Sessions whose last change is more than `days` days ago."""
    if not days or days <= 0:
        return []
    now = now or datetime.now(timezone.utc)
    old = []
    for session in sessions:
        stamp = _parse_timestamp(_session_value(session, "updated_at"))
        if stamp is not None and (now - stamp).total_seconds() > days * 86400:
            old.append(session)
    return old


AUTO_DELETE_CHOICES: tuple[int, ...] = (0, 1, 7, 30)
LOCK_AFTER_CHOICES: tuple[int, ...] = (0, 5, 15, 60)


# lock: a passphrase is stored only as a scrypt hash with its own salt.

def hash_passphrase(passphrase: str, salt: Optional[bytes] = None) -> dict[str, str]:
    salt = salt if salt is not None else os.urandom(16)
    digest = hashlib.scrypt(passphrase.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return {"kdf": "scrypt", "salt": salt.hex(), "hash": digest.hex()}


def verify_passphrase(passphrase: str, record: Mapping[str, Any]) -> bool:
    import hmac

    try:
        salt = bytes.fromhex(_as_text_core(record.get("salt")))
        expected = bytes.fromhex(_as_text_core(record.get("hash")))
    except (AttributeError, ValueError):
        return False
    if record.get("kdf") != "scrypt" or not salt or not expected:
        return False
    digest = hashlib.scrypt(passphrase.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return hmac.compare_digest(digest, expected)


# --- Encryption at rest --------------------------------------------------
#
# Everything under the desktop data folder (settings, sessions, the network
# log, revert backups) is AES-256-GCM encrypted with one random data key.
# vault.json beside it is the only plaintext file and holds no secret: which
# key store keeps the data key, or - once the person sets a password - the
# data key wrapped with a key derived from that password (scrypt). A password
# is optional; without one the operating system's key store releases the key
# to this user account, so a copied disk or another account reads nothing,
# but programs running as this same user could ask for it. With a password the
# key is nowhere on disk in usable form, and a forgotten password means the
# data is gone: there is no recovery, by design.

VAULT_FILE = "vault.json"
_ENC_MAGIC = b"OMENC1\n"
_ENC_LINE = "enc1:"
_ENC_AAD = b"onionmind/v1"
_NONCE_BYTES = 12
_SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1}


class VaultError(RuntimeError):
    """The data folder's encryption could not be opened or changed."""


class WrongPassword(VaultError):
    pass


class KeystoreUnavailable(VaultError):
    pass


class VaultLocked(VaultError):
    """The vault was locked: its key is out of memory until the password is typed."""


def encryption_available() -> bool:
    """True when the `cryptography` package (AES-GCM) can be imported."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
    except Exception:
        return False
    return True


def _aesgcm(key: bytes) -> Any:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    return AESGCM(key)


def _key_tag(root: Path) -> str:
    """A stable, non-identifying name for one data folder's key."""
    return hashlib.sha256(str(Path(root).expanduser().resolve()).encode("utf-8")).hexdigest()[:16]


class FileKeystore:
    """Fallback: a key file only this user can read, kept outside the data folder.

    It stops another account and a copied data folder, not a program running as
    this user - the settings say so and recommend a password."""

    name = "file"
    label = "a key file only your user account can read"
    strong = False

    def __init__(self, tag: str, directory: Optional[Path] = None) -> None:
        base = directory or Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "onionmind" / "keys"
        self.path = base / f"{tag}.key"

    def get(self) -> Optional[bytes]:
        try:
            value = bytes.fromhex(self.path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            return None
        return value if len(value) == 32 else None

    def put(self, key: bytes) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
        descriptor = os.open(self.path, flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write(key.hex())
        os.chmod(self.path, 0o600)

    def delete(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


class SecretServiceKeystore:
    """Linux desktops: GNOME Keyring / KWallet through `secret-tool` (libsecret).
    The key goes in on stdin, never on a command line."""

    name = "secret-service"
    label = "your desktop keyring (Secret Service)"
    strong = True

    def __init__(self, tag: str) -> None:
        self.attributes = ["service", "onionmind", "key", tag]

    def _run(self, *args: str, data: Optional[str] = None) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(["secret-tool", *args, *self.attributes], input=data, capture_output=True,
                                  text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise KeystoreUnavailable(f"secret-tool failed: {exc}") from exc

    def get(self) -> Optional[bytes]:
        result = self._run("lookup")
        try:
            value = bytes.fromhex(result.stdout.strip()) if result.returncode == 0 else b""
        except ValueError:
            return None
        return value if len(value) == 32 else None

    def put(self, key: bytes) -> None:
        result = self._run("store", "--label=Onionmind data key", data=key.hex())
        if result.returncode != 0 or self.get() != key:
            raise KeystoreUnavailable(result.stderr.strip() or "the keyring did not keep the key")

    def delete(self) -> None:
        self._run("clear")


class MacKeychain:
    """macOS: the login keychain through `security`. Commands go in on stdin
    (`security -i`), so the key never appears in a process listing."""

    name = "keychain"
    label = "the macOS Keychain"
    strong = True

    def __init__(self, tag: str) -> None:
        self.service, self.account = "Onionmind", f"data-key-{tag}"

    def _run(self, args: list[str], data: Optional[str] = None) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(["security", *args], input=data, capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise KeystoreUnavailable(f"security failed: {exc}") from exc

    def get(self) -> Optional[bytes]:
        result = self._run(["find-generic-password", "-s", self.service, "-a", self.account, "-w"])
        try:
            value = bytes.fromhex(result.stdout.strip()) if result.returncode == 0 else b""
        except ValueError:
            return None
        return value if len(value) == 32 else None

    def put(self, key: bytes) -> None:
        command = (f"add-generic-password -U -s {self.service} -a {self.account} "
                   f"-l \"Onionmind data key\" -w {key.hex()}\n")
        result = self._run(["-i"], data=command)
        if result.returncode != 0 or self.get() != key:
            raise KeystoreUnavailable(result.stderr.strip() or "the Keychain did not keep the key")

    def delete(self) -> None:
        self._run(["delete-generic-password", "-s", self.service, "-a", self.account])


class DpapiKeystore:
    """Windows: the key sealed with DPAPI (CryptProtectData) for this user
    account, stored beside vault.json. The sealed blob is useless to another
    account or on another machine."""

    name = "dpapi"
    label = "Windows Data Protection (DPAPI) for your account"
    strong = True

    def __init__(self, path: Path) -> None:
        self.path = path

    @staticmethod
    def _call(function_name: str, data: bytes) -> bytes:
        import ctypes
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        function = getattr(crypt32, function_name)
        function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                             ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        function.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        buffer = ctypes.create_string_buffer(data, len(data))
        source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
        result = Blob()
        # 0x1 = CRYPTPROTECT_UI_FORBIDDEN: never show a prompt.
        if not function(ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(result)):
            raise KeystoreUnavailable(f"{function_name} failed ({ctypes.get_last_error()})")
        try:
            return ctypes.string_at(result.pbData, result.cbData)
        finally:
            kernel32.LocalFree(result.pbData)

    def get(self) -> Optional[bytes]:
        try:
            value = self._call("CryptUnprotectData", self.path.read_bytes())
        except (OSError, KeystoreUnavailable):
            return None
        return value if len(value) == 32 else None

    def put(self, key: bytes) -> None:
        sealed = self._call("CryptProtectData", key)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_bytes(sealed)
        os.replace(temporary, self.path)
        if self.get() != key:
            raise KeystoreUnavailable("DPAPI did not return the key it sealed")

    def delete(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def keystore_for(root: PathInput, name: Optional[str] = None) -> Any:
    """The key store a vault uses: `name` from its header, else this platform's best."""
    root = Path(root).expanduser()
    tag = _key_tag(root)
    choice = name or ("dpapi" if os.name == "nt" else
                      "keychain" if sys.platform == "darwin" else
                      "secret-service" if shutil.which("secret-tool") else "file")
    if choice == "dpapi":
        return DpapiKeystore(root / "device-key.dpapi")
    if choice == "keychain":
        return MacKeychain(tag)
    if choice == "secret-service":
        return SecretServiceKeystore(tag)
    return FileKeystore(tag)


def _wrap_key(key: bytes, password: str) -> dict[str, Any]:
    salt, nonce = os.urandom(16), os.urandom(_NONCE_BYTES)
    kek = hashlib.scrypt(password.encode("utf-8"), salt=salt, dklen=32, maxmem=128 * 1024 * 1024, **_SCRYPT)
    wrapped = _aesgcm(kek).encrypt(nonce, key, b"onionmind/key")
    return {"kdf": "scrypt", **_SCRYPT, "salt": salt.hex(), "nonce": nonce.hex(), "wrapped": wrapped.hex()}


def _unwrap_key(record: Mapping[str, Any], password: str) -> bytes:
    from cryptography.exceptions import InvalidTag

    try:
        salt, nonce = bytes.fromhex(record["salt"]), bytes.fromhex(record["nonce"])
        wrapped = bytes.fromhex(record["wrapped"])
        params = {key: int(record[key]) for key in ("n", "r", "p")}
    except (KeyError, TypeError, ValueError) as exc:
        raise VaultError("vault.json is damaged") from exc
    if record.get("kdf") != "scrypt":
        raise VaultError("vault.json names an unknown key derivation")
    kek = hashlib.scrypt(password.encode("utf-8"), salt=salt, dklen=32, maxmem=256 * 1024 * 1024, **params)
    try:
        return _aesgcm(kek).decrypt(nonce, wrapped, b"onionmind/key")
    except InvalidTag:
        raise WrongPassword("That password is not right.") from None


class Vault:
    """An open vault: the data key in memory, and the header describing it."""

    def __init__(self, root: PathInput, key: bytes, header: Mapping[str, Any], keystore: Any = None) -> None:
        self.root = Path(root).expanduser()
        self._key: Optional[bytes] = key
        self._cipher: Any = _aesgcm(key)
        self.header = dict(header)
        self.keystore = keystore

    # lock

    @property
    def locked(self) -> bool:
        return self._key is None

    def lock(self) -> bool:
        """Drop the data key from memory; only the password brings it back.

        Only with a password: without one the key store would hand the key
        straight back, so there is nothing to lock. Python cannot overwrite the
        old bytes, so this drops every reference and leaves them to the
        allocator. Returns True when the key was dropped."""
        if not self.has_password:
            return False
        self._key = None
        self._cipher = None
        return True

    def unlock(self, password: str) -> None:
        """Bring the key back from the password. Raises WrongPassword."""
        if not self.locked:
            return
        key = _unwrap_key(self.header["password"], password)
        self._key, self._cipher = key, _aesgcm(key)

    def _require_key(self) -> tuple[bytes, Any]:
        if self._key is None:
            raise VaultLocked("Onionmind is locked; unlock it to read or write its data.")
        return self._key, self._cipher

    # data

    def encrypt(self, data: bytes) -> bytes:
        _, cipher = self._require_key()
        nonce = os.urandom(_NONCE_BYTES)
        return _ENC_MAGIC + nonce + cipher.encrypt(nonce, data, _ENC_AAD)

    def decrypt(self, blob: bytes) -> bytes:
        from cryptography.exceptions import InvalidTag

        if not is_encrypted(blob):
            raise VaultError("not an Onionmind encrypted file")
        _, cipher = self._require_key()
        start = len(_ENC_MAGIC)
        try:
            return cipher.decrypt(blob[start:start + _NONCE_BYTES], blob[start + _NONCE_BYTES:], _ENC_AAD)
        except InvalidTag:
            raise VaultError("the file was not encrypted with this data key, or it was changed") from None

    def encrypt_line(self, text: str) -> str:
        import base64

        return _ENC_LINE + base64.b64encode(self.encrypt(text.encode("utf-8"))).decode("ascii")

    def decrypt_line(self, line: str) -> str:
        import base64

        if not line.startswith(_ENC_LINE):
            return line
        return self.decrypt(base64.b64decode(line[len(_ENC_LINE):])).decode("utf-8")

    # password

    @property
    def mode(self) -> str:
        return "password" if isinstance(self.header.get("password"), dict) else "device"

    @property
    def has_password(self) -> bool:
        return self.mode == "password"

    def describe(self) -> str:
        if self.has_password:
            return "Encrypted (AES-256-GCM). The key is unlocked by your password, which is never stored."
        store = self.keystore or keystore_for(self.root, self.header.get("keystore"))
        text = f"Encrypted (AES-256-GCM). The key is kept in {store.label}."
        if not store.strong:
            text += " Set a password for full protection: without one, programs running as you could read that file."
        return text

    def check_password(self, password: str) -> bool:
        record = self.header.get("password")
        if not isinstance(record, dict):
            return False
        try:
            key = _unwrap_key(record, password)
        except VaultError:
            return False
        return self._key is None or key == self._key

    def set_password(self, password: str, current: Optional[str] = None) -> None:
        """Set or change the password. Changing needs the current one."""
        if not password:
            raise VaultError("The password cannot be empty.")
        if self.has_password and not self.check_password(current or ""):
            raise WrongPassword("The current password is not right.")
        key, _ = self._require_key()
        header = {**self.header, "mode": "password", "password": _wrap_key(key, password)}
        header.pop("keystore", None)
        _write_header(self.root, header)            # the wrapped key first...
        old = self.header.get("keystore")
        self.header = header
        if old:                                     # ...then the unwrapped copy goes
            try:
                keystore_for(self.root, old).delete()
            except KeystoreUnavailable:
                pass
        self.keystore = None

    def remove_password(self, current: str) -> None:
        """Back to the key store: the data stays encrypted, no login needed."""
        if not self.check_password(current):
            raise WrongPassword("That password is not right.")
        key, _ = self._require_key()
        store = keystore_for(self.root)
        store.put(key)                        # the key store first...
        header = {key: value for key, value in self.header.items() if key != "password"}
        header.update(mode="device", keystore=store.name)
        _write_header(self.root, header)            # ...then the header stops asking
        self.header, self.keystore = header, store


def is_encrypted(blob: bytes) -> bool:
    return blob[:len(_ENC_MAGIC)] == _ENC_MAGIC


def _write_header(root: Path, header: Mapping[str, Any]) -> None:
    _atomic_write_json(Path(root) / VAULT_FILE, header)


def vault_header(root: PathInput) -> Optional[dict[str, Any]]:
    """The plaintext header, or None when this data folder was never encrypted."""
    path = Path(root).expanduser() / VAULT_FILE
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise VaultError(f"{path} is unreadable") from exc
    if not isinstance(value, dict) or value.get("version") != 1:
        raise VaultError(f"{path} is not a vault header this version understands")
    return value


def vault_needs_password(root: PathInput) -> bool:
    header = vault_header(root)
    return bool(header and isinstance(header.get("password"), dict))


def open_vault(root: PathInput, password: Optional[str] = None, keystore: Any = None) -> Vault:
    """Open (or, the first time, create) the vault of a data folder.

    First run: a new random data key goes into the platform key store and every
    plaintext file already in the folder is encrypted in place. Afterwards the
    key comes from the key store, or from `password` once one is set. Raises
    WrongPassword, KeystoreUnavailable or VaultError - never falls back to
    plaintext on its own."""
    if not encryption_available():
        raise VaultError("The cryptography package is missing; run the updater to install it.")
    root = Path(root).expanduser()
    header = vault_header(root)
    if header is None:
        key = os.urandom(32)
        store = keystore or keystore_for(root)
        try:
            store.put(key)
        except KeystoreUnavailable:
            if store.name == "file":
                raise
            store = FileKeystore(_key_tag(root))
            store.put(key)
        header = {"version": 1, "cipher": "AES-256-GCM", "mode": "device", "keystore": store.name,
                  "created_at": _utc_now()}
        _write_header(root, header)
        vault = Vault(root, key, header, store)
        encrypt_data_folder(root, vault)
        return vault
    if isinstance(header.get("password"), dict):
        if password is None:
            raise WrongPassword("A password is needed to open Onionmind's data.")
        return Vault(root, _unwrap_key(header["password"], password), header)
    store = keystore or keystore_for(root, header.get("keystore"))
    key = store.get()
    if key is None:
        raise KeystoreUnavailable(
            f"The data key is missing from {store.label}. Without it the encrypted data cannot be read.")
    vault = Vault(root, key, header, store)
    encrypt_data_folder(root, vault)                # anything an older version left in plaintext
    return vault


_VAULT_OWN_FILES = {VAULT_FILE, "device-key.dpapi"}


def encrypt_data_folder(root: PathInput, vault: Vault) -> int:
    """Encrypt in place every plaintext file under the data folder. Returns the count."""
    root = Path(root).expanduser()
    done = 0
    if not root.is_dir():
        return done
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink() or path.name in _VAULT_OWN_FILES and path.parent == root:
            continue
        if path.name.endswith(".tmp"):
            continue
        try:
            with path.open("rb") as handle:
                if is_encrypted(handle.read(len(_ENC_MAGIC))) and path.suffix != ".jsonl":
                    continue
            raw = path.read_bytes()
        except OSError:
            continue
        if path.suffix == ".jsonl":
            lines = raw.decode("utf-8", "replace").splitlines()
            if all(line.startswith(_ENC_LINE) or not line.strip() for line in lines):
                continue
            body = "".join(vault.encrypt_line(line) + "\n" for line in lines if line.strip())
            _atomic_write_bytes(path, body.encode("ascii"))
        elif is_encrypted(raw):
            continue
        else:
            _atomic_write_bytes(path.with_name(f".{path.name}.migrating.tmp"), vault.encrypt(raw))
            # Overwrite the plaintext bytes before the encrypted copy takes the
            # name, so they do not simply linger in freed blocks (same limits as
            # shred_file: SSDs and copy-on-write filesystems may keep a copy).
            try:
                with open(path, "r+b", buffering=0) as handle:
                    handle.write(os.urandom(len(raw)))
                    os.fsync(handle.fileno())
            except OSError:
                pass
            os.replace(path.with_name(f".{path.name}.migrating.tmp"), path)
        done += 1
    return done


def erase_data_folder(root: PathInput) -> None:
    """Forgotten password: delete the encrypted data, its header and the key."""
    root = Path(root).expanduser()
    if not (root / VAULT_FILE).is_file():
        raise VaultError(f"{root} is not an Onionmind data folder")
    try:
        header = vault_header(root) or {}
    except VaultError:
        header = {}
    if header.get("keystore"):
        try:
            keystore_for(root, header["keystore"]).delete()
        except KeystoreUnavailable:
            pass
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False)
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def read_data_file(path: PathInput, vault: Optional[Vault]) -> bytes:
    """A file from the data folder, decrypted when it is encrypted."""
    raw = Path(path).read_bytes()
    if is_encrypted(raw):
        if vault is None:
            raise VaultError("this file is encrypted and the vault is not open")
        return vault.decrypt(raw)
    return raw


def write_data_file(path: PathInput, data: bytes, vault: Optional[Vault]) -> None:
    """Write a file into the data folder, encrypted when a vault is open."""
    _atomic_write_bytes(Path(path), vault.encrypt(data) if vault is not None else data)


# export: one password-protected file with the sessions and settings, to move
# them to another machine or keep a backup. It does not depend on the data
# key, so it opens anywhere with the password alone. PBKDF2-HMAC-SHA256
# rather than the vault's scrypt because the phone (javax.crypto) has it too,
# and the same file imports on either side. Layout:
#   OMEXPORT1\n  <header JSON, one line>\n  <12-byte nonce><AES-256-GCM ciphertext>
# The magic and header line are the associated data, so changing the KDF
# parameters breaks the tag instead of weakening the key.

EXPORT_MAGIC = b"OMEXPORT1\n"
EXPORT_SUFFIX = ".onionmind"
EXPORT_PBKDF2_ITERATIONS = 600_000


class ExportError(VaultError):
    """An export file that is not one, is damaged, or needs another password."""


def _export_key(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, dklen=32)


def export_bytes(password: str, payload: Mapping[str, Any]) -> bytes:
    import base64

    if not password:
        raise ExportError("The export password cannot be empty.")
    salt, nonce = os.urandom(16), os.urandom(_NONCE_BYTES)
    header = json.dumps({"format": "onionmind-export", "version": 1, "kdf": "PBKDF2WithHmacSHA256",
                         "iterations": EXPORT_PBKDF2_ITERATIONS,
                         "salt": base64.b64encode(salt).decode("ascii")}, separators=(",", ":"))
    preamble = EXPORT_MAGIC + header.encode("ascii") + b"\n"
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    key = _export_key(password, salt, EXPORT_PBKDF2_ITERATIONS)
    return preamble + nonce + _aesgcm(key).encrypt(nonce, body, preamble)


def read_export_bytes(blob: bytes, password: str) -> dict[str, Any]:
    import base64
    from cryptography.exceptions import InvalidTag

    if not blob.startswith(EXPORT_MAGIC):
        raise ExportError("This is not an Onionmind export file.")
    end = blob.find(b"\n", len(EXPORT_MAGIC))
    try:
        header = json.loads(blob[len(EXPORT_MAGIC):end].decode("ascii"))
        salt = base64.b64decode(header["salt"], validate=True)
        iterations = int(header["iterations"])
    except (ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
        raise ExportError("The export file is damaged.") from exc
    if end < 0 or header.get("format") != "onionmind-export" or header.get("version") != 1:
        raise ExportError("This export was made by a newer Onionmind; update to import it.")
    if header.get("kdf") != "PBKDF2WithHmacSHA256" or not 100_000 <= iterations <= 10_000_000:
        raise ExportError("The export file names a key derivation this version does not accept.")
    preamble, rest = blob[:end + 1], blob[end + 1:]
    if len(rest) < _NONCE_BYTES + 16:
        raise ExportError("The export file is damaged.")
    key = _export_key(password, salt, iterations)
    try:
        body = _aesgcm(key).decrypt(rest[:_NONCE_BYTES], rest[_NONCE_BYTES:], preamble)
    except InvalidTag:
        raise WrongPassword("That password does not open this export, or the file was changed.") from None
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ExportError("The export file is damaged.")
    return payload


def export_payload(sessions: Iterable[Any], settings: Optional[Mapping[str, Any]] = None,
                   source: str = "desktop") -> dict[str, Any]:
    """What an export carries: every session (archived ones too) and the
    settings, minus anything that only means something on this machine."""
    rows = [session.to_dict() if hasattr(session, "to_dict") else dict(session) for session in sessions]
    clean = copy.deepcopy(dict(settings or {}))
    lock = clean.get("lock")
    if isinstance(lock, dict):
        lock.pop("passphrase", None)                # an old hash; never travels
    return {"version": 1, "from": source, "exported_at": _utc_now(), "sessions": rows, "settings": clean}


def write_export(path: PathInput, password: str, store: SessionStore,
                 settings: Optional[Mapping[str, Any]] = None) -> int:
    """Write every session in `store` and `settings` to an export file. Returns the session count."""
    sessions = store.list() + store.list(archived=True)
    payload = export_payload(sessions, settings)
    _atomic_write_bytes(Path(path).expanduser(), export_bytes(password, payload))
    return len(sessions)


def read_export(path: PathInput, password: str) -> dict[str, Any]:
    try:
        blob = Path(path).expanduser().read_bytes()
    except OSError as exc:
        raise ExportError(f"The export file could not be read: {exc.strerror or exc}") from exc
    return read_export_bytes(blob, password)


def import_sessions(store: SessionStore, payload: Mapping[str, Any]) -> dict[str, int]:
    """Add the export's sessions to `store`. Ones already here are kept as they
    are; ones that do not validate are skipped. Counts: added, existing, invalid."""
    counts = {"added": 0, "existing": 0, "invalid": 0}
    rows = payload.get("sessions")
    for row in rows if isinstance(rows, list) else []:
        try:
            counts["added" if store.adopt(row) else "existing"] += 1
        except (TypeError, ValueError):
            counts["invalid"] += 1
    return counts


def imported_settings(current: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    """`current` with the export's settings laid over it, for a desktop export only:
    the phone's settings are different knobs."""
    merged = copy.deepcopy(dict(current))
    settings = payload.get("settings")
    if payload.get("from") == "desktop" and isinstance(settings, dict):
        merged.update(copy.deepcopy(settings))
    return merged


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


# undo: revert files to their last commit with a recoverable backup, so Undo
# puts back exactly what was on disk - including files git never tracked.

@dataclass(frozen=True)
class RevertRecord:
    root: str
    backup: str
    paths: tuple[str, ...]
    untracked: tuple[str, ...]


def _safe_relative(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve(strict=False)
    candidate.relative_to(root)                   # ValueError when it escapes
    return candidate


def _backup_copy(source: Path, target: Path, vault: Any) -> None:
    """copy2, encrypted when a vault is open; the file mode rides on the backup."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if vault is None:
        shutil.copy2(source, target)
        return
    write_data_file(target, source.read_bytes(), vault)
    shutil.copystat(source, target)


def _restore_copy(source: Path, target: Path, vault: Any) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(read_data_file(source, vault))
    shutil.copystat(source, target)


def revert_changes(
    selected: PathInput, changes: Iterable[Any], backup_root: PathInput, vault: Any = None
) -> RevertRecord:
    """Back up each changed file, then restore it from HEAD (or remove it when untracked).

    `changes` are WorkspaceChange-like (status, path). The backup lives under
    backup_root, outside the project, until the next revert replaces it.
    """
    root = WorkspaceInspector._root(selected)
    backup = Path(backup_root).expanduser() / uuid4().hex
    tracked: list[str] = []
    untracked: list[str] = []
    for change in changes:
        status = _as_text_core(_session_value(change, "status", ""))
        relative = _as_text_core(_session_value(change, "path", ""))
        if not relative:
            continue
        source = _safe_relative(root, relative)
        (untracked if status == "??" else tracked).append(relative)
        if source.is_file():
            _backup_copy(source, backup / relative, vault)
    if tracked:
        result = WorkspaceInspector._git(root, "restore", "--source=HEAD", "--staged", "--worktree", "--", *tracked)
        if result.returncode != 0:
            # A file added to the index with no HEAD version cannot be restored
            # from HEAD; unstage it and treat it as untracked instead.
            WorkspaceInspector._git(root, "restore", "--staged", "--", *tracked)
            for relative in list(tracked):
                if WorkspaceInspector._git(root, "cat-file", "-e", f"HEAD:{relative}").returncode != 0:
                    tracked.remove(relative)
                    untracked.append(relative)
            if tracked:
                retry = WorkspaceInspector._git(root, "restore", "--source=HEAD", "--worktree", "--", *tracked)
                if retry.returncode != 0:
                    raise RuntimeError(retry.stderr.decode("utf-8", "replace").strip() or "git restore failed")
    for relative in untracked:
        target = _safe_relative(root, relative)
        if target.is_file() or target.is_symlink():
            target.unlink()
    (backup / ".onionmind-revert.json").parent.mkdir(parents=True, exist_ok=True)
    record = RevertRecord(str(root), str(backup), tuple(tracked + untracked), tuple(untracked))
    _atomic_write_json(backup / ".onionmind-revert.json",
                       {"root": record.root, "paths": list(record.paths), "untracked": list(record.untracked)},
                       vault)
    return record


def undo_revert(record: RevertRecord, vault: Any = None) -> int:
    """Copy the backed-up files back into the project. Returns files restored."""
    root = Path(record.root)
    backup = Path(record.backup)
    restored = 0
    for relative in record.paths:
        source = backup / relative
        if not source.is_file():
            continue
        _restore_copy(source, _safe_relative(root, relative), vault)
        restored += 1
    return restored


@dataclass(frozen=True)
class DiffHunk:
    index: int
    header: str                                   # the "@@ -a,b +c,d @@ ..." line
    lines: tuple[str, ...]                        # body lines, each with its +/-/space prefix
    added: int
    removed: int


_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@")


def _file_diff(root: Path, relative: str) -> tuple[str, list[DiffHunk]]:
    """(file header, hunks) of one tracked file's worktree diff against HEAD."""
    result = WorkspaceInspector._git(root, "diff", "--no-ext-diff", "--no-textconv", "--no-color",
                                     "-U3", "HEAD", "--", relative)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", "replace").strip() or "git diff failed")
    text = result.stdout.decode("utf-8", "surrogateescape")
    header: list[str] = []
    hunks: list[DiffHunk] = []
    current: Optional[list[str]] = None
    for line in text.splitlines(keepends=True):
        if _HUNK_HEADER.match(line):
            if current is not None:
                hunks.append(_make_hunk(len(hunks), current))
            current = [line]
        elif current is None:
            if line.startswith(("Binary files", "GIT binary patch")):
                raise ValueError(f"{relative} is binary; revert the whole file instead")
            header.append(line)
        else:
            current.append(line)
    if current is not None:
        hunks.append(_make_hunk(len(hunks), current))
    return "".join(header), hunks


def _make_hunk(index: int, raw: list[str]) -> DiffHunk:
    body = tuple(raw[1:])
    return DiffHunk(index, raw[0].rstrip("\n"), body,
                    sum(1 for line in body if line.startswith("+")),
                    sum(1 for line in body if line.startswith("-")))


def diff_hunks(selected: PathInput, relative: str) -> list[DiffHunk]:
    """The hunks of one changed, tracked file, for choosing which to revert."""
    root = WorkspaceInspector._root(selected)
    _safe_relative(root, relative)
    return _file_diff(root, relative)[1]


def revert_hunks(selected: PathInput, relative: str, indices: Iterable[int],
                 backup_root: PathInput, vault: Any = None) -> RevertRecord:
    """Revert only the chosen hunks of one file (git apply -R), with the same
    whole-file backup as revert_changes so Undo puts back exactly what was there.

    The diff is re-read here, not trusted from the UI: when the file changed
    since the hunks were shown, the patch no longer applies and nothing is touched.
    """
    root = WorkspaceInspector._root(selected)
    source = _safe_relative(root, relative)
    header, hunks = _file_diff(root, relative)
    wanted = sorted(set(int(i) for i in indices))
    if not wanted or any(i < 0 or i >= len(hunks) for i in wanted):
        raise ValueError("no such hunk - refresh Changes and choose again")
    backup = Path(backup_root).expanduser() / uuid4().hex
    backup.mkdir(parents=True, exist_ok=True)
    if source.is_file():
        _backup_copy(source, backup / relative, vault)
    # The patch is the project's own diff, already in plaintext in the project;
    # it exists only for the git apply below.
    patch = backup / ".onionmind-hunks.patch"
    body = header + "".join(hunks[i].header + "\n" + "".join(hunks[i].lines) for i in wanted)
    patch.write_bytes(body.encode("utf-8", "surrogateescape"))
    for check in (("--check",), ()):
        result = WorkspaceInspector._git(root, "apply", "-R", *check, "--whitespace=nowarn", str(patch))
        if result.returncode != 0:
            shutil.rmtree(backup, ignore_errors=True)
            raise RuntimeError(result.stderr.decode("utf-8", "replace").strip() or "git apply failed")
    patch.unlink()
    record = RevertRecord(str(root), str(backup), (relative,), ())
    _atomic_write_json(backup / ".onionmind-revert.json",
                       {"root": record.root, "paths": [relative], "untracked": [], "hunks": wanted}, vault)
    return record


def change_stats(selected: PathInput) -> dict[str, tuple[int, int]]:
    """{path: (+added, -removed)} from `git diff --numstat HEAD`, empty when unknown."""
    try:
        root = WorkspaceInspector._root(selected)
        result = WorkspaceInspector._git(root, "diff", "--numstat", "--no-ext-diff", "HEAD", "--")
    except (ValueError, RuntimeError):
        return {}
    stats: dict[str, tuple[int, int]] = {}
    if result.returncode != 0:
        return stats
    for line in result.stdout.decode("utf-8", "replace").splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            stats[parts[2]] = (int(parts[0]), int(parts[1]))
    return stats


# ladder / modelPlus: real hardware numbers, or None when the platform won't say.

def _run_probe(argv: list[str]) -> Optional[str]:
    """stdout of a hardware probe, or None when it is missing or fails."""
    try:
        output = subprocess.run(argv, capture_output=True, text=True, timeout=10,
                                creationflags=_NO_WINDOW)
    except Exception:
        return None
    return output.stdout if output.returncode == 0 and output.stdout.strip() else None


def _nvidia_memory() -> Optional[tuple[Optional[int], int, Optional[str]]]:
    out = _run_probe(["nvidia-smi", "--query-gpu=memory.used,memory.total,name",
                      "--format=csv,noheader,nounits"])
    if out is None:
        return None
    try:
        used, total, name = [part.strip() for part in out.strip().splitlines()[0].split(",", 2)]
        return int(used), int(total), name or None
    except ValueError:
        return None


_DRM_ROOT = Path("/sys/class/drm")


def _amd_memory() -> Optional[tuple[Optional[int], int, Optional[str]]]:
    """amdgpu's own sysfs counters (Linux): no rocm-smi needed. The card with the
    most VRAM wins; an APU's small carve-out (under 1 GB) does not count."""
    best: Optional[tuple[Optional[int], int, Optional[str]]] = None
    try:
        cards = sorted(_DRM_ROOT.glob("card[0-9]*/device"))
    except OSError:
        return None
    for device in cards:
        try:
            if (device / "vendor").read_text().strip().lower() != "0x1002":
                continue
            total = int((device / "mem_info_vram_total").read_text()) // (1024 * 1024)
            used = int((device / "mem_info_vram_used").read_text()) // (1024 * 1024)
        except (OSError, ValueError):
            continue
        if total < 1024 or (best and best[1] >= total):
            continue
        try:
            name = (device / "product_name").read_text().strip() or None
        except OSError:
            name = None
        best = (used, total, name or "AMD GPU")
    return best


def _intel_memory() -> Optional[tuple[Optional[int], int, Optional[str]]]:
    """Intel discrete GPUs through xpu-smi (Arc, Data Center GPU)."""
    out = _run_probe(["xpu-smi", "discovery", "-d", "0", "-j"])
    if out is None:
        return None
    try:
        found = json.loads(out)
        total = int(found["memory_physical_size_byte"]) // (1024 * 1024)
    except (ValueError, KeyError, TypeError):
        return None
    if total <= 0:
        return None
    used: Optional[int] = None
    stats = _run_probe(["xpu-smi", "stats", "-d", "0", "-j"])
    try:
        for metric in json.loads(stats or "{}").get("device_level") or []:
            if metric.get("metrics_type") == "XPUM_STATS_MEMORY_USED":
                used = int(float(metric["value"]))
    except (ValueError, KeyError, TypeError, AttributeError):
        used = None
    return used, total, _as_text_core(found.get("device_name")) or "Intel GPU"


def _apple_memory() -> Optional[tuple[Optional[int], int, Optional[str]]]:
    """Apple Silicon shares RAM with the GPU. Metal lets the GPU wire about 2/3
    of it (3/4 above 36 GB) unless iogpu.wired_limit_mb raises the cap, so that is
    the total. "Used" is left None: the caller takes Ollama's size_vram instead,
    since system-wide memory use is not GPU use."""
    if os.uname().sysname != "Darwin" or os.uname().machine != "arm64":
        return None
    out = _run_probe(["sysctl", "-n", "hw.memsize"])
    try:
        ram = int((out or "").strip()) // (1024 * 1024)
    except ValueError:
        return None
    if ram <= 0:
        return None
    try:
        wired = int((_run_probe(["sysctl", "-n", "iogpu.wired_limit_mb"]) or "0").strip())
    except ValueError:
        wired = 0
    total = wired if 0 < wired <= ram else (ram * 3 // 4 if ram > 36 * 1024 else ram * 2 // 3)
    return None, total, "Apple GPU (unified memory)"


def _dxgi_adapters() -> list[dict[str, Any]]:
    """Every display adapter Windows knows, through DXGI (no vendor tool).

    Each: name, vendor id, dedicated and shared memory (MB), software flag, and
    the current local-memory use and budget (MB) where IDXGIAdapter3 exists
    (Windows 10+). Empty off Windows or when DXGI cannot be loaded."""
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    def guid(text: str) -> GUID:
        import uuid as _uuid

        raw = _uuid.UUID(text).bytes_le
        return GUID.from_buffer_copy(raw)

    class DESC1(ctypes.Structure):
        _fields_ = [("Description", ctypes.c_wchar * 128), ("VendorId", ctypes.c_uint),
                    ("DeviceId", ctypes.c_uint), ("SubSysId", ctypes.c_uint), ("Revision", ctypes.c_uint),
                    ("DedicatedVideoMemory", ctypes.c_size_t), ("DedicatedSystemMemory", ctypes.c_size_t),
                    ("SharedSystemMemory", ctypes.c_size_t), ("LuidLow", wintypes.DWORD),
                    ("LuidHigh", wintypes.LONG), ("Flags", ctypes.c_uint)]

    class MEMINFO(ctypes.Structure):
        _fields_ = [("Budget", ctypes.c_uint64), ("CurrentUsage", ctypes.c_uint64),
                    ("AvailableForReservation", ctypes.c_uint64), ("CurrentReservation", ctypes.c_uint64)]

    HRESULT = ctypes.c_long

    def method(obj: ctypes.c_void_p, index: int, *argtypes: Any) -> Callable[..., int]:
        vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, *argtypes)(vtable[index])

    def release(obj: ctypes.c_void_p) -> None:
        if obj:
            vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])(obj)

    try:
        dxgi = ctypes.WinDLL("dxgi")
    except OSError:
        return []
    factory = ctypes.c_void_p()
    create = dxgi.CreateDXGIFactory1
    create.restype = HRESULT
    create.argtypes = [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    if create(ctypes.byref(guid("770aae78-f26f-4dba-a829-253c83d1b387")), ctypes.byref(factory)) != 0:
        return []
    found: list[dict[str, Any]] = []
    mb = 1024 * 1024
    try:
        enum_adapters1 = method(factory, 12, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))
        for index in range(16):
            adapter = ctypes.c_void_p()
            if enum_adapters1(factory, index, ctypes.byref(adapter)) != 0:
                break                            # DXGI_ERROR_NOT_FOUND: no more adapters
            try:
                desc = DESC1()
                if method(adapter, 10, ctypes.POINTER(DESC1))(adapter, ctypes.byref(desc)) != 0:
                    continue
                row: dict[str, Any] = {
                    "name": desc.Description.strip() or None,
                    "vendor": desc.VendorId,
                    "dedicated_mb": desc.DedicatedVideoMemory // mb,
                    "shared_mb": desc.SharedSystemMemory // mb,
                    "software": bool(desc.Flags & 2),     # DXGI_ADAPTER_FLAG_SOFTWARE
                    "used_mb": None,
                    "budget_mb": None,
                }
                adapter3 = ctypes.c_void_p()
                query = method(adapter, 0, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))
                if query(adapter, ctypes.byref(guid("645967a4-1392-4310-a798-8053ce3e93fd")),
                         ctypes.byref(adapter3)) == 0:
                    try:
                        info = MEMINFO()
                        memory = method(adapter3, 14, ctypes.c_uint, ctypes.c_int, ctypes.POINTER(MEMINFO))
                        if memory(adapter3, 0, 0, ctypes.byref(info)) == 0:   # node 0, local segment
                            row["used_mb"] = int(info.CurrentUsage // mb)
                            row["budget_mb"] = int(info.Budget // mb)
                    finally:
                        release(adapter3)
                found.append(row)
            finally:
                release(adapter)
    finally:
        release(factory)
    return found


def _pick_dxgi_adapter(adapters: Iterable[Mapping[str, Any]]) -> Optional[tuple[Optional[int], int, Optional[str]]]:
    """The adapter models would run on, from _dxgi_adapters(): the hardware one
    with the most dedicated memory. A discrete card (1 GB or more of its own)
    reports its real use; an integrated GPU, which borrows system RAM, reports
    the shared size with used=None, as Apple unified memory does."""
    # Microsoft's Basic Render Driver (vendor 0x1414) is the CPU rasterizer of a
    # VM or a machine without a GPU driver; on a Windows CI runner it does NOT
    # carry the software flag, and would otherwise pass for 8 GB of shared VRAM.
    hardware = [a for a in adapters
                if not a.get("software") and int(a.get("vendor") or 0) != _DXGI_MICROSOFT
                and (a.get("dedicated_mb") or a.get("shared_mb"))]
    if not hardware:
        return None
    best = max(hardware, key=lambda a: (int(a.get("dedicated_mb") or 0), int(a.get("shared_mb") or 0)))
    dedicated = int(best.get("dedicated_mb") or 0)
    vendor = _DXGI_VENDORS.get(int(best.get("vendor") or 0), "GPU")
    name = _as_text_core(best.get("name")) or vendor
    if dedicated >= 1024:
        used = best.get("used_mb")
        return (int(used) if used is not None else None), dedicated, name
    shared = int(best.get("shared_mb") or 0) + dedicated
    if shared < 1024:
        return None
    return None, shared, f"{name} (shared memory)"


_DXGI_VENDORS = {0x10DE: "NVIDIA GPU", 0x1002: "AMD GPU", 0x1022: "AMD GPU", 0x8086: "Intel GPU"}
_DXGI_MICROSOFT = 0x1414


def _windows_memory() -> Optional[tuple[Optional[int], int, Optional[str]]]:
    """Any vendor on Windows through DXGI; nvidia-smi still goes first."""
    return _pick_dxgi_adapter(_dxgi_adapters())


def _gpu_memory() -> Optional[tuple[Optional[int], int, Optional[str]]]:
    """(used MB or None, total MB, name) of the GPU models would run on."""
    probes = [_nvidia_memory]
    if os.name != "nt":
        probes += [_amd_memory, _intel_memory, _apple_memory]
    else:
        probes += [_intel_memory, _windows_memory]
    for probe in probes:
        try:
            found = probe()
        except Exception:
            found = None
        if found is not None:
            return found
    return None


def gpu_memory_mb() -> tuple[Optional[int], Optional[int]]:
    """(used, total) MB of the GPU, or (None, None) without one we can read.

    NVIDIA (nvidia-smi), AMD (amdgpu sysfs, Linux), Intel (xpu-smi), any
    vendor on Windows through DXGI, and Apple unified memory. Apple and
    integrated GPUs on Windows report used=None: there the caller shows the
    size Ollama itself has in GPU memory.
    """
    found = _gpu_memory()
    return (found[0], found[1]) if found else (None, None)


def gpu_name() -> Optional[str]:
    found = _gpu_memory()
    return found[2] if found else None


def model_fit(size_bytes: Optional[int], vram_mb: Optional[int], ram_mb: Optional[int]) -> str:
    """'Fits' / 'Tight' / 'Too big' / '' for an installed model's real file size.

    Compares the on-disk size (what Ollama loads) with headroom for the KV
    cache against VRAM, else RAM. '' means there is nothing honest to say.
    """
    if not size_bytes:
        return ""
    need_mb = size_bytes / (1024 * 1024) * 1.2
    budget = vram_mb or (int(ram_mb * 0.85) if ram_mb else 0)
    if not budget:
        return ""
    if need_mb <= budget:
        return "Fits"
    if need_mb <= budget * 1.35:
        return "Tight"
    return "Too big"


def format_gb(mb: Optional[float]) -> str:
    if mb is None:
        return "?"
    return f"{mb / 1024:.1f}"


@dataclass(frozen=True)
class ServerStart:
    label: str                 # "the ollama system service", shown to the person
    argv: tuple[str, ...]
    waits: bool                # True: the command returns when done; False: it is the server
    manual: str = ""           # what to run by hand when this way fails


def model_server_start_plan(
    platform: str = "",
    which: Callable[[str], Optional[str]] = shutil.which,
    exists: Callable[[str], bool] = os.path.exists,
    unit_exists: Optional[Callable[[tuple[str, ...]], bool]] = None,
) -> list[ServerStart]:
    """How to start the local Ollama this machine already has, best first.

    The installers set Ollama up as a service (Linux: the system ollama.service
    with its own model store) or as the desktop app (macOS, Windows). Starting
    `ollama serve` as the user instead would come up with an empty model list,
    so when a service exists that is the only way offered.
    """
    platform = platform or sys.platform
    if unit_exists is None:
        def unit_exists(scope: tuple[str, ...]) -> bool:
            try:
                return subprocess.run(["systemctl", *scope, "cat", "ollama.service"],
                                      capture_output=True, timeout=10).returncode == 0
            except Exception:
                return False
    plan: list[ServerStart] = []
    if platform.startswith("linux") and which("systemctl"):
        if unit_exists(("--user",)):
            return [ServerStart("your ollama user service",
                                ("systemctl", "--user", "start", "ollama.service"), True,
                                "systemctl --user start ollama")]
        if unit_exists(()):
            # Root-owned: polkit asks for a password where a desktop agent runs.
            return [ServerStart("the ollama system service",
                                ("systemctl", "start", "ollama.service"), True,
                                "sudo systemctl start ollama")]
    elif platform == "darwin":
        for app in ("/Applications/Ollama.app", os.path.expanduser("~/Applications/Ollama.app")):
            if exists(app):
                plan.append(ServerStart("the Ollama app", ("open", "-g", "-a", app), True))
                break
    elif platform == "win32":
        tray = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama app.exe")
        if os.environ.get("LOCALAPPDATA") and exists(tray):
            plan.append(ServerStart("the Ollama app", (tray,), False))
    program = which("ollama")
    if program:
        plan.append(ServerStart("ollama serve", (program, "serve"), False, "ollama serve"))
    return plan


def node_version() -> Optional[str]:
    """`node --version` without a console window, or None when Node is missing."""
    executable = shutil.which("node")
    if not executable:
        return None
    try:
        output = subprocess.run([executable, "--version"], capture_output=True, text=True,
                                timeout=10, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    version = output.stdout.strip()
    return version or None


def node_supports_agent(version: Optional[str]) -> bool:
    """Qwen Code needs Node 22.19+ (or 24+)."""
    match = re.match(r"v?(\d+)\.(\d+)", _as_text_core(version))
    if not match:
        return False
    major, minor = int(match.group(1)), int(match.group(2))
    return major >= 24 or (major == 22 and minor >= 19) or major == 23


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
