# Onionmind Matchstick — Tails' amnesia, your model, the host machine's GPU

A bootable USB that runs the whole Onionmind stack from the stick: boot any
machine off it, use **that machine's GPU/CPU/RAM** for inference, touch
**nothing on its internal disk**, and forget everything at power-off. It is
built with Debian `live-build`, not by modifying Tails — Tails itself cannot
run this (no NVIDIA driver, no CUDA, and its amnesia would mean re-downloading
16GB of weights over Tor every boot). This is the same *mechanics* — live
boot, RAM-only overlay, network forced through Tor — with the model added.

The name is the shape of the thing, like Onionmind's is: strike a matchstick
on any machine, it burns bright on that machine's GPU, and when it's done
there's nothing but ash — the session leaves no residue.

## What the stick enforces

- **Amnesia.** The system runs from the USB into a RAM overlay; there is no
  persistence partition, swap is force-disabled, and the internal disk is
  never mounted. Power off → your questions, the answers, the shell history
  do not exist.
- **Fail-closed networking, structurally.** An `nftables` ruleset loads before
  the network comes up: the only things that can leave the machine are tor
  (the `debian-tor` user), DHCP, and loopback. Not even root can open a
  clearnet socket, and DNS is blocked outright — tor doesn't need it
  (`socks5h` resolves remotely; directory authority IPs are hard-coded). A
  compromised or misbehaving process has nowhere to leak to. This is the
  Whonix-gateway idea expressed in a firewall.
- **Fresh MAC per boot, verified.** Every interface gets a random MAC, and a
  boot check verifies it actually happened — if an interface comes up with
  its burned-in MAC, the console says so loudly (the LAN could link sessions
  to the device). Tails blocks networking in that case; this stick warns,
  because bricking connectivity on odd hardware helps no one.
- **Free RAM scrubbed at clean shutdown.** A shutdown unit overwrites all
  free memory with zeros, so freed prompt text and model context stop being
  cold-boot recoverable. Best-effort, not Tails-grade — see *What it does
  not fix*.
- **Nothing clearnet at runtime.** The NVIDIA driver, Ollama, the weights and
  the vision projector are all baked into the image. The only network
  activity, ever, is tor building circuits and the searches themselves.
- **Searches stay as they are.** Same `.onion` DuckDuckGo, same fresh circuit
  per query, same fail-closed check — `onionmind.py` is unchanged.

## What it does not fix

Same list as the main README, plus the live-USB-specific ones:

- A global passive adversary watching both ends still wins. Tor's limits are
  not improved by booting from a stick.
- Query content and stylometry identify you regardless of circuit.
- **Secure Boot must be OFF** to boot it (unsigned NVIDIA module). On a
  BitLocker machine, toggling Secure Boot can trigger a recovery-key prompt
  the next time Windows boots — have that key handy, or use a machine that
  isn't yours-with-BitLocker.
- Cold-boot attacks: RAM contents persist briefly after power-off. A clean
  shutdown scrubs *free* RAM (see *What the stick enforces*), but
  kernel-resident pages survive and a power-cut runs nothing — pull the plug
  and walk away only if the threat doesn't include someone grabbing the
  machine within ~minutes.
- The stick itself is not encrypted (the weights are public; your *use* of
  it lives in RAM). Don't save anything to a second partition and expect
  amnesia.
- Firmware/IME/the BIOS can observe everything, same as on any OS.

## Tails, as parts — not as a base

Tails itself is built on a fork of live-build 2.x plus their own Vagrant +
KVM toolchain, and it is shaped around Tor Browser on the desktop. Basing
this image on the Tails repo would mean marrying that build system (KVM-only,
no Docker), rebasing against an actively-moving project to keep security
updates, and dragging its desktop assumptions along with 16GB of weights.
Derivative builders generally don't; Tails positions its *mechanisms* as the
reusable part. So that's what this kit takes — each re-implemented natively
(systemd, nftables, plain sh), not copied, so there's no GPL entanglement in
this repo and no dependency on their codebase:

- **Fail-closed, tor-only networking** — Tails' core design, here as an
  nftables ruleset where `debian-tor` is the only user allowed off the box.
- **Clock over Tor** — Tails' htpdate pattern (`onionmind-clock.service`):
  once Tor has a circuit, read a `Date` header through it and step the clock.
  A live box has no RTC it can trust; Windows dual-boot machines carry
  local-time clocks that break Tor's consensus validation.
- **MAC randomization per boot** — systemd `MACAddressPolicy=random`, the
  same goal as Tails' spoofing: the LAN cannot link two sessions.
- **Kernel/network sysctl hardening** — their playbook of redirects/source-
  routing/dmesg knobs, as a sysctl drop-in.
- **Amnesia mechanics** — RAM-only overlay, no persistence, swap off. Same
  shape as Tails, one tenth the machinery.

Deliberately **not** taken:

- **Transparent torification of all traffic.** Tails redirects every
  connection through Tor because it's a general-purpose desktop. This stick
  is an appliance: deny-by-default is safer than silently proxying apps that
  were never designed to be careful about what they send.
- **Shutdown RAM wipe — partial.** Clean shutdown runs a best-effort scrub
  of *free* memory (a tmpfs fill of zeros); kernel-resident pages survive it,
  and a power-cut or pulled battery runs nothing at all. Tails' full answer
  is kexec into a dedicated wipe environment — not ported here without
  hardware to test on. If seizure-within-minutes is in your threat model,
  treat powered-off RAM as readable.
- **Tor Browser.** Console-only; the search agent already blends into the
  Tor Browser UA herd. Boots are faster and the image smaller without it.
- **Persistent storage** — the encrypted volume Tails offers is the opposite
  of what this stick is for.

If you want the details straight from the source, Tails' design docs on
*Tor enforcement* and *time syncing* describe the reasoning behind each of
these far better than this README can.

## Requirements

**To build** (once, on your own machine): Docker, ~40GB free, an hour+ for
the squashfs. Downloads are cached in `usb/cache/` and resume if dropped.

**The stick**: USB 3, 16GB for the 9b/4b tiers, 32GB for the 27B tiers.
Speed matters — model load streams from the stick.

**The machines you boot**: x86-64, 16GB+ RAM recommended for the 27B tiers,
Secure Boot off, NVIDIA GPU from GTX 900 (Maxwell) through RTX 40 for GPU
speed (trixie's driver is 550; RTX 50 needs a backported driver — see
Troubleshooting). Anything else runs on CPU: fine for 9b/4b, slow for the 27B.

## Build

From the repo root:

```bash
docker build -f usb/Dockerfile -t onionmind-usb .
docker run --rm --privileged \
  -v "$(pwd)/usb/cache:/onionmind/usb/cache" \
  -v "$(pwd)/usb/out:/onionmind/usb/out" \
  onionmind-usb 12gb
```

`--privileged` is required: live-build chroots and bind-mounts `/proc`,
`/sys`, `/dev` inside the image it assembles. On a Debian trixie+ box you can
skip Docker: `sudo apt install live-build && sudo ./usb/build.sh 12gb`.

The tier argument is the GPU class of the machines you'll boot it on — the
build machine's hardware is irrelevant. `auto` doesn't exist here because the
stick can't see the target's VRAM; you decide once:

| Tier | Weights | Model | Vision | Stick |
|---|---|---|---|---|
| `17gb` | 16.0 GB | Qwen3.8-27B `Q4_K_M` | yes | 32GB |
| `12gb` | 11.7 GB | Qwen3.8-27B `3.69bpw` | yes | 32GB |
| `8gb` | 9.5 GB | Qwen3.8-27B `IQ2_M` | yes | 32GB |
| `9b` | 5.2 GB | Qwen3.5-9B | no | 16GB |
| `4b` | 2.5 GB | Qwen3.5-4B | no | 16GB |

Output lands in `usb/out/onionmind-matchstick-<tier>-amd64.iso` with a `.sha256`.

Pin a specific ollama with `OLLAMA_URL=<release-tarball-url>`; change GPU
offload with `NUM_GPU=56` (same trade-offs as the main README).

To bake **bridges** in at build time so the stick never shows plain Tor
traffic to the local network (obfs4 lines from
https://bridges.torproject.org, semicolon- or newline-separated):

```bash
ONIONMIND_BRIDGES="obfs4 1.2.3.4:9130 cert=abc iat-mode=0" \
  docker run --rm --privileged ... onionmind-usb 12gb
```

## Burn

Rufus or Etcher on Windows, or `dd if=...iso of=/dev/sdX bs=4M status=progress
oflag=direct` on Linux. Ventoy is untested — use a plain dd-style burn.

## Boot

1. Plug in, power on, pick the USB in the firmware boot menu. Secure Boot
   **off** — a signed-image refusal at boot means it's still on. The boot
   menu has ready-made entries: the default amnesic boot, **print mode**
   (network-printer firewall exception), and **debug** (firewall open) — no
   hand-editing kernel cmdlines.
2. The console autologs in and drops you into the chat on tty1. First boot
   takes a minute: DKMS-checked driver, tor circuit, model server.
3. Ethernet usually just works. Wi-Fi: switch to a shell with **alt-F2**,
   `sudo nmtui`, connect, switch back (alt-F1).
4. **`sudo onionmind-status`** — one glance at everything protecting the
   session: firewall sealed, tor circuit verified, MACs randomized, model
   loaded, no swap, clock. Run it after boot and believe your eyes, not the
   docs.
5. Ask things. `[tor] active, exit <ip>` under your first search means the
   whole chain is up. No circuit → the tool refuses to search, by design.
   `/save <file>` exports the conversation.
6. Power off when done. That's the whole privacy mechanism: nothing
   persisted, so nothing to find. In a hurry: **`sudo onionmind-panic`**
   scrubs free RAM and cuts power. Laptops: closing the lid does nothing,
   deliberately — a suspended machine is a frozen RAM image.

### Hiding that you use Tor at all

Plain tor is visible to the local network as tor. To hide it, either bake
bridges in at build time (`ONIONMIND_BRIDGES=...`, see Build) or add them at
the console (alt-F2):

```bash
sudo onionmind-bridges "obfs4 1.2.3.4:9130 cert=abc iat-mode=0"
```

`obfs4proxy` is already on the stick; console-added bridges live in RAM and
vanish at power-off like everything else. Get bridge lines from
https://bridges.torproject.org, requested from a different machine.

### Printing and editing

Two editors are on the stick: **micro** (the friendly one — Ctrl-S saves,
Ctrl-Q quits, menus explain themselves) and **nano**. `gpm` gives the raw
console a mouse: select with the left button, middle-button pastes into the
editor. The usual flow: type **`/save notes.txt`** in the chat to export the
conversation, then `sudo onionmind-print notes.txt`. (Mouse-select + paste
into `micro` works too.)

Printing is **opt-in by design** — it is the one thing that deliberately
punches through the amnesia, so it happens on your terms:

- **USB printer** — plug in the cable and `sudo onionmind-print notes.txt`.
  The network stays fully sealed; nothing about the print touches tor or the
  LAN. This is the recommended path.
- **Network printer** — reboot with `onionmind.print` on the kernel cmdline
  (Tab at the boot menu on BIOS, `e` on UEFI). That loads a narrow firewall
  exception: mDNS, IPP (631) and SNMP to the local network only — still no
  DNS, no clearnet, tor untouched. Then the same `sudo onionmind-print`.

Most printers from the last decade are driverless (AirPrint/IPP Everywhere)
and get picked up automatically; older ones fall back to the bundled foomatic
PPDs. Truly ancient printers may need a rebuild with extra `printer-driver-*`
packages.

**The privacy cost of paper, said plainly:** everything on this stick is
amnesic *except a printout*. The sheet is physical evidence that outlives
power-off. Many printers keep job logs — and sometimes full job images — in
their own nonvolatile storage. Color laser printers embed microscopic yellow
tracking dots encoding serial number and timestamp. Printing something is a
publishing decision; make it the last act of the session, then power off.

### Debugging the firewall

Everything outbound except tor/DHCP/loopback is dropped, always. If something
legitimate needs out during debugging, boot the **debug** entry from the boot
menu (firewall open, that session only) — or append `onionmind.openfw` to the
kernel cmdline by hand (Tab on BIOS, `e` on UEFI). Don't daily-drive it open.
(`onionmind.print` is the narrow, sanctioned exception: mDNS/IPP/SNMP to the
LAN for printers — see *Printing and editing*.)

## The NVIDIA question

NVIDIA-on-Linux has a reputation, and it's earned — but almost all of it is
about the failure modes this design can't have:

- **Kernel/driver mismatch** (the black-screen classic) is manufactured by
  kernel updates. The stick's kernel never updates: the DKMS module is
  compiled against exactly the image's kernel at build time and neither half
  can move. Verified during the first full build — the 550 stack and its
  module build cleanly in the chroot.
- **The `.run` installer** is where most horror stories live; it fights the
  package manager and breaks on every kernel bump. The stick uses Debian's
  packaged DKMS driver — the boring, supported route.
- **The real limit is coverage, not stability**: driver 550 supports Maxwell
  (GTX 900) through RTX 40. Pre-Maxwell, AMD, Intel and RTX 50 GPUs get no
  CUDA — ollama falls back to CPU. The console, tor and search are unaffected;
  a slow stick beats a dead one. (Fine on 4b/9b, painful on the 27B.)
- Checked August 2026: trixie-backports carries the **same 550 branch**
  (`550.163.01-4~bpo13+1`), so there is no newer-driver path on this base
  yet. When Debian ships one, pinning it in `build.sh` is a few lines; until
  then RTX 50 means CPU.
- **Nothing downloads at boot** — that's the anonymity property. NVIDIA
  userspace, module, GPU firmware, Wi-Fi firmware and CPU microcode are all
  baked in (the build log shows the firmware set installing alongside the
  driver).

## Troubleshooting

**Won't boot / firmware refuses the stick** — Secure Boot is on. Turn it off
(and see the BitLocker note above).

**`nvidia-smi: command not found` or no GPU line at the console banner** —
the GPU isn't one driver 550 knows (RTX 50-series, pre-Maxwell, AMD, Intel).
Inference falls back to CPU: fine on 9b/4b, ~1–2 tok/s on the 27B. See
*The NVIDIA question* — trixie-backports has no newer branch yet, so RTX 50
is CPU-only on this base for now.

**tor never reaches 10%** — clock. `onionmind-clock.service` fixes the clock
automatically once Tor has a circuit (Tails' htpdate pattern; watch
`journalctl -u onionmind-clock -f`). The remaining corner is the one Tails'
design doc also has: if the clock is *so* wrong that Tor cannot bootstrap at
all, nothing over Tor can rescue it — set it by hand, roughly right, UTC:
`sudo timedatectl set-time '2026-08-21 14:00'`, then
`sudo systemctl restart tor@default`.

**Searches fail with "no results; exit node may be rate-limited"** — same as
the installed setup: retry for a fresh circuit.

**The network shows "limited connectivity" / no internet** — that is the
firewall correctly refusing NetworkManager's clearnet connectivity *check*.
Ethernet, DHCP, tor and search all work regardless; the check is a clearnet
HTTP probe and clearnet does not exist here.

**Model takes a while to answer the first question** — first token streams
the weights off the stick into VRAM. Subsequent tokens are GPU-speed; the
README's speed table for your tier is what to expect.

## How it works (the parts worth knowing)

**Weights are baked, not downloaded.** `ollama create` runs inside the
live-build chroot at *build* time, so the blob store ships inside the
squashfs. The booted machine never registers, hashes or copies weights — a
runtime copy would land in tmpfs and eat the RAM you need for the KV cache.

**Ollama's create doubles the weights; the build garbage-collects.** Measured
with a 484MB GGUF: `create` stores the raw blob *and* a converted layer — the
same bytes twice. The bake hook walks the manifests, keeps every referenced
digest and deletes the rest (`usb/tests/validate-ollama-bake.sh` proves
inference works from the collected store). That's the difference between a
12GB tier fitting a 32GB stick and not.

**Serving from a read-only filesystem.** The squashfs is read-only but ollama
wants a writable models root, so `onionmind-prepare-models.service` builds one
in `/run` at boot: manifests (a few KB) copied, blobs (the actual weights)
symlinked. Serving from that store is verified in the same test.

**Firewall ordering.** The ruleset loads `Before=network-pre.target`, so no
packet beats it. `swapoff` runs in the same unit — even a host swap partition
can't catch prompt text.

## What has been verified

| | Status |
|---|---|
| `lb config` accepts every flag used by `build.sh` (zstd squashfs included) | ✅ debian:trixie container |
| All package names resolve on trixie (nvidia-driver 550, tor 0.4.9, …) | ✅ apt policy |
| Ollama tarball layout (`bin/` + `lib/`, `.tar.zst` — the old `.tgz` URL now 404s) | ✅ real download |
| `ollama create` works headless in a chroot-like container | ✅ |
| Manifest-GC halves the store; inference still works | ✅ |
| Serving from a read-only store via symlinked `/run` root | ✅ |
| Clock-over-Tor script: real circuit, Date fetch/parse/decide | ✅ dry-run (`validate-clock.sh`) |
| MAC check (pass + burned-in-warn), RAM scrub, bridge-line transform | ✅ `validate-fixes.sh` |
| Print/editor packages resolve; cupsd runs; nft main+print rules load in order | ✅ `validate-print.sh` |
| `onionmind-status` reports every branch honestly on a degraded box | ✅ `validate-status.sh` |
| All of the above, on every push | CI: `.github/workflows/usb-tests.yml` |
| **Full `lb build` produces an ISO** | ✅ 4b (5.5GB) and 12gb (16GB) tiers — DKMS NVIDIA module compiled in-chroot, hooks ran, single-copy stores confirmed inside both artifacts |
| 12gb vision bake | ✅ both manifests (`qwen38-uncensored`, `-vision`) in the image; blobs deduplicated to one 11.7GB base + one 885MB projector shared by both models |
| Boot-menu entries in the final ISO | ✅ syslinux + grub print/debug entries verified in both shipped images (after fixing the hook's CWD assumption in build #1) |
| **Boots on real hardware; driver loads; firewall order holds** | ❌ first boot is yours |

The two validation scripts are in `usb/tests/` and re-runnable via the docker
commands in their headers.
