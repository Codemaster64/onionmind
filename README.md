<div align="center">

<img src="logo.svg" width="120" alt="Onionmind">

# Onionmind

**An uncensored AI that lives on your machine — and searches the web through Tor.**

![platforms](https://img.shields.io/badge/platforms-Windows%20%7C%20Linux%20%7C%20USB-7D4698)
![local](https://img.shields.io/badge/data_sent_to_the_cloud-0-black)
![uncensored](https://img.shields.io/badge/uncensored-no%20built--in%20filters-9146F0)
![speed](https://img.shields.io/badge/speed-up%20to%20166%20tok%2Fs-59316C)
![license](https://img.shields.io/badge/license-MIT%20%2F%20Apache--2.0-black)
![project](https://img.shields.io/badge/free%20%26%20open%20source-non--commercial-9146F0)

[![Download for Windows](https://img.shields.io/badge/Windows-Onionmind--Setup.cmd-0078D6?style=for-the-badge&logo=windows&logoColor=white)](https://github.com/Codemaster64/onionmind/releases/download/v1.0/onionmind-setup.cmd)
[![Download for Linux](https://img.shields.io/badge/Linux-install--onionmind.sh-333333?style=for-the-badge&logo=linux&logoColor=white)](https://github.com/Codemaster64/onionmind/releases/download/v1.0/install-onionmind.sh)
[![Download for Android](https://img.shields.io/badge/Android-Onionmind.apk-3DDC84?style=for-the-badge&logo=android&logoColor=white)](https://github.com/Codemaster64/onionmind/releases/download/v1.0/Onionmind-0.1.apk)
[![Get the USB stick](https://img.shields.io/badge/USB-Stick%20Matchstick-7D4698?style=for-the-badge&logo=usb&logoColor=white)](#matchstick--the-usb-stick-that-forgets)

**Matchstick — [download the pre-built USB image](https://github.com/Codemaster64/onionmind/releases/tag/matchstick-pocket)** · [Windows launcher `.cmd`](https://github.com/Codemaster64/onionmind/raw/refs/heads/main/matchstick.cmd) · [Linux/macOS launcher `.sh`](https://github.com/Codemaster64/onionmind/raw/refs/heads/main/matchstick.sh)

[all downloads](https://github.com/Codemaster64/onionmind/releases) · every install includes the `onionmind` command

**No cloud · No account · No telemetry** for the Onionmind maintainers: this is a
free, open-source, non-commercial project. The wrapper code is MIT-licensed;
the model weights are distributed under Apache-2.0 with attribution.

A 27-billion-parameter AI that runs on a gaming GPU in your home — it can even read
images. When it needs fresh information, it searches the web **through the Tor
network**, which is designed to hide your IP address from the search provider.
Your questions, the AI's reasoning, and its answers stay on your computer. Search
queries are the exception: they are sent to DuckDuckGo's onion endpoint through
Tor. DuckDuckGo sees the query, but the design is intended to hide your IP address
from it.

*Onion-routed on the outside. A mind that stays local at the centre.*

</div>

---

## Get it in 60 seconds

Every install also gives you the **`onionmind` command** for the terminal —
same thing the desktop icon runs.

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

For repository-aware coding work, the setup also provides an
**`onionmind-code` launcher**:

```text
onionmind-code
```

It starts DeepSeek Harness through Ollama. This is deliberately a separate
mode: Onionmind remains the local Tor-search chat, while Harness is the agent
that can inspect and modify a workspace. DeepSeek Harness is currently a
developer preview.

The desktop chat also has a **Coding agent** button. It opens the same Harness
session using the currently selected Ollama model.

Harness web searches are replaced with Onionmind's Tor-backed search provider;
the search query is handled by the same Tor-verified code as the main chat.

After the first setup, update the installed code without touching the model:

```text
onionmind-update
```

**Android (light models)**

**[⬇ Onionmind APK](https://github.com/Codemaster64/onionmind/releases/download/v1.0/Onionmind-0.1.apk)**
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
onionmind                    # chat — it searches the web by itself when it needs to
onionmind "who won the last election?"
```

Or just double-click the icon. Type `/save notes.txt` in the chat to export the
conversation — print it from there if you want it on paper.

The models are named for the burn — Matchstick lights them, and the name
encodes the size: **spark** (4B) < **ember** (9B) <
**inferno-27b** (27B, with **inferno-27b-vision** reading images). Smaller installs use
**ember-9b** or **spark-4b**.
works too, for raw chat without search.

---

## Matchstick — the USB stick that forgets

A **whole private operating system on a USB stick**. Boot *any* PC from it: use that
machine's GPU and memory, touch nothing on its disk — and when you power off,
everything you did ceases to exist. Like Tails, but it runs a 27B AI.

> Strike it anywhere. Burns bright. Leaves only ash.

1. **Download the launcher:** [Windows `matchstick.cmd`](https://github.com/Codemaster64/onionmind/raw/refs/heads/main/matchstick.cmd)
   or [Linux/macOS `matchstick.sh`](https://github.com/Codemaster64/onionmind/raw/refs/heads/main/matchstick.sh).
   Double-click the `.cmd` on Windows, or run `chmod +x matchstick.sh && ./matchstick.sh`
   on Linux/macOS. You do **not** need to clone the repository. Choose one:

   | Option | What it does | Choose this if… |
   | --- | --- | --- |
   | **Download** | Gets the ready-made **pocket/spark** stick (~6.6GB), verifies it, and writes it to USB. No Docker or build time. | You want the fastest, simplest route. |
   | **Build** | Creates a stick from source and lets you choose any edition, including larger models. Requires Docker and about an hour. | You want a different edition or a customized, reproducible build. |

   Plug a
   USB stick when asked, confirm the erase — the script writes the stick for
   you, no Rufus, no command-line plumbing.
2. **Boot any PC** from it (Secure Boot off). You land straight in the chat —
   `sudo onionmind-status` shows every protection verifying itself live.

The pre-built stick is the **pocket** edition (spark model — runs on any PC,
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

- **Searches are designed not to leak the user's IP address.** Every web search
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
user prompts or outputs. Processing happens locally on the user's device. Search
queries are sent to DuckDuckGo over Tor and are visible to DuckDuckGo. Tor and
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
