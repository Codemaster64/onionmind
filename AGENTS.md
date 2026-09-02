# AGENTS.md

Instructions for an AI agent working in this repo. Read
[ARCHITECTURE.md](ARCHITECTURE.md) for the code map; this file is the shortlist
of things you must not get wrong here, and the checks that prove you didn't.

## Orient first

- **What this is:** a local-first coding workbench (Ollama/llama.cpp models) with
  fail-closed Tor for search and agent egress. Three front ends — desktop
  (PySide6), Android (Kotlin), Matchstick USB — over **one** inference core,
  `onionmind.py`.
- **Read before editing:** [ARCHITECTURE.md](ARCHITECTURE.md) (where things live),
  [CONTRIBUTING.md](CONTRIBUTING.md) (the check list), [TECHNICAL.md](TECHNICAL.md)
  (the privacy claims you must not weaken).
- **Environment:** Windows + PowerShell 7 is the primary shell. CI
  (`.github/workflows/`) runs the payload check, core tests, and the desktop
  build on every push, and republishes the rolling `desktop-latest` release
  from `main` (the in-app updater's feed). Tagged releases, the Android APK,
  and the Matchstick image are built and attached manually.

## Hard rules — do not break these

1. **Single-source the payloads.** `onionmind.py`, `onionmind.ico`, and the logo
   are embedded (copied) into every installer (`install-onionmind.*`,
   `onionmind-setup.cmd`, …). **Never hand-edit an installer's embedded copy.**
   Edit the source, then run `python build.py`, and commit both. `python build.py
   --check` is the gate; if you skip it, `tests/test_installer_contracts.py` fails.

2. **Never weaken the Tor egress boundary.** The invariant: *the agent's and
   search's only way off the machine is a verified Tor circuit, or there is none.*
   Concretely, in `onionmind.py`:
   - Search and agent both call `tor_check()` and **fail closed** (exit) when no
     circuit verifies. Do not add a direct-network fallback.
   - The agent starts in exactly one place (`agent_env()` → `run_agent`/desktop
     *Agent* mode). Do not add a second entry point that skips the funnel.
   - YOLO (`--yolo`, or the Agent-mode checkbox) only sets `tools.approvalMode`.
     It must never touch `permissions.deny`, the proxy variables or the socket
     shims `_contain_env()` injects. Don't route around them.
   - Onionmind launches a hidden `tor.exe`/`tor` **it owns** and never Tor
     Browser's `firefox.exe`; it reuses (never kills) a pre-existing SOCKS
     listener. Keep both behaviours.
   If a change here is genuinely intended, say so explicitly and update
   `tests/test_privacy_contracts.py` + [TECHNICAL.md](TECHNICAL.md) in the same PR.

3. **Honesty culture — every claim states how it was verified.** If you add a
   feature, add the check that proves it, or mark it unverified in the docs. Do
   not add performance numbers you didn't measure. Do not describe behaviour you
   didn't run.

4. **Tier labels are presentation only.** SPARK/EMBER/BLAZE/INFERNO/… never reach
   a backend or external tool — the **raw** Ollama/llama.cpp model name is what
   crosses those seams. Don't pass a label to a backend.

5. **Respect the seams.** Logic goes in `onionmind_desktop_core.py` (no Qt import,
   unit-tested); Qt-only code in `onionmind_desktop.py`; inference stays in
   `onionmind.py`. Don't import Qt into the core, and don't reimplement inference
   in a front end. On Android, platform-free logic goes in `android/core`, the
   shell in `android/app`.

## Checks to run

Match the check to what you touched (all are plain, framework-free):

```bash
python build.py --check                 # installer payloads in sync with source
python -m py_compile onionmind.py build.py
python tests/test_backends.py           # adapter logic, no network
python tests/test_coding_agent.py       # agent argv/env/containment shape
python tests/test_privacy_contracts.py  # fail-closed / Tor-only invariants
python tests/test_desktop_core.py       # onionmind_desktop_core.py logic
```

Desktop UI: `.\tools\build-desktop.ps1 -Check -PythonExecutable python`, plus the
`tests/test_desktop_*_ui.py` set. Android: the Kotlin `test/` suites under
`android/**`. Shell scripts: `bash -n install-onionmind.sh
install-onionmind-android.sh matchstick.sh usb/build.sh`. Full list:
[CONTRIBUTING.md](CONTRIBUTING.md).

**No test framework** — tests are `assert`-based scripts you run directly. Non-trivial
logic you add leaves one runnable check behind; don't add fixtures or per-function
suites unless asked.

## House style

- **Lazy but correct:** stdlib before a dependency, native before custom, the
  shortest diff that works. Don't add a dependency for what a few lines do. Don't
  add speculative abstractions.
- `ponytail:` comments mark deliberate shortcuts and name the ceiling/upgrade path
  (e.g. the `ping`/compiled-binary egress gap in `agent_env`). Keep that
  convention — a known gap is documented, not hidden.
- **Git:** the primary shell is pwsh 7, so `git commit -m` with quotes is safe and
  `>` writes clean UTF-8. Only legacy `powershell.exe` (5.1) needs the `-F <file>`
  detour. Branch before committing on `main`; push fast-forward, never `--force`.
- **Commits/PRs carry no AI attribution** — no `Co-Authored-By`, no
  "Generated with" trailer, no session links.

## Common ways an agent gets this repo wrong

- Editing an installer's embedded `onionmind.py` copy instead of the source (then
  `build.py --check` fails, or worse, the copies silently diverge).
- Adding a "if Tor is down, try direct" fallback "to be helpful" — that is the one
  thing the whole design exists to prevent.
- Writing a doc claim ("~19 tok/s", "works on X") without running it.
- Importing Qt into `onionmind_desktop_core.py` and breaking its headless tests.
- Assuming CI covers you — the `desktop-build` workflow runs the payload check
  and a subset of the tests, but the coding-agent, privacy-contract, release-
  version, and desktop UI suites only run when you run them.
