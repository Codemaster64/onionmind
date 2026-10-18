# Workbench v2: what is real, what is partial, what is missing

The v2 design prototype faked most of its numbers. This branch wires every
feature to real program state instead. Where real data does not exist yet,
the UI hides the element or says "unknown"; nothing prints an invented
number. This file lists each of those places and the work that would close
it.

Each row says how it was verified: **unit** = a test in `tests/`, **live** =
run in the real window against real code (qwen-code 0.25.0, `onionmind.py`,
this machine's GPU) with a fake model server standing in for Ollama, **not
run** = not exercised.

## Gaps to close

| Feature | What is missing | Backend work needed |
|---|---|---|
| modelPlus, ladder | **Windows VRAM through DXGI is untested on real GPUs.** NVIDIA (nvidia-smi), AMD on Linux (amdgpu sysfs), Intel discrete (xpu-smi), Apple unified memory and, on Windows, any vendor through DXGI (`IDXGIAdapter3::QueryVideoMemoryInfo`) are read. An integrated GPU on Windows shows its shared size with no "used" figure, as Apple does. The DXGI call runs in Windows CI (where the only adapter is the software one) and the choice of adapter is unit-tested against canned adapters; it has not run on a real AMD or Intel card. | A run on real Windows AMD and Intel hardware. |
| ladder | **No load percentage.** Ollama's preload call reports nothing while it loads, so the UI shows an indeterminate bar, not the prototype's percentage. | Ollama would need to stream load progress; until then the bar is honest. |
| ladder | **Fit labels are estimates.** "Fits / Tight / Too big" compares the real file size plus 20% for the KV cache with VRAM, or 85% of RAM. | Ask Ollama for the actual memory estimate per context size, if it ever exposes one. |
| modelPlus | **No tok/s in Agent mode.** qwen-code's stream reports no token timing, so the status bar says Idle during Agent runs. | Read timing from qwen's `result.usage`, if it starts filling it (it reported zeros in testing). |
| contextMeter | **Before an Agent run the figure is still partly an estimate.** During a run the meter shows qwen-code's own count (`get_context_usage`, asked after each tool result and at the end). Between runs it is the estimate (AGENTS.md, selected files, task) plus the system prompt and tool schemas qwen reported last run. The first run in a session has no such measurement. Unit-tested against canned control responses, not a live qwen. | Ask qwen for its overhead before the first run (needs a running process). |
| runPanel | **The plan only shows when the agent keeps one.** The plan comes from qwen's `todo_write` calls; otherwise the panel lists the tools it actually ran. | Nothing to fake; this matches what the agent really did. |
| errors | **Starting a model server we don't own is best-effort.** "Restart model server" now starts what the machine has: on Linux the `ollama` user or system service (never a second `ollama serve` with an empty model store; the system service may ask for a password through polkit), on macOS the Ollama app, on Windows the tray app, and `ollama serve` otherwise. If every way fails, the card shows the exact command to run. A llama-server is still not restarted. The start plan is unit-tested; the starts themselves were not run here (no Ollama on this machine). | Restarting llama-server. |
| errors | **Out of memory is matched by text.** It is recognised from Ollama's error body ("out of memory", CUDA). Other wordings show the generic failure. | Structured error codes from the engine. |
| errors | **Tor start on Linux needs the tor package.** When nothing listens, Onionmind now runs the distribution's `tor` as the user under its own torrc (as on macOS), so "Try a new circuit" can start one. Without the package installed it still fails closed with an install hint. Run live on Linux with tor 0.4.9.14 (Tor Project's expert bundle): a cold start took about 110 s to bootstrap, and `tor_check` used to give up before that on every first start. Onionmind now waits for tor's own "Bootstrapped 100%" notice (up to 180 s) before checking. | Bundle a tor binary for Linux, as Tor Browser provides one on Windows. |
| undo | **Only modified files can be split.** A modified file's "Parts…" button reverts only the ticked hunks (`git apply -R`, re-read and `--check`ed first). New, deleted, renamed and binary files still revert whole. Undo restores the whole file as it was before the revert. | Line-level picking inside a hunk. |
| rules | **Blocked paths through the shell.** Blocked paths are hard qwen deny rules for read and edit tools. A shell command touching one is refused only when it names the path and goes through an approval (not under YOLO). | qwen shell-command path rules, or an OS sandbox. |
| lock | **Without a password the key store releases the key to any program running as you.** The data is encrypted, but DPAPI, the Keychain and the Linux keyring all hand the key to the same user account; on Linux without a keyring it is a 0600 file. | A password (offered in Settings); the OS cannot tell Onionmind apart from other programs of the same user. |
| lock | **Agent data in plaintext during a run.** qwen-code needs its session in plaintext while it runs, so it lives in a private scratch folder (RAM-backed under `XDG_RUNTIME_DIR` on Linux, the temp folder on Windows and macOS) until the run ends. `save_memory` notes still go to `~/.qwen/memory.md`. The redirect (`QWEN_RUNTIME_DIR`) was run live with qwen-code 0.25.1 against Ollama: qwen's chats and memory went to the scratch folder, were sealed into the vault when the run ended, and `~/.qwen` kept only its config and a token-count record. | An encrypted mount for the run itself. |
| appearance | **The script installers do not carry Inter.** It ships in `fonts/` and in the Windows bundle; an install from `install-onionmind.sh` or `onionmind-setup.cmd` falls back to a system Inter, then Verdana or DejaVu Sans. | Embed or fetch `fonts/` in the script installers. |
| design files | **`.impeccable/design.json` is still the old palette.** It is generated by the impeccable tool. | Regenerate it from the updated `DESIGN.md`. |
| boundary | **"Today" counts start when logging starts.** Counts come from the egress log, which is written only while netlog or boundary is on. | None; this is the privacy choice (no log unless asked). |

## Closed since

- **Encryption at rest.** Settings, sessions, the network log and revert copies are AES-256-GCM encrypted; the key lives in the OS key store, or behind an optional password asked for at start (desktop and TUI). See TECHNICAL.md "Encryption at rest". Tested in `tests/test_vault.py` and live against GNOME Keyring.
- **The lock drops the key.** On the desktop the idle lock removes the data key from memory once nothing is running, and unlocking derives it from the password again. On Android, five minutes in the background with a password set overwrites the key and returns the app to the unlock screen. Wrong passwords on Android back off up to 5 minutes. Tested in `tests/test_vault.py` and `VaultTest`; the Android lock also on an emulator (see the PR).
- **Encrypted export and import.** One password-protected `.onionmind` file with every session and the settings; import adds sessions without replacing any. The same format on the phone (`ExportFile.kt`), which reads the desktop's sample in `ExportFileTest`; the phone exports its conversation and imports the newest one from a file (Settings → Conversation; not yet run on a device). See TECHNICAL.md "Export and import".
- **Encryption at rest on Android.** Same file format; the key is wrapped by the Android Keystore or by an optional password (PBKDF2). Tested in `VaultTest` and on an Android 14 emulator, including the upgrade from the previous APK.

- **Resuming a stopped run.** A stream run that was denied, halted (approvals off) or stopped keeps qwen's session id from its `init` line. A "Continue this run?" card resumes that session (`onionmind.py --agent --resume <id>` → `qwen --resume <id>`, as of qwen-code 0.25.1), so its context survives. A halted run's card says that continuing allows edits for the rest of the run. Unit-tested; not yet run against a live qwen.

## Copy changed from the prototype, and why

- **Starter prompts.** The prototype's examples assumed its fake Ruby project ("Write tests for the user model", "Is there a newer minitest release?"). They became "Write tests for the code I selected" and "Is there a newer release of a library this project uses?".
- **The out-of-memory card.** The prototype said "about 11 GB"; the card now names the real tier and this GPU's real VRAM.
- **Deny and halt outcomes.** These say what was refused ("Nothing was written for it", "That action was not run") rather than "Nothing on disk changed", which earlier steps of the same run might contradict.

## Flagged, not changed

- **The Agent warning dialog** (resolved). It used to say the agent is "not confined to Tor" and "may contact arbitrary hosts directly", while the docs said Tor-only and fail-closed. Now everything says the same accurate thing: proxy-aware agent traffic goes through Tor, direct Python and Node sockets are blocked, and a command that ignores proxies can still connect directly. This affects the dialog, the run-panel meta line, the "Agent network" row, the privacy card and TECHNICAL.md.
- **Retry with YOLO.** The checklist's baseline lists a stop card with "Retry with YOLO". That button does not exist in the current app (only "Retry last turn" for failed Chat turns), so it was not added.
