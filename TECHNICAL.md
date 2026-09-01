# Onionmind — the full technical story

Everything behind the pitch on the front page: model selection, measured
performance, the exact privacy boundary, threat models, tuning, and
troubleshooting. Nothing here is marketing; numbers are measured on real
hardware, and every claim says how it was checked.

- Matchstick (the live USB) internals: **[usb/README.md](usb/README.md)**
- Back to the front page: **[README.md](README.md)**

## Quick start

**Windows** — run `onionmind-setup.cmd` or paste `install-onionmind.ps1` into PowerShell.
**Linux** — `bash install-onionmind.sh` (Arch or Ubuntu/Debian; run as your normal user, it calls `sudo` where needed).

The desktop entry point opens a native PySide6 workbench. For development:

```text
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-desktop.txt
.venv\Scripts\python onionmind_desktop.py
```

There is no embedded browser, WebView, or local HTTP frontend in the desktop
program. `tools/build-desktop.ps1` produces the standalone Windows artifact.

By default you get **Qwen3.8-27B** where it fits in VRAM, and the fast small model where it
doesn't. Override with `ONIONMIND_MODEL`:

| `ONIONMIND_MODEL` | Installs |
|---|---|
| `auto` *(default)* | 27B if VRAM ≥ 8GB, otherwise the fast small model |
| `fast` | always the small model — 116 tok/s (9B) or 166 tok/s (4B) |
| `27b` | always Qwen3.8-27B, even if that means 1–2 tok/s on CPU |

The models are named for the burn, in increasing weight:
`SPARK < EMBER < BLAZE < INFERNO < CINDER < WILDFIRE < FLASHPOINT < PHOENIX < NOVA < PYRE`.
The currently shipped sizes are `SPARK` (2.6B), `EMBER` (4B), `BLAZE` (9B),
and `INFERNO` (27B, with vision). The remaining names are reserved for future
larger models; `CINDER` is reserved for a low-and-slow embedding/retrieval
small embedding model optimized for high recall, search, RAG, and vector
databases. Lineage, exact quants and the size mapping are in the tables
below; switching installs never silently replaces a model you already have.
The target profile for **INFERNO** is **30–70B MoE**, optimized for high
throughput, fast API responses, real-time apps, and customer support agents.
Positioning: **Frontier** · tagline: **“Full flame.”**
The reserved **WILDFIRE** profile is **100B+ MoE**, multimodal, deep-reasoning,
and long-context, for research, complex agents, and enterprise AI. Tagline:
Multi-agent orchestration, heavy tool use, autonomous workflows, autonomous ops,
and deep automation.
**Agentic / Swarm** · tagline: **“Burn it down.”**
The reserved **FLASHPOINT** tier is **Experimental**, for limited preview
models and new capabilities before full release, for beta testers and labs,
with the tagline
**“Uncontainable.”**
The reserved **NOVA** tier is **Ultra / Premium**, targeting top benchmarks
with high-cost, specialized training for high-stakes enterprise and scientific
modeling. Tagline: **“One spark away.”**
The reserved **PHOENIX** tier is **Fine-tune / Recovery**, with the tagline
**“Cosmic burn.”** Distillation, safety tuning, red-teaming, model reset,
custom models, and safety alignment.
The reserved **PYRE** tier is **Legacy / Collector**, for archived or
special-edition models and older versions, for retro access and research
archives, with the tagline **“Rise.”**

The installer is re-runnable and resumes partial downloads, so a dropped connection just
means running it again.

## What it installs

| | |
|---|---|
| Ollama | via winget, plus a background server on `127.0.0.1:11434` |
| Tor Browser | via winget, then **started automatically** — it owns the SOCKS proxy on 9150 |
| Model weights | 10–16GB GGUF, picked to fit your GPU |
| Vision projector | 885 MiB `mmproj`, registered as a second model sharing the same base |
| `onionmind.py` | local chat/search engine and compatibility CLI, written next to the weights |
| `onionmind_desktop.py` | native PySide6 presentation and process orchestration |
| `onionmind_desktop_core.py` | persistence, model naming, workspace inspection, and Harness command construction |
| `onionmind` + desktop icon | native project/session workbench |
| `onionmind-chat` | compatibility alias for the same native workbench |
| `onionmind-code "task"` | one-shot DeepSeek Harness headless task through Ollama |
| `onionmind-update` | lightweight code/launcher updater; leaves model weights untouched |
| Isolated desktop runtime | `requests`, `PySocks`, and `PySide6-Essentials` in `desktop-env` |

Agent mode additionally requires a Node.js version accepted by the current DSH
package (`^22.19` or `24+`). Both the native preflight and generated CLI launcher
check this before claiming Harness is ready.

## Native desktop architecture

The desktop rewrite keeps three deliberately narrow seams:

- `onionmind.py` owns Ollama/llama.cpp inference, streaming, image messages,
  tool calls, and fail-closed Tor search. Existing CLI, Android, and Matchstick
  paths continue to use this core.
- `onionmind_desktop_core.py` has no Qt dependency. It owns atomic JSON settings
  and sessions, Onionmind tier/display names, bounded workspace and Git snapshots,
  terminal parsing, and the exact DeepSeek Harness command. This layer is covered
  by unit tests without starting a GUI.
- `onionmind_desktop.py` owns the Qt widget tree and asynchronous workers. Model
  generation, terminal commands, Git refreshes, model pulls, and Harness tasks
  stay off the UI thread; cancel actions terminate the corresponding operation.

The application window is a native, resizable three-pane workbench: projects and
sessions on the left; conversation, terminal, and composer in the centre; context,
changes, and activity on the right. Narrow windows collapse inspectors before the
conversation. Keyboard focus, tooltips, visible selection, and standard controls
remain available without a mouse.

Ollama remains the model seam: discovery and pulls use its public local API and
inference continues through `onionmind.py`. Harness remains the agent seam:

```text
ollama launch dsh --model <raw-ollama-name> -- --profile headless "<task>"
```

The raw Ollama model name is always passed to external tools. The UI labels a
model with what it actually is plus its weight class (`Qwen3.8 27B · heavy -
~12-16 GB VRAM`); SPARK, EMBER, BLAZE, and INFERNO are the internal size ladder
those labels are resolved from, not what is shown. Harness output
is streamed into the native transcript, and its working directory is the active
project rather than the user's home directory.

The stock DSH headless profile has no interactive approval answerer, so approval
requests fail closed. Custom DSH profiles can alter that composition. Stop
terminates the shell or Harness launcher managed by Onionmind; deliberately
detached/background child processes can outlive their launcher and may need
manual termination.

### Self-update over Tor

The standalone workbench updates itself without ever touching clearnet, and
never without permission:

- Every bundle carries a `.onionmind-source-revision` marker naming the exact
  commit it was compiled from. CI republishes a rolling `desktop-latest`
  GitHub release on each push to `main` with two assets: the bundle zip and a
  small `onionmind-update.json` manifest (revision, size, SHA-256).
- **Nothing is contacted until the user asks.** The updater opens no network
  connection on its own: Settings → **Check for updates** is the permission,
  and it fetches the manifest through the verified Tor SOCKS port with fresh
  credentials — a dedicated circuit, like a search. When Tor is down the
  button refuses rather than falling back. The status bar keeps an
  **Updates…** entry available at all times; it opens the update controls
  with one click.
- **Check automatically over Tor** is an explicit opt-in (off by default).
  Granted, Onionmind checks for as long as it stays open — not only at
  startup — at most every 12 hours, always over a verified circuit, and a
  newer published revision surfaces as a status-bar notice. The heavy
  download still requires its own button.
- **Download and install** streams the zip through Tor, verifies size and
  SHA-256, unpacks it into a staging directory (zip-slip guarded, revision
  marker re-checked), and only then offers **Restart and update**. A
  generated PowerShell helper waits for the app to exit, renames the old
  bundle to a dated `backup-before-<rev>` sibling (the installer's naming),
  moves the staged bundle into place, relaunches, and keeps the two newest
  backups. A failed move rolls the backup back into position first.

The feed is deliberately a plain release-asset URL, not `api.github.com`:
the API is heavily rate-limited per exit address, which is hostile to shared
Tor exits. `onionmind-update` (the CLI command) remains the updater for
source installs and leaves model weights untouched in both paths.

## Which build you get

The installer reads your VRAM and picks accordingly. All three are the same abliterated
Qwen3.8-27B; they differ only in quantization.

| VRAM | Build | Size | Vision |
|---|---|---|---|
| ≥ 17GB | Qwen3.8-27B `mtp-Q4_K_M` | 16.0 GB | yes |
| ≥ 12GB | Qwen3.8-27B `3.69bpw-12GB-MTP` | 11.7 GB | yes |
| ≥ 8GB | Qwen3.8-27B `IQ2_M` | 9.5 GB | yes |
| ≥ 6GB | Qwen3.5-9B abliterated | 5.2 GB | no |
| < 6GB | Qwen3.5-4B abliterated | 2.5 GB | no |

### Laptops and small GPUs

**There is no small Qwen3.8.** As of August 2026 the family ships exactly two models — the
27B and a 2.4T MoE — and no generation newer than 3.8 exists (no 3.7, 3.9 or 4). So below
~8GB VRAM there is nothing in-family to fall back to.

The smallest 27B build is 9.5GB. On a 4GB laptop that runs almost entirely on CPU at roughly
**1–2 tok/s** — technically Qwen3.8, practically unusable. The installer instead picks the
abliterated **Qwen3.5** at 9B or 4B: one generation behind, but the newest model that exists
in that size class, and it fits entirely in VRAM. The 4B measured **166 tok/s** and refuses
nothing.

Set `ONIONMIND_MODEL=27b` to override and get Qwen3.8 regardless of speed.

Vision is skipped on the small tiers — the `mmproj` is built for the 27B architecture.

All are **MTP** builds — they keep the multi-token-prediction head, which Ollama uses for
speculative decoding. Non-MTP (`noMTP`) builds work but give that up.

## Measured performance

On an RTX 3080 Ti (12GB), 32GB RAM, Windows 11:

| Model | Speed | On GPU |
|---|---|---|
| Qwen3.5-9B abliterated | 116.7 tok/s | 100% |
| Qwen3-14B abliterated | 75.8 tok/s | 100% |
| **Qwen3.8-27B @ 3.69bpw** | **~19 tok/s** | 100% |
| Qwen3.8-27B + vision | ~17–23 tok/s | 100% |
| Qwen3.8-27B @ Q4_K_M | 8.0 tok/s | 58% |

The 27B figure is the honest steady-state with a normal desktop running. It climbs
substantially — into the 40s — when the GPU is otherwise idle. See *Speed swings* below;
this is the single most confusing behaviour of the setup.

A model that fits **entirely** in VRAM is worth more than a bigger one that doesn't. The 9B
is 6× faster than the 27B at Q4_K_M for exactly that reason.

## Web search over Tor

The installer starts Tor Browser and waits for a circuit, so it is already up after a fresh
install. Just **leave it open** — it owns the SOCKS proxy on port 9150. A standalone `tor`
daemon on 9050 also works; the script tries both.

The model decides when to search. Ask it something it already knows and it answers directly;
ask for something current and it issues its own queries, reads the snippets, and cites them.
In interactive chat, `/save notes.txt` exports the conversation so far.

### What protects you

- **DuckDuckGo's `.onion` service** — the query never leaves the Tor network, so no exit node
  ever sees it. Also the only reliable option: the clearnet endpoint returns **403** to most
  Tor exits.
- **A fresh circuit per search** — random SOCKS credentials per request make Tor build a
  separate circuit. Without this every search shares one exit and they're trivially linkable.
- **`socks5h://`** — DNS resolves through Tor. Plain `socks5` leaks every hostname to your
  ISP's resolver while appearing to work.
- **Fails closed** — verified against `check.torproject.org`. If it isn't Tor, it exits rather
  than searching in the clear.
- **Tor Browser's User-Agent** — blends into that crowd instead of being a unique fingerprint.

### The privacy boundary, precisely

**In Chat mode, prompts and inference stay on your machine.** Model reasoning,
answers, project snapshots, and session files remain local. Search queries go to
DuckDuckGo through Tor. Model pulls and the source-install `onionmind-update`
command are explicit direct downloads; the native desktop updater fetches its
manifest and bundle through Tor.

**Agent mode's supported web path leaves over Tor or does not leave.** Every
agent launcher—the workbench, `onionmind-code`, and launchers installed by the
update scripts—goes through `onionmind.py --agent`, which is the one place a
circuit is verified. `tor_check()` runs before anything starts and exits when no
verified circuit exists, so "the agent ran while Tor was down" is not a reachable
state. It then hands DeepSeek Harness the same
containment `run_code()` gives Qwen Code: `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY`
and `NODE_USE_ENV_PROXY` point at a loopback HTTP bridge that only knows how to
dial out through Tor (an unroutable host gets a 502, never a direct connection),
`NO_PROXY` is blanked, and a `sitecustomize.py` plus a Node `--require` shim
refuse any socket or DNS lookup that is not loopback - so code that opens its own
socket fails instead of leaving directly. Everything that leaves is logged to
`~/.onionmind/agent-net.log`.

Ollama's current DSH launcher rejects Onionmind's `--patch`, so the Harness keeps
its own search provider rather than the Tor one; that provider is a Node HTTP
client, so the proxy and the shims put it on Tor anyway. Set
`ONIONMIND_DSH_PATCH=1` once the launcher accepts the patch. The remaining gap is
the shell tool running a command that ignores proxies (`ping`, `nslookup`,
`traceroute`) - refused for Qwen Code through its permissions file, and closable
for the Harness only with an OS egress rule.

## GPU support on the live USB

The live image carries drivers for the machine it boots on. Which GPU you have decides
whether inference runs on the card or falls back to CPU.

| GPU | Driver in the image | Inference |
|---|---|---|
| NVIDIA Maxwell (GTX 900) - Ada (RTX 40) | `nvidia-driver` **550.163.01**, DKMS-built at image-build time | GPU (CUDA) |
| **NVIDIA RTX 50-series (Blackwell)** | **none that works** | **CPU only** |
| AMD (amdgpu-supported) | in-kernel `amdgpu` + `firmware-amd-graphics` + ROCm payload | GPU (ROCm) |
| Intel / older | none | CPU |

### RTX 50-series does not work, and there is no apt fix

Blackwell needs driver **570+**. Debian packages nothing newer than **550.163.01** anywhere —
checked `trixie`, `trixie-backports`, `sid` and `forky`, and all four resolve to the same
version. So "add backports and install the newer driver" does not work; it is the same driver.

Until Debian ships 570+, a 50-series card boots fine and runs the model on **CPU**. If you need
GPU inference on Blackwell today, the live USB is the wrong vehicle — use `install-onionmind.sh`
on a normal distro whose driver is current.

### Two things to check before booting

- **Secure Boot must be OFF.** The NVIDIA module is unsigned and will not load with it on.
- **The ROCm payload adds ~1GB.** Build with `ROCM=0 ./usb/build.sh <tier>` to drop it if every
  machine you boot on is NVIDIA.

Verified by inspecting a built ISO, not by reading config: 1,825 ROCm libraries under
`usr/lib/ollama/rocm/`, 639 `amdgpu` firmware files, `nvidia.ko` present, CUDA v12 and v13
alongside ROCm. Ollama selects the backend at runtime from the hardware it finds.

## Android

Two ways to run the light tiers on a phone — the **APK** (the product) and
**Termux** (the tinkerer's path). Both run the model entirely on-device with
Tor-routed search; neither needs root.

### The APK (`android/`)

A standalone app, built in Docker from `android/Dockerfile`, ~14 MB:

- **Engine:** llama.cpp's `llama-server`, cross-compiled for arm64 with the
  NDK (the host-compiled web-UI embed tool needs a host `g++` present — the
  Dockerfile documents it). Shipped as `jniLibs` with `LD_LIBRARY_PATH`
  pointing the linker at the app's native dir.
- **Tor:** the prebuilt `libtor.so` from Maven Central
  (`info.guardianproject:tor-android` — the same binary Orbot runs), exec'd
  with a generated torrc.
- **UI:** a WebView talking to a NanoHTTPD server on `127.0.0.1:8081` only —
  onboarding (model download with progress), chat, status dots for tor/model.
  Android shares loopback across every installed app, so the chat page is
  served only under a high-entropy capability URL handed to this app's own
  WebView, and the JSON API additionally requires a header token that never
  appears in a URL; both secrets are random per app start and not persisted.
- **Child ownership:** a listening port is not ownership evidence on Android.
  `OwnedLoopbackProcess` (core) treats `llama-server`'s port as ready only
  while the exact child this app launched is alive: an already-listening
  `:8080` is rejected rather than adopted, and shutdown destroys only the
  child we started — never whoever owns the port. Tor readiness follows the
  same rule: our child alive plus the SOCKS port open.
- **Search agent:** `android/core` is a pure-Kotlin port of `onionmind.py` —
  including a hand-written SOCKS5 client with username/password auth, because
  **fresh random credentials per search are what make tor build a separate
  circuit per query**, and the JVM's stock SOCKS support can't authenticate.
  The agent loop, DDG onion endpoint, per-block result parsing and
  thinking-strip mirror the Python line for line. The `web_search` tool is
  advertised to llama-server only on the exact turn the user allowed search;
  a search tool call without that permission is answered with a refusal and
  the network callback never runs, and a tor that fails to start safely
  yields "(Tor could not start safely; web search was not performed)"
  instead of a query.
- **Model:** downloaded on first launch into app storage (4B/9B by RAM),
  resumable with HTTP Range. APK stays 14 MB; the 4B adds 2.5 GB on-device.
- **Verified:** `:core` runs live network tests against a real tor daemon
  inside the build image (`android/itest.sh`) — SOCKS auth handshake, circuit
  isolation, onion search, result parsing. The adapter logic has the same
  mock-server test as the desktop (`tests/test_backends.py`). Offline JVM
  suites pin the Android-specific invariants: `AgentPrivacyTest` (the search
  tool is scoped to the turn that allowed it; a spurious tool call is refused
  without touching the network), `OwnedLoopbackProcessTest` (an occupied
  port is rejected, a dead child never turns a stranger's listener into ours,
  waiting fails closed on child death, stop destroys only our child), and
  `Socks5Test` (a request built through the real okhttp client hands the
  hostname to the SOCKS5 proxy unresolved — remote DNS, no system-resolver
  leak — offline against a stub proxy). The
  loopback capability/token split in `Server.kt` is code review only — no
  test covers it yet.
- **Not verified: anything on a phone.** No device or emulator ran this APK;
  it is debug-signed, first install is yours, and the Termux path is the
  battle-tested fallback.

### Termux

Get [Termux from F-Droid](https://f-droid.org) (the Play Store build is
abandoned) and paste `install-onionmind-android.sh`. It builds llama.cpp in
Termux (~15–20 min, one-time), installs the `tor` package, picks 4B/9B by
`/proc/meminfo`, and installs an `onionmind` command. `onionmind.py` itself
auto-detects its backend — ollama on `:11434`, llama-server on `:8080` — with
the tool-calling format translation living in the script.

On desktop, `onionmind` opens the native workbench. `onionmind-code "task"`
launches Ollama's official DSH integration with the public `headless` profile,
so it returns output in the current terminal and never opens DSH's browser UI.
The native workbench uses the same command in Agent mode and sets the selected
project as the working directory.

`dsh-onionmind-tor-search.js` remains a current DSH search-provider adapter for
direct Harness configurations that support patches. It invokes
`onionmind.py --tor-search`, which verifies Tor and creates the same fresh circuit
used by Onionmind Chat. Ollama's current `ollama launch dsh` wrapper rejects a
custom `--patch`, so the shipped wrapper does not claim to load it. Its built-in
provider is still a Node HTTP client and therefore inherits the Tor bridge and
socket shim described above. This is application-level containment: proxy-aware
traffic and Python/Node children are covered, while an arbitrary external binary
that ignores proxies requires an OS egress rule, container, or Matchstick.

**Phone realities, honestly:** community figures (not our measurements) put
the 4B Q4 at roughly 5–15 tok/s depending on chipset. Android will kill
background apps — disable battery optimisation for the app or Termux, and
expect warmth: sustained inference is a benchmark workload for a phone.

## Going further on privacy

Tor protects Onionmind's explicit network paths; it does not reconfigure the whole
host. Search and native desktop updates use Tor. Agent traffic that honors the
injected proxy uses the Tor bridge, and Python/Node direct sockets are refused.
Model downloads, source-install updates, normal applications, and external agent
commands that ignore proxies can still use the host network. Extending anonymity
means changing **the host**, not just this tool.

### Start with a threat model

"Stay hidden" from whom? The answers diverge completely:

| Adversary | What actually helps |
|---|---|
| Ad networks, data brokers | Onionmind search uses Tor and local inference emits no telemetry; normal host applications remain outside this boundary. |
| Your ISP / network admin | They see *that* you use Tor, not what you search. Bridges hide even that. |
| The search engine | Handled — onion service, fresh circuit per query, no account |
| Someone with your disk | Full-disk encryption, encrypted swap. Tor is irrelevant here. |
| A global passive adversary | Traffic-correlation attacks defeat Tor. Accept this or don't do the thing. |

Most privacy advice is wasted effort aimed at the wrong row.

### The tension nobody mentions

Anonymity systems are built on being **amnesic and disposable** — Tails forgets everything at
shutdown. A 27B model needs **12GB of VRAM, 12GB of disk, and a specific GPU**. You cannot run
this on a Tails USB. Every step toward anonymity costs you the ability to run the model, and
every step toward running the model costs anonymity. Where you land on that line is the real
decision; the rest is detail.

### The live USB — having it both ways

`usb/` in this repo builds **Onionmind Matchstick**, a Debian live image that keeps the
anonymity half of that bargain and pays for the model half with the host's *hardware*
instead of its disk: NVIDIA driver, Ollama and the weights baked into the stick, tor daemon,
an nftables ruleset where **tor is the only process allowed off the box**, fresh MAC per
boot, RAM-only overlay, internal disk never mounted. Boot any machine, use its GPU/CPU/RAM,
power off — nothing happened. It is not Tails; it's Tails' mechanics with the model added —
fail-closed tor-only networking, clock sync over Tor, MAC randomization, amnesia — built with
`live-build` rather than Tails' own KVM-only toolchain. See **[usb/README.md](usb/README.md)**.

### This setup's actual local leaks

Verified on this machine, not assumed:

- ✅ **Ollama does not log prompts.** 888KB of `server.log` searched for text from every test
  prompt — zero hits. It logs connections and errors, not content. Conversations are not
  persisted either; only blobs and manifests exist on disk.
- ❌ **Shell history captures one-shot queries.** `onionmind "sensitive question"` puts that
  string in PSReadline history (Windows) or `~/.bash_history` (Linux), in plaintext, forever.
  **Use interactive mode** — queries typed at the `you>` prompt go to Python's `input()` and
  never touch the shell.
- ❌ **Swap and hibernation.** Prompt text and model context live in RAM, and RAM pages to disk.
  `hiberfil.sys` on Windows is a full RAM image. Encrypt or disable both.

### Choosing an OS — honestly ranked

Where the usual candidates sit, for completeness:

| | What it is | For this use |
|---|---|---|
| **Whonix** | Two VMs: a gateway that forces *all* traffic through Tor, and a workstation with no route to the internet except the gateway | **Best fit.** Fail-closed at the network layer — a leak is structurally impossible, not just discouraged. Workstation can't learn your real IP even if compromised. GPU passthrough is possible but work. |
| **Qubes OS** (+ Whonix) | Compartmentalisation — everything in disposable VMs | Strongest, hardest. GPU passthrough is genuinely painful. |
| **Tails** | Amnesic live USB, everything through Tor, forgets on shutdown | Excellent anonymity, **cannot run this**. Not what it's for — but this repo's own `usb/` build is a Tails-style image that can. |
| **Parrot OS** | Debian derivative with security *tooling*, plus AnonSurf | Fine as a daily OS, but it is a **pentest distro, not an anonymity guarantee**. AnonSurf routes through Tor, but it is not fail-closed like Whonix's gateway and not amnesic like Tails. Choosing Parrot for anonymity mostly buys you preinstalled tools. |

If the goal is "run this model and never leak my IP", **Whonix is the honest answer** — its
gateway makes leaks architectural rather than a matter of configuring things correctly.

### Network layer

- **Bridges** (`obfs4`, `snowflake`) — if you need to hide *that you use Tor at all*. Plain Tor
  is visible to your ISP as Tor traffic even though contents aren't.
- **VPN → Tor** — hides Tor use from your ISP, moves that trust to the VPN. **Tor → VPN** is
  usually a mistake: it gives the VPN a stable identity for your exit traffic.
- **Per-app vs system-wide** — this tool proxies itself. Everything else on the box still goes
  out directly, including OS telemetry, updaters, and your normal browser.

### Disk layer

- Full-disk encryption: **LUKS** (Linux) or **BitLocker** (Windows). Without it everything above
  is decoration for anyone holding the drive.
- Encrypted or disabled swap.
- Disable hibernation, or accept that a RAM image sits on disk.

### What no amount of Tor fixes

- **Query content.** Search something only you would search and the query identifies you
  regardless of circuit.
- **Stylometry.** How you write is a fingerprint. Long prose over Tor is attributable.
- **Logging into anything.** One authenticated session over Tor links that identity to the
  circuit and often to everything else in it.
- **Timing correlation.** An adversary seeing both ends of the circuit does not need to break
  the encryption.

## Configuration

| Setting | Where | Notes |
|---|---|---|
| `ONIONMIND_DIR` | env var | Where weights live. **Keep it on an NVMe.** |
| `num_gpu` | Modelfile | `99` = all layers on GPU. Lower it if speed swings. |
| `num_ctx` | Modelfile | `8192`. Each doubling costs ~1.5GB of KV cache. |
| `num_predict` | `onionmind.py` | `16384`. **Do not lower.** See *empty response* below. |
| `MODEL` | `onionmind.py` | Which Ollama model the search agent talks to. |
| `OLLAMA_MODELS` | env var | Blob store location. NVMe matters here too. |

Applying a change:

```powershell
ollama create inferno -f "$env:LOCALAPPDATA\qwen\Modelfile"
```

## Troubleshooting

### Speed swings wildly, or is 3× slower than expected

VRAM contention. At `num_gpu 99` the 27B claims 11.0GB of 12.3GB, leaving ~400MB for the
Windows desktop and every browser tab. When they need more, the driver silently pushes model
layers into **shared system memory** — Ollama still reports "100% GPU" while throughput
collapses. Close browsers, or lower `num_gpu` to ~56 to buy headroom.

More layers on the GPU is **not** monotonically better. Measured on an oversized 16.3GB
build that could not fit — the shape is what matters, not the absolute numbers:

| `num_gpu` | Speed |
|---|---|
| 44 | 6.6 tok/s |
| 48 | **8.2 tok/s** ← peak |
| 52 | 6.1 tok/s |
| 99 | 3.9 tok/s |

Past the point where it fits, you pay shared-memory costs worse than Ollama's own CPU
offload. The naive "put everything on the GPU" setting was the worst of the four.

### The model returns an empty response — and it looks like a refusal

It isn't. These are reasoning models: they emit thinking tokens *before* the answer, and
`num_predict` caps the whole generation. Cap it too low and the budget is spent thinking,
so you get an empty string back — indistinguishable from a refusal at a glance.

Measured on the 9B with the same prompt:

| `num_predict` | Result |
|---|---|
| 700 | empty |
| 2048 | empty |
| 4096 | empty |
| **-1 (unlimited)** | **full answer, 5514 tokens, `done_reason: stop`** |

It needed 5514 tokens to reach its first word of answer. The tools ship `16384` to leave
headroom for longer reasoning runs. If you see empty responses, raise it — don't assume
the model refused.

Reaching that ceiling is recoverable rather than terminal. Onionmind keeps the cutoff
reasoning for one bounded `4096`-token finalization pass, disables thinking and tools for
that pass, and asks for a concise best-effort answer plus anything still unfinished. A
successful result replaces the oversized reasoning in saved history, so the next turn
continues from a compact checkpoint. If finalization also reaches its limit, any partial
answer is retained and marked incomplete; if there is no answer at all, the unfinished
reasoning state stays attached so a later `continue` can resume it. The recovery pass runs
at most once.

### The model echoes your prompt, or leaks "You are a helpful assistant!"

Missing chat template. Importing a bare GGUF gets you `TEMPLATE {{ .Prompt }}`, a raw
passthrough — the model *continues text* instead of chatting. Check with:

```powershell
ollama show --modelfile inferno | Select-String TEMPLATE
```

It should show ChatML (`<|im_start|>`), not `{{ .Prompt }}`. The installer sets this.

### `ollama create` crawls for 15+ minutes at "gathering model components"

The blob store is on a slow disk. A Windows **Storage Space** pooling HDDs benchmarked at
604 MB/s against 3296 MB/s for an NVMe — imports crawled and first model load took 119s
instead of 9.8s. Point `OLLAMA_MODELS` at an NVMe.

### `context type MTP requested but model doesn't contain MTP layers`

You put a `DRAFT` line in the Modelfile with a non-MTP model. Ollama's `DRAFT` directive is
wired to the model's own MTP head, not an external draft model. Use an MTP build or drop the
directive.

### Search returns nothing, or `403 Forbidden`

DuckDuckGo blocks Tor exit nodes on its clearnet endpoint. The script prefers the `.onion`
service specifically to avoid this, and retries on a fresh circuit. If both fail, the exit is
rate-limited — retry for a new circuit.

### `No Tor proxy on 9150/9050`

Tor Browser isn't running, or is still on its connect screen. Open it, wait for the circuit,
retry. This message means the tool **refused to search** rather than leaking — working as
intended.

### Linux: `ollama create` fails with "no such file" on a path in your home directory

Ollama runs as `User=ollama`. On Arch the unit sets **`ProtectHome=yes`**, making `/home`
structurally invisible to the service — no `chmod` fixes it. Ubuntu's vendor unit has no
`ProtectHome`, but home dirs are often mode 750. Either way, weights must live outside
`$HOME`; the installer uses `/var/lib/qwen`.

### Linux: `pip install` refuses with "externally-managed-environment"

Arch and Ubuntu 23.04+ both mark the system Python externally managed (PEP 668). Use the
packaged versions — `python-requests python-pysocks` on Arch, `python3-requests python3-socks`
on Ubuntu — or a venv. The installer uses the distro packages.

### Linux: tuning env vars appear to have no effect

Ollama runs as a systemd service under its own user, so exporting `OLLAMA_FLASH_ATTENTION`
in your shell does nothing — the server never sees it. It has to be a unit drop-in at
`/etc/systemd/system/ollama.service.d/`, followed by `daemon-reload` and a restart. The
installer writes one.

### HTTP 401 downloading weights

That HuggingFace repo is **gated** — public metadata, but files need you to accept terms while
signed in. The repos in the installer are ungated. If you swap in a gated one you'll need an
`HF_TOKEN`.

## Files

| File | |
|---|---|
| `install-onionmind.ps1` | One-paste installer. Everything below is produced by it. |
| `onionmind.py` | Local inference/search core, compatibility CLI, and legacy-UI fallback. |
| `onionmind_desktop.py` | Native PySide6 workbench and asynchronous process/UI adapters. |
| `onionmind_desktop_core.py` | Qt-free session/settings storage, workspace/Git inspection, model labels, and Harness command spec. |
| `install-onionmind.sh` | Linux installer. Detects Arch vs Ubuntu/Debian; systemd either way. |
| `install-onionmind-android.sh` | Android installer, runs inside Termux. Builds llama.cpp, installs tor + model by RAM. |
| `android/` | The standalone APK: Kotlin app + pure-Kotlin port of the search agent (`:core`), Docker build from source. |
| `build.py` | Single-sources all three desktop Python modules + icon payloads into the installers; `--check` flags drift. |
| `pyproject.toml` / `requirements-desktop.txt` | Constrained native runtime and standalone-build dependencies. |
| `tools/build-desktop.ps1` | PowerShell 5.1-compatible Nuitka standalone build and validation entry point. |
| `Modelfile` | Generated. Template, stop tokens, `num_gpu`, `num_ctx`. |
| `logo.svg` / `logo-small.svg` | The mark. Small variant for favicons — the full one turns to mush below ~24px. |
| `onionmind.ico` | The mark as Windows icon (16–256px), rendered from `logo.svg`. Regenerate: `rsvg-convert` each size + `convert` them into an ico (see `build.py`), then run `python build.py` to re-inject into the installers. |
| `usb/` | **Matchstick** — the live-USB build kit: Tails-style amnesia + the host machine's GPU. See `usb/README.md`. |

## What has been verified

| | Windows | Linux |
|---|---|---|
| Installer runs end-to-end | ✅ PS 5.1 and pwsh 7.6 | ⚠️ package phase only (container) |
| Model registers and answers | ✅ | ❌ needs a GPU host |
| Vision model reads an image | ✅ installer-built | ❌ needs a GPU host |
| Tor search returns results | ✅ | ✅ real circuit, onion endpoint |
| Fails closed without Tor | ✅ | ✅ |
| apt/pacman package names | — | ✅ resolved and installed |
| systemd units | — | ❌ containers have no systemd |

Linux was tested in a real `ubuntu:24.04` container. What a container cannot exercise —
systemd services and GPU detection — remains unverified, though both units were read from
vendor source. Arch's package phase is verified against the Arch package API only.

The live-USB kit's internals were validated the same way, in `debian:trixie` containers
(`usb/tests/`): every `lb config` flag resolves, the package names exist on trixie, ollama
bakes and garbage-collects the model store, and it serves that store read-only. The full
image build and a boot on real hardware have not been run — `usb/README.md` keeps the
honest list.

The llama-server backend adapter (the Android path) is exercised against a mock
OpenAI server in `tests/test_backends.py` — translation, string-encoded tool
arguments, positional tool ids, and a full search turn. Nothing has run on an
actual phone yet.

macOS support follows the same honesty rule. What is verified offline:
`tests/test_privacy_contracts.py` covers the darwin Tor path end to end with
mocks — a listener is reused before anything launches, Homebrew's `tor` starts
under a generated torrc (SOCKS 9050, private data dir, no control cookie) with
output discarded, and a missing binary fails closed with an install hint. The
installer's Homebrew branch passes `bash -n` and the payload contract. What is
**not** verified: nothing has been executed on Apple hardware — the installer
run, brew service behaviour, PySide6-on-mac rendering, and Metal model
performance are all first-run territory.

## Notes

- **Vision** is installed automatically as `inferno-vision`. The `mmproj` is the
  vision tower in its own file — architecture-specific, not quant-specific — so one projector
  binds to any Qwen3.8-27B build. It shares the base blob, costing ~900MB rather than a second
  full model. Verified reading shapes, colours and text from a test image.
- **Function calling** works; that's what the search agent is built on.
- **Context** is 8192 here against a native 262144. That's a RAM ceiling, not a config one.
- **Quantization honesty**: the 12GB build reports as `IQ2_S`. Mixed-precision builds report
  their lowest tier, so the 3.69bpw average is plausible — but quality loss is real, and it can
  be loose with numbers. The Q4_K_M build is steadier when precision matters.
- **Uncensored costs nothing in speed.** Abliteration is a weight edit: same architecture, same
  size, same arithmetic per token. Slow models are slow for being big.
