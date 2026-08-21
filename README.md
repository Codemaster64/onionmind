<img src="logo.svg" width="110" align="left" alt="">

# Onionmind

### An uncensored AI that lives on *your* machine — and searches the web through Tor.

![platforms](https://img.shields.io/badge/platforms-Windows%20%C2%B7%20Linux%20%C2%B7%20Bootable%20USB-7D4698)
![local](https://img.shields.io/badge/your%20data%20leaving%20the%20machine-0%20(except%20Tor%20searches)-black)
![uncensored](https://img.shields.io/badge/uncensored-yes-9146F0)
![speed](https://img.shields.io/benchmark-up%20to%20166%20tok%2Fs%20on%20a%20gaming%20GPU-59316C)

<br clear="left">

**No cloud. No account. No telemetry. No refusals.**

A 27-billion-parameter AI that runs on a gaming GPU in your home — it can even read
images. When it needs fresh information from the web, it searches through the Tor
network, so nobody — not your ISP, not the search engine — can see what it looked up.
Your questions, the AI's reasoning, its answers: they never leave your computer.

The name is the shape of the thing: onion-routed on the outside, a mind that stays
local at the centre.

---

## ⚡ Get it in 60 seconds

### Windows

1. Open **[install-onionmind.ps1](install-onionmind.ps1)**, click *Raw*, copy everything.
2. Paste into **PowerShell** and press Enter.
3. Wait for the download (10–16GB, resumes if interrupted) — then **double-click the
   Onionmind icon on your desktop**. That's it.

The installer figures out your GPU and installs the right model automatically.
No decisions required.

### Linux

```bash
bash install-onionmind.sh
```

(Arch or Ubuntu/Debian. Same idea — one paste, it picks the right model, you get an
`onionmind` command and a desktop icon.)

### Using it

```
onionmind                          ← chat. It searches the web by itself when it needs to
onionmind "who won the last election?"
```

Or just double-click the icon. Type `/save notes.txt` to export a conversation,
`/save` + print if you want it on paper.

---

## 🔥 Matchstick — the USB stick that forgets

A **whole private operating system on a USB stick**. Boot *any* PC from it — use that
machine's GPU and memory, touch nothing on its disk — and when you power off,
everything you did ceases to exist. Like Tails, but it runs a 27B AI.

**Strike it anywhere. Burns bright. Leaves only ash.**

1. **Build the stick** (one command, needs [Docker](https://docker.com), ~1 hour — [full guide](usb/README.md)):
   ```bash
   docker build -f usb/Dockerfile -t onionmind-usb .
   docker run --rm --privileged -v "$(pwd)/usb/cache:/onionmind/usb/cache" \
     -v "$(pwd)/usb/out:/onionmind/usb/out" onionmind-usb 12gb
   ```
2. **Burn** `usb/out/onionmind-matchstick-12gb-amd64.iso` to a USB stick with
   [Rufus](https://rufus.ie) or Etcher.
3. **Boot any PC** from it (Secure Boot off). You land straight in the chat. Type
   `sudo onionmind-status` to watch every protection verify itself live.

What's inside the stick: the AI and its weights baked in, a firewall where **Tor is
the only process allowed to leave the machine**, a fresh fake MAC address every boot,
the clock synced over Tor, RAM scrubbed on shutdown. Nothing installs. Nothing
persists. Nothing to find.

---

## 📦 Pick your version

**Onionmind (installed)** picks its own model to fit your GPU — you don't choose:

| Your GPU | What you get |
|---|---|
| 12GB+ (RTX 3080/4070 Ti class) | **The full 27B with vision** — reads images too |
| 8GB+ | The full 27B, squeezed |
| 6GB+ | 9B — sharp, and *faster* than the big one |
| Anything / no GPU | 4B — still uncensored, still searches Tor, runs on a potato |

**Matchstick editions** — you pick when building the stick:

| Edition | Tagline | Runs great on | Stick size |
|---|---|---|---|
| `4b` | the pocket rocket | any PC, GPU optional | 16GB |
| `9b` | the daily driver | 6GB+ GPU | 16GB |
| `8gb` | the big brain, squeezed | 8GB GPU | 32GB |
| `12gb` 💜 | **the flagship** — 27B + vision | 12GB GPU (RTX 3080 Ti class) | 32GB |
| `17gb` | the flagship, full fat | 17GB+ GPU (RTX 4090 class) | 32GB |

---

## 📊 The numbers (measured, not promised)

On a regular RTX 3080 Ti gaming rig:

| | Speed |
|---|---|
| 4B model | **166 tokens/second** |
| 9B model | **116 tokens/second** |
| Full 27B | **~19 tokens/second** (40+ when the GPU is idle) |

Every figure in this repo was measured on real hardware — including the honest ones.
That's the culture: [what has been verified](TECHNICAL.md#what-has-been-verified).

---

## 🔬 Under the hood — the 60-second version

For the engineers (the long version: **[TECHNICAL.md](TECHNICAL.md)**):

- **Searches can't leak.** Each web search builds a *fresh Tor circuit*, resolves DNS
  through Tor (`socks5h`), hits DuckDuckGo's onion endpoint so no exit node ever sees
  the query — and the tool **refuses to search at all** if the circuit doesn't verify.
- **The model is baked, not downloaded.** On Matchstick, weights are deduplicated into
  the image at build time and served from a read-only store — boot does zero network.
- **The firewall is structural.** An nftables ruleset loads *before* networking exists:
  only the tor user, DHCP and loopback can leave. Even root can't open a clearnet
  socket. DNS is blocked outright.
- **The driver can't rot.** The NVIDIA module is compiled against the exact kernel on
  the stick at build time — and the stick's kernel never updates. The classic
  driver-vs-kernel breakage is structurally impossible.
- **Everything is tested.** Seven validation suites (`usb/tests/`) run the whole stack
  in containers — from Tor bootstrap to model baking to firewall rule ordering.

---

## 🤝 The honest small print

Things we won't lie to you about (full threat model in
[TECHNICAL.md](TECHNICAL.md#going-further-on-privacy)):

- A government watching both ends of the network can still correlate traffic. That's
  a Tor limit, not a product feature — Tails has it too.
- What you *search for* and how you *write* can identify you regardless of any
  technology.
- No GPU? It still runs — on the CPU. Slower: fine for the small models, painful for
  the 27B.
- Matchstick needs Secure Boot off to boot (the NVIDIA driver is unsigned), and it
  supports NVIDIA GTX 900 → RTX 40 and modern AMD cards for GPU speed; RTX 50 falls
  back to CPU until Debian ships a newer driver.

---

<div align="center">

**Onionmind** — onion-routed on the outside, a mind that stays local at the centre.

[Quick start](#-get-it-in-60-seconds) · [Matchstick](usb/README.md) · [TECHNICAL.md](TECHNICAL.md)

</div>
