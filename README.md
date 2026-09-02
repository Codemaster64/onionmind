<div align="center">

<img src="logo.svg" width="120" alt="Onionmind">

# Onionmind

**A native, local-first coding workbench with Ollama models and Tor search.**

![platforms](https://img.shields.io/badge/platforms-Windows%20%7C%20Linux%20%7C%20macOS%20%7C%20Android%20%7C%20USB-7D4698)
![local](https://img.shields.io/badge/inference-local--first-black)
![speed](https://img.shields.io/badge/speed-up%20to%20166%20tok%2Fs-59316C)
![license](https://img.shields.io/badge/license-MIT%20%2F%20Apache--2.0-black)
![status](https://img.shields.io/badge/status-beta-9146F0)
![project](https://img.shields.io/badge/open%20source-community--maintained-59316C)

[![Desktop build](https://github.com/Codemaster64/onionmind/actions/workflows/desktop-build.yml/badge.svg?branch=main)](https://github.com/Codemaster64/onionmind/actions/workflows/desktop-build.yml)
[![USB kit](https://github.com/Codemaster64/onionmind/actions/workflows/usb-tests.yml/badge.svg?branch=main)](https://github.com/Codemaster64/onionmind/actions/workflows/usb-tests.yml)
[![Ollama Tor plugin](https://github.com/Codemaster64/onionmind/actions/workflows/ollama-tor.yml/badge.svg?branch=main)](https://github.com/Codemaster64/onionmind/actions/workflows/ollama-tor.yml)

[![Download for Windows](https://img.shields.io/badge/Windows-Onionmind--Setup.cmd-0078D6?style=for-the-badge&logo=windows&logoColor=white)](https://github.com/Codemaster64/onionmind/raw/refs/heads/main/onionmind-setup.cmd)
[![Download for Linux](https://img.shields.io/badge/Linux-install--onionmind.sh-333333?style=for-the-badge&logo=linux&logoColor=white)](https://github.com/Codemaster64/onionmind/raw/refs/heads/main/install-onionmind.sh)
[![Download for Android](https://img.shields.io/badge/Android-releases%20page-3DDC84?style=for-the-badge&logo=android&logoColor=white)](https://github.com/Codemaster64/onionmind/releases)
[![Get the USB stick](https://img.shields.io/badge/USB-Stick%20Matchstick-7D4698?style=for-the-badge&logo=usb&logoColor=white)](#matchstick--the-amnesic-usb-stick)

**Matchstick — [download the pre-built USB image](https://github.com/Codemaster64/onionmind/releases/tag/matchstick-pocket)** · [Windows launcher `.cmd`](https://github.com/Codemaster64/onionmind/raw/refs/heads/main/matchstick.cmd) · [Linux/macOS launcher `.sh`](https://github.com/Codemaster64/onionmind/raw/refs/heads/main/matchstick.sh)

Windows ships two routes: the one-click `onionmind-setup.cmd` above, and the
rolling [`Onionmind-Windows-x64.zip`](https://github.com/Codemaster64/onionmind/releases/download/desktop-latest/Onionmind-Windows-x64.zip)
the desktop workflow rebuilds after every push to `main` — that rolling release
is also the in-app updater's feed. Tagged release artifacts, including the
Android APK, are built and attached manually. The repository is public, so
release downloads need no sign-in.

[all downloads](https://github.com/Codemaster64/onionmind/releases) · desktop and Termux installs include the `onionmind` command

**No Onionmind account · no Onionmind telemetry.** Opening Onionmind makes no
external request and starts no network service. Local chat, project context,
session history, and model inference stay on your computer. Every external
action is user-triggered: Tor search is enabled per turn, and on Windows
Onionmind then starts only Tor's background process - no Tor Browser window, no
console - and shows its state in the Tor indicator. A search sends its query to
DuckDuckGo's onion service; model installation downloads weights; and agent
traffic is routed through Tor or refused. The wrapper code is MIT-licensed;
model weights retain the upstream licenses documented in `THIRD_PARTY_NOTICES.md`.

On desktop, Onionmind is a standalone PySide6 application—not a browser shell,
WebView, or localhost website. Its three-pane workbench combines project and
session navigation, a privacy-filtered conversation, integrated terminal, Git changes,
context inspection, model management, and a one-shot Qwen Code agent mode.
Each Chat turn immediately opens a textual **Thinking** state in the pending
Onionmind reply. Model output stays behind that state until the completed response
has been sanitized for private reasoning markup, then appears in the same reply.
Its restrained motion stops on completion or failure; reduced-motion mode keeps
the same state static.
The interaction model is informed by the public Claude Code and Codex workflows,
while the product identity, model tiers, storage, and implementation remain
Onionmind's own. The project uses published documentation and open-source
interfaces only—no leaked source, private prompts, proprietary assets, or copied
product code.

*Onion-routed on the outside. A mind that stays local at the centre.*

</div>

---

## Get Onionmind

The shell installer provides **`onionmind`** and a desktop icon for the native
workbench. **`onionmind-code "task"`** runs a one-shot repository-aware DeepSeek
Harness task through Ollama. **`onionmind-chat`** is retained as an alias for the
workbench. The portable Windows archive is different: neither the ZIP nor its
bootstrap creates a command or shortcut. Run `Onionmind.exe` from the extracted
folder, or create a shortcut to that executable yourself.

**Windows** — one download, one double-click:
**[⬇ Onionmind-Setup.cmd](https://github.com/Codemaster64/onionmind/raw/refs/heads/main/onionmind-setup.cmd)**
(about 400 KB — the current installer and its source payloads in one file).
Double-click it, wait out the
model download (10–16 GB, resumable), get the desktop icon *and* `onionmind`
in new terminals. SmartScreen may grumble at a self-contained script — that's
what it does; *More info → Run anyway*.

The Tor indicator starts at **Off**, or **Proxy · port** when it detects an
unverified pre-existing SOCKS listener. Enabling **Allow Tor search this turn**
can change it to **Starting** and then **Running · 9150**. Onionmind launches
`tor.exe` hidden, never `firefox.exe`, and stops only the Tor process it owns
when the app closes. An already-running local Tor proxy is reused and left alone.

Prefer a portable install? Extract the rolling zip instead and run
`onionmind-bootstrap.cmd` from the extracted folder. The bootstrap takes an
**offline local inventory first**: it reports supported, missing, and
out-of-date components without downloading anything or starting Tor, Ollama,
or another service. It shows the exact install plan and direct-network
destinations before you apply anything — applying is an explicit
`-Apply -AllowDirectNetwork` step, and only confirmed missing or out-of-policy
components are installed.

**Linux & macOS** — one download, one command:
**[⬇ install-onionmind.sh](https://github.com/Codemaster64/onionmind/raw/refs/heads/main/install-onionmind.sh)**,
then `bash install-onionmind.sh` (Arch, Ubuntu/Debian, or macOS with Homebrew;
run as your normal user). Same deal — the right model for your hardware, an
`onionmind` command and, on Linux, a desktop launcher. The macOS path is new
and honestly labelled: its Tor logic is unit-tested offline and the installer
is syntax-checked, but it has not been run on Apple hardware yet.

Already set up and want the newest native Windows build directly? Download the
rolling [`Onionmind-Windows-x64.zip`](https://github.com/Codemaster64/onionmind/releases/download/desktop-latest/Onionmind-Windows-x64.zip),
rebuilt from `main` after every successful desktop workflow.

For repository-aware coding work from a terminal:

```text
onionmind-code "inspect this repository and explain the failing tests"
```

After a direct-network confirmation, it starts Qwen Code on the selected raw
Ollama model name. Agent mode currently requires Node.js `^22.19` or `24+` and
reports an actionable setup message when that runtime is missing. Agent mode is
currently a developer preview.

The agent's only way off the machine is Tor. `onionmind-code` hands over to
`onionmind.py --agent`, which verifies a circuit first and exits if there is
none, then runs the Harness with every proxy variable pointed at a loopback
bridge that exits through Tor, and with its Python and Node children refused any
socket that is not loopback. Commands that ignore proxies entirely (`ping`,
`nslookup`, `traceroute`) are the remaining gap; closing that needs an OS egress
rule rather than an environment variable.

Inside the workbench, switch the composer from **Chat** to **Agent** to run the
same repository-aware path without opening a browser. Output, cancellation, and
the network boundary remain visible in the transcript and activity inspector.

Onionmind never checks for or installs updates automatically. To inspect an
existing source-based installation, run the manual updater yourself:

```text
onionmind-update
```

The desktop workbench also updates itself in place: **Settings → Check for
updates** (the status bar keeps an **Updates…** entry reachable at all
times). Nothing is contacted until you press it — and if you want it fully
automatic, tick **Check automatically over Tor** in Settings and Onionmind
will look for updates for as long as it runs. The check and the ~50 MB bundle
download both travel over Tor — a fresh circuit each, verified size and
SHA-256, staged swap with an automatic backup — and never fall back to a
direct connection.

**Android (light models)**

Android **1.4** is the current source version. APKs are built and attached to
the [releases page](https://github.com/Codemaster64/onionmind/releases) manually;
an `Onionmind-1.4.apk` is available only when that exact asset is shown there.
The older 1.3 APK is intentionally not linked because it predates the Android
remote-DNS protection. On first launch, choose and confirm the model download
that fits your phone (the 4B on any 8 GB phone, the 9B on 12 GB flagships —
resumable). The engine, Tor binary, and UI are on-device; user-enabled web-search
queries are sent to DuckDuckGo with hostname resolution performed by Tor.
arm64 phones only.

Want a terminal on your phone too? [Termux](https://f-droid.org) +
**[⬇ install-onionmind-android.sh](https://github.com/Codemaster64/onionmind/raw/refs/heads/main/install-onionmind-android.sh)**
gives you the same `onionmind` command Android-side.

**Using it**

```bash
onionmind                    # open the native desktop workbench
onionmind-code "fix the parser tests"   # one-shot repository-aware agent
python onionmind.py "explain this function"       # local CLI chat; search stays off unless enabled for that turn
```

Want the Tor routing without the Onionmind workbench? The self-contained
[`ollama-tor`](ollama-tor/) companion wraps any supported `ollama launch`
integration, verifies Tor before spawning it, and routes proxy-aware child
traffic through a fail-closed Tor bridge.

Or just double-click the icon. The desktop app keeps sessions locally and can
export a conversation; the classic CLI still supports `/save notes.txt`.

### The native workbench

- Open a repository and move between local project sessions without leaving the app.
- Buffer model output behind the Thinking state until privacy filtering completes, stop generation, inspect tool activity, and attach images to vision-capable models.
- Run a real shell in the selected project with command history and cancel support.
- Inspect the bounded project tree, Git status and diff, and recent activity beside the conversation.
- Discover installed Ollama models, see their Onionmind tier names, switch models, and confirm a direct-network pull for another model.
- Run Qwen Code non-interactively in **Agent** mode after confirming its direct-network boundary; no browser window or embedded web UI is involved.

Stop requests terminate the managed shell or agent launcher process. A command
that deliberately starts detached or background child processes may require
manual cleanup in the operating system.

Approvals are on by default: the agent asks before a protected action, and where
there is nobody to ask it stops instead of continuing. Ticking **YOLO: run
without asking** lets it edit files and run commands unattended. YOLO does not
move the network boundary - commands that cannot be proxied stay refused, and
everything still leaves through Tor or not at all.

For source development, install `requirements-desktop.txt` and run
`python onionmind_desktop.py`. GitHub Actions runs the payload check, the core
tests, and the desktop build on every push, plus the USB kit and `ollama-tor`
suites; the same principal checks run locally:

```text
python build.py --check
python tests/test_parser.py
python tests/test_backends.py
python tests/test_desktop_core.py
python tests/test_installer_contracts.py
.\tools\build-desktop.ps1 -Check -PythonExecutable python
.\tools\build-desktop.ps1 -PythonExecutable python
```

The final command produces the standalone Windows bundle. A maintainer packages
it with the offline-first bootstrap as `Onionmind-Windows-x64.zip`, verifies the
archive locally, and uploads it manually when making a release. The build audits
its isolated environment first and does not invoke pip when the constrained
versions are already present. If repair is needed, it prints the package-index
plan and refuses until rerun with `-AllowDirectNetwork` (then prompts unless
`-Yes` is also supplied).

The models are named for the burn, from least heavy to heaviest:
**SPARK < EMBER < BLAZE < INFERNO < CINDER < WILDFIRE < FLASHPOINT < PHOENIX < NOVA < PYRE**.
The models currently shipped map to **SPARK** (2.6B), **EMBER** (4B),
**BLAZE** (9B), and **INFERNO** (27B, with vision support). The remaining names
are reserved for progressively heavier models. **CINDER** is reserved for a
low-and-slow, small embedding model optimized for high recall, search, RAG,
and vector databases.
**INFERNO** is the target high-throughput tier: **30–70B MoE**, optimized for
fast API responses, real-time apps, and customer support agents. **“Full
flame.”** · **Frontier**
**WILDFIRE** is reserved for the next frontier tier: **100B+ MoE**, with
multimodal input, deep reasoning, and long context for research, complex
agents, and enterprise AI. Multi-agent orchestration, heavy tool use, and
autonomous workflows, autonomous ops, and deep automation. **Agentic / Swarm** ·
**“Burn it down.”**
**FLASHPOINT** is reserved as the next tier: **Experimental**, for limited
preview models and new capabilities before full release, for beta testers and
labs. Tagline:
**“Uncontainable.”**
**NOVA** is reserved as **Ultra / Premium**, targeting top benchmarks with
high-cost, specialized training for high-stakes enterprise and scientific
modeling. Tagline: **“One spark away.”**
**PHOENIX** is reserved as **Fine-tune / Recovery**, with the tagline
**“Cosmic burn.”** Distillation, safety tuning, red-teaming, model reset,
custom models, and safety alignment.
**PYRE** is reserved as **Legacy / Collector** for archived or
special-edition models and older versions, for retro access and research
archives, with the tagline **“Rise.”**

---

## Matchstick — the amnesic USB stick

A **whole private operating system on a USB stick**. Boot *any* PC from it: use that
machine's GPU and memory, touch nothing on its disk — and when you power off,
nothing of what you did persists. Like Tails, but it runs a 27B AI.

> Strike it anywhere. Burns bright. Leaves only ash.

1. **Download the launcher:** [Windows `matchstick.cmd`](https://github.com/Codemaster64/onionmind/raw/refs/heads/main/matchstick.cmd)
   or [Linux/macOS `matchstick.sh`](https://github.com/Codemaster64/onionmind/raw/refs/heads/main/matchstick.sh).
   Double-click the `.cmd` on Windows, or run `chmod +x matchstick.sh && ./matchstick.sh`
   on Linux/macOS. You do **not** need to clone the repository. Choose one:

   | Option | What it does | Choose this if… |
   | --- | --- | --- |
   | **Download** | Gets the ready-made **pocket/EMBER** stick with the **Qwen3.5 4B** model (~6.6GB total), verifies it, and writes it to USB. No Docker or build time. | You want the fastest, simplest route and a model that runs on almost any PC. |
   | **Build** | Creates a stick from source and lets you choose **Qwen3.5 4B (`EMBER`)**, **Qwen3.5 9B (`BLAZE`)**, or **Qwen3.8 27B (`INFERNO`)** with vision support. Requires Docker and about an hour. | You want a larger or vision-capable model, or a customized, reproducible build. |

   Plug a
   USB stick when asked, confirm the erase — the script writes the stick for
   you, no Rufus, no command-line plumbing.
2. **Boot any PC** from it (Secure Boot off). You land straight in the chat —
   `sudo onionmind-status` shows every protection verifying itself live.

The pre-built stick is the **pocket** edition (EMBER model — runs on any PC,
GPU optional). Heavier editions (up to the flagship) are one menu choice away
via Build. *The DIY route* (manual docker commands, Rufus, every knob) is
still there: **[usb/README.md](usb/README.md)**.

Inside the stick: the AI and its weights baked in · a firewall where **Tor is the
only process allowed to leave the machine** · a fresh fake MAC address every boot ·
clock synced over Tor · RAM scrubbed on shutdown. Nothing installs. Nothing
persists. Nothing to find.

---

## Choose your version

**Onionmind (installed)** picks its own model to fit your GPU:

| Your GPU | What you get |
|:---|:---|
| 12 GB+ *(RTX 3080 / 4070 Ti class)* | **The full 27B, with vision** — reads images too |
| 8 GB+ | The full 27B, squeezed to fit |
| 6 GB+ | 9B — sharp, and *faster* than the big one |
| Anything / no GPU | 4B — no built-in content filters, searches through Tor, runs on a potato |
| **Android phone** *(Termux)* | the 4B on any 8 GB phone, the 9B on 12 GB flagships |

**Matchstick editions** — you pick when building the stick (`flagship` and
`12gb` both work — name or code):

| Edition | Code | Runs great on | Stick |
|:---|:---|:---|:---|
| **pocket** — the pocket rocket | `4b` | any PC, GPU optional | 16 GB |
| **daily** — the daily driver | `9b` | 6 GB+ GPU | 16 GB |
| **standard** — the big brain, squeezed | `8gb` | 8 GB GPU | 32 GB |
| **flagship** 💜 — 27B + vision | `12gb` | 12 GB GPU *(3080 Ti class)* | 32 GB |
| **max** — the flagship, full fat | `17gb` | 17 GB+ GPU *(4090 class)* | 32 GB |

---

## Measured, not promised

On a regular RTX 3080 Ti gaming rig:

| Model | Speed |
|:---|:---|
| 4B | **166 tokens/second** |
| 9B | **116 tokens/second** |
| Full 27B | **~19 tokens/second** *(40+ when the GPU is otherwise idle)* |

Every figure in this repo was measured on real hardware — including the
disappointing ones. [See what has been verified, and what hasn't.](TECHNICAL.md#what-has-been-verified)

---

## Under the hood

The 60-second version for engineers — [the long version is here](TECHNICAL.md),
and contributors should start with the code map in [ARCHITECTURE.md](ARCHITECTURE.md):

- **Chat searches are designed not to leak the user's IP address.** Every Onionmind Chat search
  builds a *fresh Tor circuit*, resolves DNS through Tor, and hits DuckDuckGo's
  onion endpoint so no exit node ever sees the query. DuckDuckGo still sees the
  search terms. Correlation risks remain: an adversary watching both ends may be
  able to correlate traffic. If the circuit doesn't verify, the tool refuses to
  search.
- **The model is baked, not downloaded.** On Matchstick, weights are deduplicated
  into the image at build time and served from a read-only store — boot-time
  network usage: zero.
- **The firewall is structural.** An nftables ruleset loads *before* networking
  exists: only Tor, DHCP and loopback can leave. Even root can't open a clearnet
  socket. DNS is blocked outright.
- **The driver can't rot.** The NVIDIA kernel module is compiled against the exact
  kernel on the stick at build time — and that kernel never updates. The classic
  driver-vs-kernel breakage is structurally impossible.
- **Everything is tested.** Six validation suites (`usb/tests/`) exercise the
  whole stack in containers — Tor bootstrap, model baking, firewall ordering,
  read-only serving.

---

## The honest small print

We won't oversell you ([full threat model](TECHNICAL.md#going-further-on-privacy)):

- An adversary watching both ends of the network can correlate traffic. That's a
  Tor limit, not a product feature — Tails has it too.
- What you *search for*, and how you *write*, can identify you regardless of any
  technology.
- No GPU? It still runs — on the CPU. Fine for the small models, painful for the 27B.
- Agent mode refuses to start without verified Tor and sends proxy-aware child
  traffic through a Tor-only loopback bridge. Python and Node direct sockets are
  blocked, but an external shell command that ignores proxy settings remains an
  application-level gap; use Matchstick, a container, or an OS egress rule when
  the whole machine must be fail-closed.
- On a phone: expect phone speeds — roughly 5–15 tokens/second on recent
  chipsets — and disable battery optimisation for Termux, or Android will kill
  it mid-chat.
- Matchstick needs Secure Boot off (unsigned driver), and covers NVIDIA GTX 900 →
  RTX 40 plus modern AMD for GPU speed; RTX 50 falls back to CPU until Debian
  ships a newer driver.

---

## Legal and acceptable use

Released as an open-source project. Wrapper code: **MIT** ([LICENSE](LICENSE)).
Model weights: **Apache-2.0**, with attribution. See
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and [NOTICE](NOTICE) for
licenses and notices.

To the extent the EU AI Act applies, this project relies on the open-source
exception where applicable. This is not a guarantee and should not be read as a
definitive legal conclusion.

The maintainers do not operate a service and do not collect, store, or process
user prompts or outputs. Local chat processing happens on the user's device.
Chat search queries are sent to DuckDuckGo over Tor and are visible to
DuckDuckGo. Agent tools can contact other services through Tor; external
commands that ignore the inherited proxy remain the documented gap. Tor and
privacy tooling are generally lawful, but lawful use depends on what you do with
them and on applicable law.

“Uncensored” means **no built-in content filters**. The model may still refuse or
fail unpredictably. An uncensored model removes refusals, not laws — responsibility
for generated content lies with the user.

Read the [Acceptable Use Policy](ACCEPTABLE_USE.md), [Terms of Use](TERMS_OF_USE.md),
and [LEGAL.md](LEGAL.md), labelled **not legal advice**. The project is intended
to support privacy, local execution, journalism, research, human-rights work,
and access to information—not unlawful activity.

---

<div align="center">

**Onionmind** · [Get it](#get-onionmind) · [Matchstick](usb/README.md) · [Architecture](ARCHITECTURE.md) · [Technical](TECHNICAL.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [Legal](LEGAL.md)

</div>
