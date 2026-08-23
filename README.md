<div align="center">

<img src="logo.svg" width="120" alt="Onionmind">

# Onionmind

**A native, local-first coding workbench with Ollama models and Tor search.**

![platforms](https://img.shields.io/badge/platforms-Windows%20%7C%20Linux%20%7C%20USB-7D4698)
![local](https://img.shields.io/badge/inference-local--first-black)
![speed](https://img.shields.io/badge/speed-up%20to%20166%20tok%2Fs-59316C)
![license](https://img.shields.io/badge/license-MIT%20%2F%20Apache--2.0-black)
![project](https://img.shields.io/badge/free%20%26%20open%20source-non--commercial-9146F0)

[![Download for Windows](https://img.shields.io/badge/Windows-Onionmind--Setup.cmd-0078D6?style=for-the-badge&logo=windows&logoColor=white)](https://github.com/Codemaster64/onionmind/releases/download/v1.0/onionmind-setup.cmd)
[![Download for Linux](https://img.shields.io/badge/Linux-install--onionmind.sh-333333?style=for-the-badge&logo=linux&logoColor=white)](https://github.com/Codemaster64/onionmind/releases/download/v1.0/install-onionmind.sh)
[![Download for Android](https://img.shields.io/badge/Android-Onionmind--1.3.apk-3DDC84?style=for-the-badge&logo=android&logoColor=white)](https://github.com/Codemaster64/onionmind/releases/download/v1.3/Onionmind-1.3.apk)
[![Get the USB stick](https://img.shields.io/badge/USB-Stick%20Matchstick-7D4698?style=for-the-badge&logo=usb&logoColor=white)](#matchstick--the-amnesic-usb-stick)

**Matchstick — [download the pre-built USB image](https://github.com/Codemaster64/onionmind/releases/tag/matchstick-pocket)** · [Windows launcher `.cmd`](https://github.com/Codemaster64/onionmind/raw/refs/heads/main/matchstick.cmd) · [Linux/macOS launcher `.sh`](https://github.com/Codemaster64/onionmind/raw/refs/heads/main/matchstick.sh)

[all downloads](https://github.com/Codemaster64/onionmind/releases) · every install includes the `onionmind` command

**No Onionmind account · no Onionmind telemetry.** Local chat, project context,
session history, and model inference stay on your computer. Optional network
features have narrower boundaries: Tor search sends its query to DuckDuckGo's
onion service, model installation downloads weights, and DeepSeek Harness agent
traffic is direct rather than Tor-routed. The wrapper code is MIT-licensed;
model weights retain the upstream licenses documented in `THIRD_PARTY_NOTICES.md`.

On desktop, Onionmind is a standalone PySide6 application—not a browser shell,
WebView, or localhost website. Its three-pane workbench combines project and
session navigation, a streaming conversation, integrated terminal, Git changes,
context inspection, model management, and a one-shot DeepSeek Harness agent mode.
The interaction model is informed by the public Claude Code and Codex workflows,
while the product identity, model tiers, storage, and implementation remain
Onionmind's own. The project uses published documentation and open-source
interfaces only—no leaked source, private prompts, proprietary assets, or copied
product code.

*Onion-routed on the outside. A mind that stays local at the centre.*

</div>

---

## Get it in 60 seconds

Every desktop install gives you **`onionmind`** and a desktop icon for the native
workbench. **`onionmind-code "task"`** runs a one-shot repository-aware DeepSeek
Harness task through Ollama. **`onionmind-chat`** is retained as an alias for the
workbench.

**Windows** — one download, one double-click:
**[⬇ Onionmind-Setup.cmd](https://github.com/Codemaster64/onionmind/releases/download/v1.0/onionmind-setup.cmd)**
(75 KB — the entire installer in one file). Double-click it, wait out the
model download (10–16 GB, resumable), get the desktop icon *and* `onionmind`
in new terminals. SmartScreen may grumble at a self-contained script — that's
what it does; *More info → Run anyway*.

*Prefer pasting?* The classic way still works: copy
[install-onionmind.ps1](install-onionmind.ps1) into PowerShell.

**Linux** — one download, one command:
**[⬇ install-onionmind.sh](https://github.com/Codemaster64/onionmind/releases/download/v1.0/install-onionmind.sh)**,
then `bash install-onionmind.sh` (Arch or Ubuntu/Debian, run as your normal
user). Same deal — the right model for your GPU, an `onionmind` command and a
desktop launcher.

For repository-aware coding work from a terminal:

```text
onionmind-code "inspect this repository and explain the failing tests"
```

It starts DeepSeek Harness in its public headless profile using the selected raw
Ollama model name. Agent mode currently requires Node.js `^22.19` or `24+` and
reports an actionable setup message when that runtime is missing. DeepSeek
Harness is currently a developer preview. This agent
path is intentionally labelled **direct network**: Ollama's launcher does not
currently accept Onionmind's custom Tor-provider patch. Tor routing applies to
Onionmind chat search, not arbitrary Harness tools or shell commands.

Inside the workbench, switch the composer from **Chat** to **Agent** to run the
same repository-aware path without opening a browser. Output, cancellation, and
the network boundary remain visible in the transcript and activity inspector.

After the first setup, update the installed code without touching the model:

```text
onionmind-update
```

**Android (light models)**

**[⬇ Onionmind-1.3 APK](https://github.com/Codemaster64/onionmind/releases/download/v1.3/Onionmind-1.3.apk)**
— 14 MB, one file, no app store. Tap it, allow installs from unknown sources,
done. On first launch it downloads the model that fits your phone (the 4B on
any 8 GB phone, the 9B on 12 GB flagships — resumable). The engine, Tor binary,
and UI are on-device; web-search queries are sent to DuckDuckGo over Tor.
arm64 phones only.

Want a terminal on your phone too? [Termux](https://f-droid.org) +
**[⬇ install-onionmind-android.sh](https://github.com/Codemaster64/onionmind/releases/download/v1.0/install-onionmind-android.sh)**
gives you the same `onionmind` command Android-side.

**Using it**

```bash
onionmind                    # open the native desktop workbench
onionmind-code "fix the parser tests"   # one-shot repository-aware agent
python onionmind.py "who won the last election?"  # local CLI chat + Tor search
```

Or just double-click the icon. The desktop app keeps sessions locally and can
export a conversation; the classic CLI still supports `/save notes.txt`.

### The native workbench

- Open a repository and move between local project sessions without leaving the app.
- Stream model output, stop generation, inspect tool activity, and attach images to vision-capable models.
- Run a real shell in the selected project with command history and cancel support.
- Inspect the bounded project tree, Git status and diff, and recent activity beside the conversation.
- Discover installed Ollama models, see their Onionmind tier names, switch models, and pull another model.
- Run DeepSeek Harness headlessly in **Agent** mode; no browser window or embedded web UI is involved.

Stop requests terminate the managed shell or Harness launcher process. A command
that deliberately starts detached or background child processes may require
manual cleanup in the operating system. In the stock DSH headless profile,
interactive approval requests fail closed; custom DSH profiles can change that
composition.

For source development, install `requirements-desktop.txt` and run
`python onionmind_desktop.py`. `tools/build-desktop.ps1` builds the standalone
Windows executable; CI publishes the native artifact on supported release runs.

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
works too, for raw chat without search.

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

The 60-second version for engineers — [the long version is here](TECHNICAL.md):

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
- **Everything is tested.** Seven validation suites (`usb/tests/`) exercise the
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
- DeepSeek Harness Agent mode is a direct-network developer tool. Its web and
  shell activity is not covered by Onionmind Chat's Tor-search boundary.
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
DuckDuckGo; optional Harness tools may contact other services directly. Tor and
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

**Onionmind** · [Get it](#get-it-in-60-seconds) · [Matchstick](usb/README.md) · [TECHNICAL.md](TECHNICAL.md) · [Legal](LEGAL.md)

</div>
