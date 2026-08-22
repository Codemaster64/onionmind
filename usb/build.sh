#!/usr/bin/env bash
# Builds "Onionmind Matchstick" - the live USB: Tails-style amnesia, host-machine GPU.
#
#   sudo ./usb/build.sh 12gb                  # Debian trixie+ with live-build
#
#   docker build -f usb/Dockerfile -t onionmind-usb .
#   docker run --rm --privileged \
#     -v "$(pwd)/usb/cache:/onionmind/usb/cache" \
#     -v "$(pwd)/usb/out:/onionmind/usb/out" \
#     onionmind-usb 12gb
#
# Tier = the GPU class of the machines you'll boot it on (weights are baked in;
# the booted machine's VRAM decides speed, the stick's contents are fixed).
# Edition names or size codes both work - "flagship" and "12gb" are the same stick:
#
#   max|17gb       16.0GB  Qwen3.8-27B Q4_K_M   + vision   -> 32GB+ stick
#   flagship|12gb  11.7GB  Qwen3.8-27B 3.69bpw  + vision   -> 32GB+ stick
#   standard|8gb    9.5GB  Qwen3.8-27B IQ2_M    + vision   -> 32GB+ stick
#   daily|9b       5.2GB  Qwen3.5-9B            no vision -> 16GB+ stick
#   pocket|4b      2.5GB  Qwen3.5-4B            no vision -> 16GB+ stick
#   mobile|lfm     1.7GB  LFM2.5-2.6B           no vision -> 8GB+ stick
#
# env overrides: OLLAMA_URL (pin an ollama release), NUM_GPU (default 99),
#   ROCM=0 (drop AMD/ROCm support, saves ~1GB),
#   ONIONMIND_BRIDGES - obfs4 bridge line(s), semicolon- or newline-separated,
#   baked into torrc so the stick hides that it uses Tor at all:
#   ONIONMIND_BRIDGES="obfs4 1.2.3.4:9130 cert=abc iat-mode=0" ./usb/build.sh 12gb
set -euo pipefail

say()  { printf '\033[36m==> %s\033[0m\n' "$*"; }
die()  { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

# live-build chroots and mounts - it needs root. In Docker we already are root.
if [ "$(id -u)" -ne 0 ]; then
  say "re-running as root (live-build needs it)"
  exec sudo -E "$0" "$@"
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CACHE="$ROOT/usb/cache"; OUT="$ROOT/usb/out"; WORK="$ROOT/usb/build"
OLLAMA_URL="${OLLAMA_URL:-https://ollama.com/download/ollama-linux-amd64.tar.zst}"
# AMD compute. The base tarball is CUDA-only (16 cuda libs, 0 rocm), so without
# this an AMD card falls back to CPU. Costs ~1GB of image; ROCM=0 to drop it.
ROCM="${ROCM:-1}"
ROCM_URL="${ROCM_URL:-https://ollama.com/download/ollama-linux-amd64-rocm.tar.zst}"
NUM_GPU="${NUM_GPU:-99}"
mkdir -p "$CACHE" "$OUT"

# --- 1. tier ----------------------------------------------------------------
# Same build table as install-onionmind.sh, minus `auto` - the build machine
# can't see the target's VRAM, so you pick for the machines you'll boot.
TIER="${1:-flagship}"
VISION_FILE=Qwen3.8-27B-Uncensored-vision-f16.gguf
case "$TIER" in
  max|17gb) TIER=17gb; REPO=hotdogs/Qwen3.8-27B-abliterated-MTP-GGUF
        FILE=Qwen3.8-27B-abliterated-mtp-Q4_K_M.gguf
        MODEL_NAME=inferno; VISION=1 ;;
  flagship|12gb) TIER=12gb; REPO=soyaakinohara/qwen3.8-27b-abliterated-3.69bpw-12GB-MTP.gguf
        FILE=qwen3.8-27b-abliterated-3.69bpw-12GB-MTP.gguf
        MODEL_NAME=inferno; VISION=1 ;;
  standard|8gb) TIER=8gb; REPO=hotdogs/Qwen3.8-27B-abliterated-MTP-GGUF
        FILE=Qwen3.8-27B-abliterated-IQ2_M.gguf
        MODEL_NAME=inferno; VISION=1 ;;
  daily|9b) TIER=9b; REPO=mradermacher/Huihui-Qwen3.5-9B-abliterated-GGUF
        FILE=Huihui-Qwen3.5-9B-abliterated.Q4_K_M.gguf
        MODEL_NAME=ember; VISION=0 ;;
  pocket|4b) TIER=4b; REPO=mradermacher/Huihui-Qwen3.5-4B-abliterated-GGUF
        FILE=Huihui-Qwen3.5-4B-abliterated.Q4_K_M.gguf
        MODEL_NAME=spark; VISION=0 ;;
  mobile|lfm) TIER=lfm; REPO=Abiray/LFM2.5-2.6B-Heretic-Abliterated-GGUF
        FILE=LFM2.5-2.6B-heretic-Q4_K_M.gguf
        MODEL_NAME=lfm-2.6b; VISION=0 ;;
  *) die "tier must be max/17gb, flagship/12gb, standard/8gb, daily/9b, pocket/4b or mobile/lfm (got '$TIER')" ;;
esac
say "Tier $TIER: $FILE (vision: $([ "$VISION" = 1 ] && echo yes || echo no))"

# --- 2. artifacts (cached, resumable) ----------------------------------------
fetch() {  # url dest
  [ -s "$2" ] && { say "cached: $(basename "$2")"; return; }
  say "downloading $(basename "$2") (resumable)"
  curl -L -C - --fail --noproxy '*' -o "$2" "$1"
}
fetch "$OLLAMA_URL"                                              "$CACHE/ollama-linux-amd64.tar.zst"
[ "$ROCM" = 1 ] && fetch "$ROCM_URL"                             "$CACHE/ollama-linux-amd64-rocm.tar.zst"
fetch "https://huggingface.co/$REPO/resolve/main/$FILE"          "$CACHE/$FILE"
[ "$VISION" = 1 ] && fetch "https://huggingface.co/JonathanColetti/Qwen3.8-27B-Uncensored-GGUF/resolve/main/$VISION_FILE" "$CACHE/$VISION_FILE"

# --- 3. live-build scaffolding -----------------------------------------------
say "configuring live-build (debian trixie, non-free for nvidia)"
rm -rf "$WORK"; mkdir -p "$WORK"; cd "$WORK"
cp -r "$ROOT/usb/config" config

lb config \
  --architecture amd64 \
  --distribution trixie \
  --archive-areas "main contrib non-free non-free-firmware" \
  --debian-installer none \
  --memtest none \
  --chroot-squashfs-compression-type zstd \
  --firmware-chroot true \
  --iso-application "Onionmind Matchstick" \
  --iso-volume "MATCHSTICK" \
  --bootappend-live "boot=live components hostname=onionmind username=onion"

# --- 4. payloads + generated files into the chroot ---------------------------
INC=config/includes.chroot
say "staging payloads"
mkdir -p "$INC/usr/lib/onionmind/payload" "$INC/usr/lib/onionmind/weights"
cp "$CACHE/ollama-linux-amd64.tar.zst" "$INC/usr/lib/onionmind/payload/"
[ "$ROCM" = 1 ] && cp "$CACHE/ollama-linux-amd64-rocm.tar.zst" "$INC/usr/lib/onionmind/payload/"
cp "$CACHE/$FILE"                       "$INC/usr/lib/onionmind/weights/"
[ "$VISION" = 1 ] && cp "$CACHE/$VISION_FILE" "$INC/usr/lib/onionmind/weights/"

mkdir -p "$INC/usr/local/lib/onionmind"
cp "$ROOT/onionmind.py" "$INC/usr/local/lib/onionmind/onionmind.py"
sed -i "s|^MODEL  = .*|MODEL  = \"$MODEL_NAME\"|" "$INC/usr/local/lib/onionmind/onionmind.py"

cat > "$INC/usr/local/lib/onionmind/env" <<ENV
MODEL_NAME="$MODEL_NAME"
TIER="$TIER"
VISION=$VISION
ENV

# Same Modelfile the installer writes; num_gpu is a build-time choice because
# the stick is read-only once burned.
modelfile() {  # $1 = extra FROM line for the vision projector, "" for none
  cat <<MF
FROM /usr/lib/onionmind/weights/$FILE
$1
PARAMETER num_gpu $NUM_GPU
PARAMETER num_ctx 8192
PARAMETER temperature 0.7
PARAMETER stop "<|im_start|>"
PARAMETER stop "<|im_end|>"
TEMPLATE """{{ if .System }}<|im_start|>system
{{ .System }}<|im_end|>
{{ end }}{{ if .Prompt }}<|im_start|>user
{{ .Prompt }}<|im_end|>
{{ end }}<|im_start|>assistant
{{ .Response }}<|im_end|>
"""
MF
}
modelfile ""       > "$INC/usr/local/lib/onionmind/Modelfile"
[ "$VISION" = 1 ] && modelfile "FROM /usr/lib/onionmind/weights/$VISION_FILE" \
                      > "$INC/usr/local/lib/onionmind/Modelfile.vision"

# Bridges baked at build time hide "this machine uses Tor" from the local
# network entirely. obfs4 only - the transport TPB hands out by default;
# snowflake/webtunnel need different ClientTransportPlugin lines.
if [ -n "${ONIONMIND_BRIDGES:-}" ]; then
  say "baking obfs4 bridges into torrc"
  {
    echo ""
    echo "# Baked at build time via ONIONMIND_BRIDGES."
    echo "ClientTransportPlugin obfs4 exec /usr/bin/obfs4proxy"
    echo "UseBridges 1"
    printf '%s\n' "$ONIONMIND_BRIDGES" | tr ';' '\n' | sed -e 's/^[Bb]ridge[[:space:]]*/Bridge /' -e t -e 's/^/Bridge /'
  } >> "$INC/etc/tor/torrc.d/onionmind.conf"
fi

chmod +x config/hooks/normal/*.hook.chroot config/hooks/live/*.hook.binary
chmod +x "$INC/usr/local/bin/"* "$INC/usr/local/sbin/"* "$INC/usr/local/lib/onionmind/"*.sh
chmod 644 "$INC"/etc/systemd/system/*.service "$INC"/etc/systemd/system/getty@tty1.service.d/autologin.conf
chmod 440 "$INC/etc/sudoers.d/onion"

# --- 5. build -----------------------------------------------------------------
say "building the image (the squashfs over ~10GB of weights is the slow part - expect an hour+)"
lb build
ISO="$OUT/onionmind-matchstick-${TIER}-amd64.iso"
mv live-image-amd64.hybrid.iso "$ISO"
(cd "$OUT" && sha256sum "$(basename "$ISO")" > "$(basename "$ISO").sha256")

say "done"
du -h "$ISO"
echo "  burn:    Rufus/Etcher, or:  dd if=$ISO of=/dev/sdX bs=4M status=progress oflag=direct"
echo "  boot:    machine firmware set to boot USB, Secure Boot OFF (unsigned NVIDIA module)"
echo "  verify:  sha256sum -c $(basename "$ISO").sha256"
