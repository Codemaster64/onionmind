# Architecture

A contributor's map of the Onionmind source. It answers "where does this live
and why is it shaped this way", not "how do I use it" (that's [README.md](README.md))
or "is the privacy claim true" (that's [TECHNICAL.md](TECHNICAL.md)). Read those
first if you're a user; read this if you're about to change code.

## The one rule, up front

`onionmind.py`, `onionmind.ico`, and the logo payloads are **single-sourced**.
The installers (`install-onionmind.sh`, `onionmind-setup.cmd`,
`install-onionmind-android.sh`, `onionmind-setup.cmd`, …) each carry an embedded
copy. **Never hand-edit those copies.** Edit the source, then:

```bash
python build.py           # regenerates the embedded payloads in every installer
python build.py --check   # CI-style check: are the copies in sync? (no writes)
```

`build.py --check` is the gate — a PR that edits `onionmind.py` without rerunning
`build.py` fails it. See [CONTRIBUTING.md](CONTRIBUTING.md) for the full check list.

## The whole thing at a glance

```
Onionmind
├── Core inference + CLI + agent      onionmind.py            (no Qt, no Android)
├── Desktop workbench (PySide6)
│   ├── logic, no Qt import           onionmind_desktop_core.py   (unit-tested)
│   └── Qt widget tree + workers      onionmind_desktop.py
├── Android app (Kotlin)             android/
│   ├── platform-free core            core/  (Agent, Socks5, ModelSource, …)
│   └── app shell (WebView + services) app/   (Server, ProcessManager, …)
├── Matchstick live USB              usb/     (Dockerfile, build.sh, tests/)
├── Installers (generated payloads)  install-*.{sh,ps1}, onionmind-setup.cmd
├── Build tooling                    build.py, tools/build-desktop.ps1
└── Tests                            tests/ (Python), android/**/test (Kotlin)
```

Three platforms share **one inference core** (`onionmind.py`). The desktop and
Android UIs are separate front ends over it; the USB image is a packaging of the
Linux install. Nothing about the model tiers, storage, or Tor policy is
duplicated per-platform — it lives in the core and each front end calls in.

## The core: `onionmind.py`

One file, no GUI dependency, four concerns. It is the CLI (`python onionmind.py
"…"`), the Android-side command, the Matchstick brain, and the thing every other
launcher shells into. Rough section map (line numbers drift — grep the names):

| Concern | Functions | What it owns |
|---|---|---|
| **Tor lifecycle** | `start_tor_hidden`, `stop_managed_tor`, `tor_proxy_port`, `tor_check`, `_tor_browser_roots`, `_start_darwin_tor` | Starts a hidden `tor.exe`/`tor` **it owns**, reuses a pre-existing SOCKS listener without touching it, verifies a live circuit, and **fails closed** if none. Windows: Tor Browser's background `tor.exe`. macOS: Homebrew's `tor` under a generated torrc. Linux: the distro service. Never launches Tor Browser's `firefox.exe`. |
| **Inference** | `resolve_model`, `detect_backend`, `installed_models`, `pull_model`, `_ask_ollama[_stream]`, `_ask_llama`, `turn`, `turn_stream` | Ollama **and** llama.cpp adapters behind one `turn`/`turn_stream` seam. Streaming, image messages, tool calls. Model choice resolves to a **raw** backend name; tier labels (SPARK…PYRE) never reach a backend. |
| **Privacy filtering** | `strip_thinking`, `_think_tag_candidate`, `_partial_think_tag`, `_mark_incomplete`, `_recover_answer` | Strips private reasoning markup before output. The desktop buffers output behind the *Thinking* state until this has run. |
| **Web search over Tor** | `web_search`, `parse_results`, `tor_check` | Fresh circuit → DNS through Tor → DuckDuckGo onion. Refuses if the circuit doesn't verify. Search is **off unless enabled for that turn**. |
| **Coding agent + containment** | `run_agent`, `agent_argv`, `agent_env`, `run_code`, `start_tor_bridge`, `_TorBridge`, `_write_shims`, `_contain_env` | The one place the agent starts, so Tor is verified in exactly one place. See below — this is the subtle part. |

### The agent egress boundary (read this before touching agent code)

The design invariant: **the agent's only way off the machine is Tor, or there is
no way off.** It is enforced in layers, all funnelled through `agent_env()`:

1. **Fail closed.** `agent_env()` calls `start_tor_hidden()` then `tor_check()`.
   `tor_check()` exits the process if no circuit verifies, so "agent running while
   Tor is down" is unreachable. Every launcher (the `onionmind-code` script, the
   desktop *Agent* mode, `run_agent`) routes through here — there is deliberately
   no second entry point.
2. **Proxy everything.** `_proxy_env()` points every proxy variable
   (`HTTPS_PROXY`, …) at a loopback bridge (`start_tor_bridge`, served by
   `_TorBridge`) that exits through Tor. Any http library the agent's children use
   lands on Tor automatically.
3. **Deny direct sockets.** `_contain_env()` injects two shims (`_write_shims`):
   - `PY_SHIM` (via `PYTHONPATH`/`sitecustomize.py`) overrides `socket.connect`,
     `connect_ex`, and `getaddrinfo` — non-loopback connects and name lookups
     raise. Names never leak to the ISP resolver; Tor resolves at the exit.
   - `JS_SHIM` (via `NODE_OPTIONS --require`) does the same for Node's `net` and
     `dns`. Note the Windows path quirk handled in `_contain_env`: `NODE_OPTIONS`
     eats backslashes, so the shim path is forward-slashed.
4. **Known gap, documented not hidden.** Tools that ignore proxies entirely
   (`ping`, `nslookup`, `traceroute`) and any compiled binary or `python -S` still
   go straight out. The `ponytail:` comments in `agent_argv`/`_contain_env` say so
   and name the only real fix: an **OS egress rule** (firewall-by-user, container,
   netns) — which is what Matchstick's nftables ruleset actually is.

Approvals sit *inside* that boundary, not beside it. `qwen_setup()` writes
`tools.approvalMode` — `default` (ask, and stop where there is nobody to ask) or
`yolo` (edit and run unattended). YOLO widens what the agent may do to this
machine and nothing else: `permissions.deny` stays a hard denial in either mode,
and the proxy variables and socket shims are environment, which an approval
setting cannot reach. Keep it that way — a YOLO that also relaxed the deny list
would be exactly the silent boundary change the funnel exists to prevent.

## The desktop workbench

Three deliberately narrow seams so the logic is testable without a GUI:

- **`onionmind_desktop_core.py`** — *no Qt import.* Atomic JSON settings and
  sessions (`SettingsStore`, `SessionStore`, `ChatSession`, `_atomic_write_json`,
  `_quarantine_corrupt`), tier/display names (`describe_model`, `_tier_for_model`,
  `ModelDisplay`), bounded workspace + Git snapshots (`WorkspaceInspector`,
  `WorkspaceSnapshot`, `WorkspaceChange`), terminal command parsing
  (`parse_terminal_command`, `_split_windows_commandline`), the exact Harness
  command (`HarnessSpec`, `HarnessCommand`, `HarnessAvailability`), and the
  self-updater (`BundleUpdater`, `UpdateManifest`, `parse_update_manifest`,
  `update_state`). **All of this is covered by `tests/test_desktop_core.py`** — if
  you add logic here, add a test; don't reach for Qt.

- **`onionmind_desktop.py`** — the Qt widget tree and async workers. Model
  generation, terminal commands, Git refreshes, model pulls, and Harness tasks run
  **off the UI thread** (`SafeWorker`, `WorkerSignals`); cancel actions terminate
  the matching operation. `*Bridge` classes (`SessionBridge`, `WorkspaceBridge`,
  `HarnessBridge`, `UpdateBridge`, `SettingsBridge`) connect core logic to widgets.
  `OnionmindWindow` is the three-pane shell and the deliberate composition
  root — sessions, workspace, terminal, models, settings, Harness, and updates
  all plug into it through their `*Bridge` classes, so its fan-in is the
  design, not drift; `ThinkingStreamFilter` / `ThinkingIndicator` implement
  the buffer-until-sanitized *Thinking* state.

- **`onionmind.py`** — inference stays here. The desktop calls `turn_stream` etc.;
  it does not reimplement inference or Tor.

Ollama is the **model seam** (discovery + pulls via its local API, inference via
the core). Qwen Code is the **agent seam** (`qwen --model <raw> -p "<task>"`).
Raw backend names cross these seams; tier labels never do.

### Self-update over Tor

`BundleUpdater` in the core; `UpdateBridge` in the UI. Nothing is contacted until
the user presses **Check for updates** (or opts into *Check automatically over
Tor*). The manifest + ~50 MB bundle both travel a **fresh verified Tor circuit**,
are checked by size and SHA-256, staged (zip-slip guarded), and swapped via a
generated PowerShell helper that keeps dated backups and rolls back a failed move.
The feed is a plain release-asset URL, **not** `api.github.com` (per-exit rate
limits are hostile to shared Tor exits). Full detail:
[TECHNICAL.md → Self-update over Tor](TECHNICAL.md#self-update-over-tor).

## Android (`android/`)

Two Gradle modules, arm64 only:

- **`core/`** — platform-free Kotlin, the JVM-unit-testable layer. `Agent.kt`
  (agent config/egress policy), `Socks5Socket.kt` (SOCKS5 client),
  `ModelSource.kt` (the download catalog + TSV parsing/injection defence),
  `OwnedLoopbackProcess.kt`. That last one solves an Android-specific problem: a
  listening port is **not** ownership evidence because every app shares loopback,
  so a child is "ready" only while the *exact* process this instance launched is
  still alive — transitions are locked so a request can't adopt a stranger's
  listener. Tests: `core/src/test/kotlin/**` (`AgentPrivacyTest`,
  `OwnedLoopbackProcessTest`, `Socks5Test`, `ParseTest`, `ModelSourceTest`, …).

- **`app/`** — the Android shell. `App.kt` starts only the app-local HTTP bridge;
  native processes and network stay off until a user action needs them.
  `Server.kt` is the whole backend — chat page + tiny JSON API on
  `127.0.0.1:8081`; the WebView (`MainActivity.kt`) talks only to it.
  `ProcessManager.kt` owns the model catalog and the llama/tor child processes.
  `DownloadService.kt` is a foreground service + wake lock so multi-GB model
  downloads survive Doze/app-cache freezing.
- **Build helpers** — `android/icons.sh` (launcher icons rendered from the
  single-sourced `onionmind.ico`), `android/llama.sh` (NDK cross-compile of
  `llama-server` into `jniLibs`), and `android/itest.sh` (the `:core` suites,
  live-tor tests included) are the stages `android/Dockerfile` runs before
  `assembleDebug` — the Dockerfile is what ties them to the rest of the build.

Android source version and packaging notes live in
[TECHNICAL.md → Android](TECHNICAL.md#android). APKs are built and attached
manually; there is no CI build.

## Matchstick live USB (`usb/`)

A packaging of the Linux install into an amnesic, firewalled boot image, not new
inference code. `Dockerfile` + `build.sh` bake the model weights into a read-only
store (zero boot-time network); an **nftables ruleset loads before networking**
so only Tor/DHCP/loopback can leave — this is the OS-level egress rule the agent
shims can only approximate. `usb/tests/` holds the container validation suites
(Tor bootstrap, model baking, firewall ordering, read-only serving). DIY route
and the edition/GPU matrix: [usb/README.md](usb/README.md).

## Build & release

- **`build.py`** — single-sources the installer payloads (above). `--check` for CI.
- **`tools/build-desktop.ps1`** — produces the standalone Windows bundle. Audits
  its isolated venv first, skips pip when constrained versions are already present,
  and refuses to touch the network unless rerun with `-AllowDirectNetwork` (+ `-Yes`
  to skip the prompt). `-Check` validates without building.
- **CI** (`.github/workflows/desktop-build.yml`) runs the payload check, the core
  tests, and the desktop build on every push, verifies the bundle, and — on every
  push to `main` — republishes the rolling `desktop-latest` release whose manifest
  is the in-app updater's feed. `usb-tests.yml` and `ollama-tor.yml` run their own
  suites. **Tagged releases, the Android APK, and the Matchstick image are built
  and attached manually.**

## Tests

No framework — the Python tests are plain `assert`-based scripts you run directly
(`python tests/test_*.py`). Map:

| File | Guards |
|---|---|
| `test_parser.py` | Result/thinking parsing in the core |
| `test_backends.py` | Ollama/llama.cpp adapter logic (no network) |
| `test_coding_agent.py` | `agent_argv`/`agent_env`/containment shape |
| `test_privacy_contracts.py` | The fail-closed / Tor-only invariants |
| `test_desktop_core.py` | Everything in `onionmind_desktop_core.py` |
| `test_desktop_ui.py`, `test_desktop_thinking_ui.py`, `test_desktop_tor_ui.py` | Qt front end |
| `test_installer_contracts.py` | Installer payloads match source (pairs with `build.py --check`) |
| `test_release_version_contracts.py` | Version markers consistent across release artifacts |

**Honesty culture:** every claim in the docs states how it was verified. Add a
feature → add the check that proves it, or mark it unverified
([CONTRIBUTING.md](CONTRIBUTING.md)).

## Where to make a change

| You want to… | Edit | Then run |
|---|---|---|
| Change inference / Tor / search / agent egress | `onionmind.py` | `python build.py`, `tests/test_backends.py`, `test_coding_agent.py`, `test_privacy_contracts.py` |
| Change desktop logic (settings, sessions, update, git snapshot) | `onionmind_desktop_core.py` | `tests/test_desktop_core.py` |
| Change desktop UI | `onionmind_desktop.py` | `tools/build-desktop.ps1 -Check`, the `test_desktop_*_ui.py` set |
| Change Android behaviour | `android/core` (logic) / `android/app` (shell) | the Kotlin `test/` suites |
| Change the USB image | `usb/` | `usb/tests/` |
| Change an installer | **source** (`onionmind.py`/icon/logo) — never the embedded copy | `python build.py && python build.py --check` |
