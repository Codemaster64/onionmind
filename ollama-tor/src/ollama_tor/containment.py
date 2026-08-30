"""Child environment that points proxy-aware tools at the Tor bridge."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

_PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")


def shim_directory() -> Path:
    path = Path(__file__).with_name("shims")
    if (
        not (path / "sitecustomize.py").is_file()
        or not (path / "no_direct_net.cjs").is_file()
    ):
        raise RuntimeError("ollama-tor was installed without its containment shims")
    return path


def routed_environment(
    proxy_url: str,
    socks_port: int,
    *,
    log_path: Path | None,
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build the environment inherited by an Ollama integration and its children."""

    environment = dict(os.environ if base is None else base)
    for key in list(environment):
        if key.upper() in _PROXY_KEYS:
            del environment[key]

    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        environment[key] = proxy_url
        if os.name != "nt":
            environment[key.lower()] = proxy_url
    environment["NO_PROXY"] = ""
    if os.name != "nt":
        environment["no_proxy"] = ""

    # Node's built-in fetch only opts into the standard proxy variables when
    # this flag is enabled. npm has its own variables and can otherwise ignore
    # a clean HTTP_PROXY inherited from the launcher.
    environment["NODE_USE_ENV_PROXY"] = "1"
    environment["npm_config_proxy"] = proxy_url
    environment["npm_config_https_proxy"] = proxy_url
    environment["npm_config_noproxy"] = ""

    shims = shim_directory()
    python_path = environment.get("PYTHONPATH", "")
    entries = [str(shims)] + ([python_path] if python_path else [])
    environment["PYTHONPATH"] = os.pathsep.join(entries)

    node_shim = (shims / "no_direct_net.cjs").as_posix()
    preload = f'--require="{node_shim}"'
    node_options = environment.get("NODE_OPTIONS", "").strip()
    if node_shim not in node_options:
        environment["NODE_OPTIONS"] = f"{preload} {node_options}".strip()

    environment["OLLAMA_TOR_ACTIVE"] = "1"
    environment["OLLAMA_TOR_PROXY"] = proxy_url
    environment["OLLAMA_TOR_SOCKS_PORT"] = str(socks_port)
    environment["OLLAMA_TOR_LOG"] = str(log_path) if log_path is not None else ""
    return environment
