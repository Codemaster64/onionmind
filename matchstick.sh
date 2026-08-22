#!/usr/bin/env bash
# Matchstick, the easy way: run this, pick an edition, plug a stick when asked.
# Builds the ISO with Docker if needed, then raw-writes it to a USB stick.
#   ./matchstick.sh [pocket|daily|standard|flagship|max|4b|9b|8gb|12gb|17gb]
set -euo pipefail
say()  { printf '\033[36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33m    %s\033[0m\n' "$*"; }

REPO="$(cd "$(dirname "$0")" && pwd)"
DRY="${MATCHSTICK_DRY:-0}"

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

EDITION="${1:-$(edition_menu)}"
case "$EDITION" in
  4b|pocket)   EDITION=4b;  NEED=16 ;;
  9b|daily)    EDITION=9b;  NEED=16 ;;
  8gb|standard) EDITION=8gb; NEED=32 ;;
  12gb|flagship) EDITION=12gb; NEED=32 ;;
  17gb|max)    EDITION=17gb; NEED=32 ;;
  *) echo "unknown edition: $EDITION"; exit 1 ;;
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
[ -b "$DEV" ] || { echo "no such stick"; exit 1; }
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
