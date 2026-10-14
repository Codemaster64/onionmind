"""Real hardware numbers (RAM, VRAM per GPU vendor), model fit, and how to
start a model server or check Node for the agent.

Part of onionmind_desktop_core, which re-exports every name defined here.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Callable, Iterable, Mapping, Optional

from onionmind_desktop_vault import (
    _NO_WINDOW,
    _as_text_core,
)


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
