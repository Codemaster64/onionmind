<div align="center">

<img src="logo.svg" width="120" alt="Onionmind">

# Onionmind

**An uncensored AI that lives on your machine — and searches the web through Tor.**

![platforms](https://img.shields.io/badge/platforms-Windows%20%7C%20Linux%20%7C%20USB-7D4698)
![local](https://img.shields.io/badge/data_sent_to_the_cloud-0-black)
![uncensored](https://img.shields.io/badge/uncensored-yes-9146F0)
![speed](https://img.shields.io/benchmark-up%20to%20166%20tok%2Fs-59316C)

No cloud · No account · No telemetry · No refusals

A 27-billion-parameter AI that runs on a gaming GPU in your home — it can even read
images. When it needs fresh information, it searches the web **through the Tor
network**, so nobody — not your ISP, not the search engine — sees what it looked up.
Your questions, the AI's reasoning, its answers: **they never leave your computer.**

*Onion-routed on the outside. A mind that stays local at the centre.*

</div>

---

## Get it in 60 seconds

**Windows**

1. Open **[install-onionmind.ps1](install-onionmind.ps1)** → click *Raw* → copy everything.
2. Paste into **PowerShell**, press Enter.
3. When the download finishes (10–16 GB, resumes if interrupted), **double-click the
   Onionmind icon on your desktop**.

The installer detects your GPU and installs the right model automatically. No
decisions required.

**Linux**

```bash
bash install-onionmind.sh
```

Arch or Ubuntu/Debian. Same deal — one paste, the right model, an `onionmind`
command and a desktop icon.

**Android (light models)**

Install [Termux from F-Droid](https://f-droid.org) (not the Play Store build),
open it, and paste **[install-onionmind-android.sh](install-onionmind-android.sh)**.
The 4B model runs on any 8 GB phone; 12 GB flagships get the 9B. Same chat,
same Tor search — the whole thing lives on the phone, no cloud in sight.
(One-time llama.cpp build takes ~15–20 minutes.)

**Using it**

```bash
onionmind                    # chat — it searches the web by itself when it needs to
onionmind "who won the last election?"
```

Or just double-click the icon. Type `/save notes.txt` in the chat to export the
conversation — print it from there if you want it on paper.

---

## Matchstick — the USB stick that forgets

A **whole private operating system on a USB stick**. Boot *any* PC from it: use that
machine's GPU and memory, touch nothing on its disk — and when you power off,
everything you did ceases to exist. Like Tails, but it runs a 27B AI.

> Strike it anywhere. Burns bright. Leaves only ash.

1. **Build the stick** — one command, needs [Docker](https://docker.com), ~1 hour
   ([full guide](usb/README.md)):

   ```bash
   docker build -f usb/Dockerfile -t onionmind-usb .
   docker run --rm --privileged -v "$(pwd)/usb/cache:/onionmind/usb/cache" \
     -v "$(pwd)/usb/out:/onionmind/usb/out" onionmind-usb 12gb
   ```

2. **Burn** `usb/out/onionmind-matchstick-12gb-amd64.iso` to a USB stick with
   [Rufus](https://rufus.ie) or Etcher.
3. **Boot any PC** from it (Secure Boot off). You land straight in the chat —
   `sudo onionmind-status` shows every protection verifying itself live.

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
| Anything / no GPU | 4B — still uncensored, still searches Tor, runs on a potato |
| **Android phone** *(Termux)* | the 4B on any 8 GB phone, the 9B on 12 GB flagships |

**Matchstick editions** — you pick when building the stick:

| Edition | | Runs great on | Stick |
|:---|:---|:---|:---|
| `4b` | the pocket rocket | any PC, GPU optional | 16 GB |
| `9b` | the daily driver | 6 GB+ GPU | 16 GB |
| `8gb` | the big brain, squeezed | 8 GB GPU | 32 GB |
| `12gb` | **the flagship** 💜 — 27B + vision | 12 GB GPU *(3080 Ti class)* | 32 GB |
| `17gb` | the flagship, full fat | 17 GB+ GPU *(4090 class)* | 32 GB |

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

- **Searches can't leak.** Every web search builds a *fresh Tor circuit*, resolves
  DNS through Tor, and hits DuckDuckGo's onion endpoint so no exit node ever sees
  the query. If the circuit doesn't verify, the tool **refuses to search at all**.
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

<div align="center">

**Onionmind** · [Get it](#get-it-in-60-seconds) · [Matchstick](usb/README.md) · [TECHNICAL.md](TECHNICAL.md)

</div>
