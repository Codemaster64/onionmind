#!/usr/bin/env bash
# Matchstick, the easy way: run this, answer two questions, plug a stick.
# Downloads a pre-built stick (no Docker) or builds any edition with Docker,
# then raw-writes it to a USB stick.
#   ./matchstick.sh [pocket|daily|standard|flagship|max|4b|9b|8gb|12gb|17gb]
set -euo pipefail
say()  { printf '\033[36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33m    %s\033[0m\n' "$*"; }
die()  { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

REPO="$(cd "$(dirname "$0")" && pwd)"
DRY="${MATCHSTICK_DRY:-0}"
PREBUILT_BASE="https://github.com/Codemaster64/onionmind/releases/download/matchstick-pocket"

edition_menu() {
  cat <<'MENU'

   1) pocket     - 4B,  runs on anything            (16GB stick)
   2) daily      - 9B,  fast small-GPU daily driver  (16GB stick)
   3) standard   - 27B squeezed, 8GB GPUs            (32GB stick)
   4) flagship   - full 27B + vision, 12GB GPUs      (32GB stick)  <- recommended
   5) max        - full fat 27B, 17GB+ GPUs          (32GB stick)

MENU
  printf '   Which edition? [1-5, Enter = 4] '
  read -r pick
  case "${pick:-4}" in
    1|pocket)   echo 4b ;;
    2|daily)    echo 9b ;;
    3|standard) echo 8gb ;;
    4|flagship) echo 12gb ;;
    5|max)      echo 17gb ;;
    4b|9b|8gb|12gb|17gb) echo "$pick" ;;
    *) echo 12gb ;;
  esac
}

download_prebuilt() {
  local iso="$REPO/usb/out/onionmind-matchstick-4b-amd64.iso"
  [ -f "$iso" ] && { say "ISO already present: $iso"; return 0; }
  if [ "$DRY" = 1 ]; then say "DRY: would download 4 parts + SHA256SUMS, rejoin into $iso"; exit 0; fi
  say "Downloading pre-built pocket stick (4 parts, ~6.6GB)"
  mkdir -p "$REPO/usb/out/parts"
  # gh first: it authenticates, which private repos require. Plain curl works
  # when the repo is public. Rerun resumes: gh skips files it already fetched.
  if command -v gh >/dev/null 2>&1; then
    gh release download matchstick-pocket --repo Codemaster64/onionmind \
      --dir "$REPO/usb/out/parts" --clobber \
      --pattern 'onionmind-matchstick-4b-amd64.iso.part*' --pattern 'SHA256SUMS' \
      || die "gh download failed - check: gh auth status"
  else
    for p in part00 part01 part02 part03 SHA256SUMS; do
      f="onionmind-matchstick-4b-amd64.iso.$p"; [ "$p" = SHA256SUMS ] && f=SHA256SUMS
      curl -L -C - --fail -o "$REPO/usb/out/parts/$f" "$PREBUILT_BASE/$f" \
        || die "download failed at $f - needs the repo public, or install gh and auth it"
    done
  fi
  say "Rejoining parts"
  cat "$REPO"/usb/out/parts/onionmind-matchstick-4b-amd64.iso.part?? > "$iso"
  say "Verifying checksum"
  want="$(grep -vE 'part' "$REPO/usb/out/parts/SHA256SUMS" | awk '{print $1}')"
  got="$(sha256sum "$iso" | awk '{print $1}')"
  [ "$got" = "$want" ] || die "checksum mismatch (want $want got $got) - delete parts and retry"
  say "Checksum OK"
}

printf '\n   [D] DOWNLOAD: ready-made pocket/spark with Qwen3.5 4B, ~6.6GB total\n'
printf '       Fastest and simplest; runs on almost any PC\n'
printf '   [B] BUILD: choose Qwen3.5 4B, 9B, or 27B (+ vision), ~1 hour\n'
printf '       Needs Docker; for larger or vision-capable models\n\n'
printf '   Download or build? [D/B, Enter = D] '
read -r MODE
if [ "$(echo "${MODE:-D}" | tr '[:lower:]' '[:upper:]')" != "B" ]; then
  download_prebuilt
  EDITION=4b
else
  EDITION="${1:-$(edition_menu)}"
fi

case "$EDITION" in
  4b|pocket)    EDITION=4b;   NEED=16 ;;
  9b|daily)     EDITION=9b;   NEED=16 ;;
  8gb|standard) EDITION=8gb;  NEED=32 ;;
  12gb|flagship) EDITION=12gb; NEED=32 ;;
  17gb|max)     EDITION=17gb; NEED=32 ;;
  *) die "unknown edition: $EDITION" ;;
esac
say "Edition: $EDITION (needs a ${NEED}GB+ USB-3 stick)"

ISO="$REPO/usb/out/onionmind-matchstick-$EDITION-amd64.iso"

# --- build if the ISO isn't there yet ------------------------------------------
if [ ! -f "$ISO" ]; then
  if [ "$DRY" = 1 ]; then say "DRY: would build $ISO"; exit 0; fi
  say "Checking Docker"
  docker info >/dev/null 2>&1 || { warn "Docker isn't running - install/start it, then rerun."; exit 1; }
  say "Building the stick (downloads cached; the squashfs step takes ~1h first time)"
  docker build -f "$REPO/usb/Dockerfile" -t onionmind-usb "$REPO"
  docker run --rm --privileged \
    -v "$REPO/usb/cache:/onionmind/usb/cache" \
    -v "$REPO/usb/out:/onionmind/usb/out" \
    onionmind-usb "$EDITION"
fi
say "ISO: $ISO ($(du -h "$ISO" | cut -f1))"

# --- pick a stick ----------------------------------------------------------------
mapfile -t sticks < <(lsblk -dnbo NAME,SIZE,MODEL,TRAN | awk '$4=="usb" {print $1, $2, $3}')
[ ${#sticks[@]} -ge 1 ] || { warn "No USB stick found - plug one in and rerun."; exit 1; }
echo
i=1
for s in "${sticks[@]}"; do echo "   [$i] /dev/$s"; i=$((i+1)); done
printf '   Which one? [number] '
read -r n
DEV="/dev/$(echo "${sticks[$((n-1))]:-}" | awk '{print $1}')"
[ -b "$DEV" ] || die "no such stick"
SIZE_GB=$(( $(blockdev --getsize64 "$DEV" 2>/dev/null || echo 0) / 1073741824 ))
if [ "$SIZE_GB" -lt "$NEED" ] 2>/dev/null; then
  warn "That stick looks too small for the $EDITION edition (${NEED}GB needed)."
fi
echo
printf '\033[31m   ABOUT TO ERASE %s - the ISO will be written raw\033[0m\n' "$DEV"
printf '   Type YES to continue: '
read -r confirm
[ "$confirm" = "YES" ] || { say "aborted, nothing written"; exit 0; }

if [ "$DRY" = 1 ]; then say "DRY: would dd $ISO -> $DEV"; exit 0; fi

# --- raw write --------------------------------------------------------------------
say "Writing (USB speed is the limit)"
sudo umount "$DEV"?* 2>/dev/null || true
sudo dd if="$ISO" of="$DEV" bs=4M conv=fsync status=progress
sync
say "Done. Unplug the stick, then:"
echo "   1. Plug it into the machine you want to boot"
echo "   2. Turn Secure Boot OFF in the firmware (the NVIDIA driver is unsigned)"
echo "   3. Boot menu: F12 / F8 / Esc / F9 (varies by maker) - pick USB"
echo "   4. First boot takes a minute. Then: sudo onionmind-status"
