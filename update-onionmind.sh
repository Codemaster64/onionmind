#!/usr/bin/env bash
set -euo pipefail

DIR="${ONIONMIND_DIR:-/var/lib/qwen}"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --install-dir) DIR="$2"; shift 2 ;;
    *) echo "usage: $0 [--install-dir DIR]" >&2; exit 2 ;;
  esac
done

command -v curl >/dev/null 2>&1 || { echo "curl is required" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "python3 is required" >&2; exit 1; }
DIR=$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).expanduser().resolve())' "$DIR")
HOME_REAL=$(python3 -c 'from pathlib import Path; print(Path.home().resolve())')
case "$DIR" in
  /|/bin|/boot|/dev|/etc|/home|/lib|/lib64|/opt|/proc|/root|/run|/sbin|/srv|/sys|/tmp|/usr|/var|/var/lib|"$HOME_REAL")
    echo "Refusing broad install directory: $DIR" >&2
    exit 1
    ;;
esac
mkdir -p "$DIR"

model='inferno'
if [ -f "$DIR/onionmind.py" ]; then
  model=$(sed -n 's/^MODEL[[:space:]]*=[[:space:]]*"\([^"]*\)"/\1/p' "$DIR/onionmind.py" | head -1)
  model=${model:-inferno}
fi

STAGE=$(mktemp -d)
PREPARED=""
cleanup() {
  rm -rf -- "$STAGE"
  if [ -n "$PREPARED" ] && [ -d "$PREPARED" ]; then
    rm -rf -- "$PREPARED"
  fi
}
trap cleanup EXIT

revision=$(curl -fsSL 'https://api.github.com/repos/Codemaster64/onionmind/commits/main' |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["sha"])')
[[ "$revision" =~ ^[0-9a-fA-F]{40}$ ]] || { echo "Could not resolve a fixed update revision." >&2; exit 1; }
base="https://raw.githubusercontent.com/Codemaster64/onionmind/$revision"
FILES=(
  onionmind.py
  onionmind_desktop_core.py
  onionmind_desktop.py
  dsh-onionmind-tor-search.js
  dsh-onionmind-tor.patch.yml
)
for name in "${FILES[@]}"; do
  curl -fsSL "$base/$name" -o "$STAGE/$name"
done

python3 - "$STAGE/onionmind.py" "$model" <<'PY'
from pathlib import Path
import json
import re
import sys

path = Path(sys.argv[1])
model = sys.argv[2]
text = path.read_text(encoding="utf-8")
text, count = re.subn(
    r'^MODEL\s*=.*$',
    lambda _match: f"MODEL  = {json.dumps(model, ensure_ascii=False)}",
    text,
    count=1,
    flags=re.MULTILINE,
)
if count != 1:
    raise SystemExit("MODEL assignment is missing from downloaded onionmind.py")
path.write_text(text, encoding="utf-8", newline="\n")
PY

python3 - "$STAGE/dsh-onionmind-tor.patch.yml" "$DIR/dsh-onionmind-tor-search.js" <<'PY'
from pathlib import Path
import sys

patch = Path(sys.argv[1])
plugin = Path(sys.argv[2]).as_posix().replace("'", "''")
text = patch.read_text(encoding="utf-8")
if "@ONIONMIND_DSH_PLUGIN@" not in text:
    raise SystemExit("DSH patch placeholder is missing")
patch.write_text(text.replace("@ONIONMIND_DSH_PLUGIN@", plugin), encoding="utf-8", newline="\n")
PY

python3 -m py_compile "$STAGE/onionmind.py"
if python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  python3 -m py_compile \
    "$STAGE/onionmind_desktop_core.py" \
    "$STAGE/onionmind_desktop.py"
else
  echo "Python 3.10+ is required for the native workbench; the classic UI will remain active." >&2
fi
chmod 755 "$STAGE/onionmind.py"
chmod 644 \
  "$STAGE/onionmind_desktop_core.py" \
  "$STAGE/onionmind_desktop.py" \
  "$STAGE/dsh-onionmind-tor-search.js" \
  "$STAGE/dsh-onionmind-tor.patch.yml"

# Prepare every file on the destination filesystem before replacing anything.
# If an unlikely rename fails mid-set, restore every path already replaced.
PREPARED=$(mktemp -d "$DIR/.onionmind-update.XXXXXX")
BACKUP="$STAGE/backup"
mkdir -p "$BACKUP"
for name in "${FILES[@]}"; do
  cp -p -- "$STAGE/$name" "$PREPARED/$name"
  if [ -f "$DIR/$name" ]; then
    cp -p -- "$DIR/$name" "$BACKUP/$name"
  fi
done

replaced=()
rollback() {
  local name
  for name in "${replaced[@]}"; do
    if [ -f "$BACKUP/$name" ]; then
      cp -p -- "$BACKUP/$name" "$DIR/$name"
    else
      rm -f -- "$DIR/$name"
    fi
  done
}
for name in "${FILES[@]}"; do
  if ! mv -f -- "$PREPARED/$name" "$DIR/$name"; then
    rollback
    echo "Update failed while replacing $name; previous files were restored." >&2
    exit 1
  fi
  replaced+=("$name")
done

if python3 -m venv "$DIR/desktop-env"; then
  desktop_python="$DIR/desktop-env/bin/python"
  desktop_marker="$DIR/desktop-env/.onionmind-desktop-ready"
  rm -f -- "$desktop_marker"
  if ! "$desktop_python" -m pip install --quiet --disable-pip-version-check \
      requests PySocks PySide6-Essentials==6.11.1; then
    echo "Native desktop dependency download failed; checking the existing environment." >&2
  fi
  if "$desktop_python" -c 'import requests, socks, PySide6.QtWidgets'; then
    : > "$desktop_marker"
  else
    echo "Native desktop dependencies could not be imported; the classic UI remains available." >&2
  fi
else
  echo "Could not create the native desktop environment; install python3-venv and rerun." >&2
fi

if [ -w /usr/local/bin ] || command -v sudo >/dev/null 2>&1; then
  SUDO=""; [ -w /usr/local/bin ] || SUDO=sudo
  DIR_LITERAL=$(python3 -c 'import shlex,sys; print(shlex.quote(sys.argv[1]))' "$DIR")
  MODEL_LITERAL=$(python3 -c 'import shlex,sys; print(shlex.quote(sys.argv[1]))' "$model")

  $SUDO tee /usr/local/bin/onionmind >/dev/null <<DESKTOP
#!/bin/sh
DIR=$DIR_LITERAL
systemctl is-active --quiet tor 2>/dev/null || sudo -n systemctl start tor 2>/dev/null \
  || echo "[tor] not running - chat search will refuse until: sudo systemctl start tor"
cd "\$HOME"
PYTHON="\$DIR/desktop-env/bin/python"
[ -x "\$PYTHON" ] && [ -f "\$DIR/desktop-env/.onionmind-desktop-ready" ] || PYTHON=python3
if [ "\$#" -eq 0 ]; then
  exec "\$PYTHON" "\$DIR/onionmind.py" --ui
else
  exec "\$PYTHON" "\$DIR/onionmind.py" "\$@"
fi
DESKTOP
  $SUDO chmod 755 /usr/local/bin/onionmind

  $SUDO tee /usr/local/bin/onionmind-code >/dev/null <<AGENT
#!/bin/sh
DIR=$DIR_LITERAL
MODEL=$MODEL_LITERAL
export ONIONMIND_PY="\$DIR/onionmind.py"
export ONIONMIND_PYTHON=python3
if [ "\$#" -eq 0 ]; then
  echo 'usage: onionmind-code "task for the coding agent"' >&2
  exit 2
fi
NODE_VERSION=\$(node --version 2>/dev/null) || {
  echo 'DeepSeek Harness requires Node.js ^22.19 or 24+. Install a supported Node.js release first.' >&2
  exit 1
}
NODE_VERSION=\${NODE_VERSION#v}
NODE_MAJOR=\${NODE_VERSION%%.*}
NODE_REST=\${NODE_VERSION#*.}
NODE_MINOR=\${NODE_REST%%.*}
case "\$NODE_MAJOR:\$NODE_MINOR" in
  *[!0-9:]*|:) echo "Could not read the installed Node.js version: \$NODE_VERSION" >&2; exit 1 ;;
esac
if [ "\$NODE_MAJOR" -lt 24 ] && { [ "\$NODE_MAJOR" -ne 22 ] || [ "\$NODE_MINOR" -lt 19 ]; }; then
  echo "DeepSeek Harness requires Node.js ^22.19 or 24+; found \$NODE_VERSION." >&2
  exit 1
fi
echo 'Agent network note: Harness traffic is direct; only Onionmind chat search uses Tor.' >&2
exec ollama launch dsh --model "\$MODEL" -- --profile headless "\$*"
AGENT
  $SUDO chmod 755 /usr/local/bin/onionmind-code

  $SUDO tee /usr/local/bin/onionmind-update >/dev/null <<UPDATE
#!/bin/sh
DIR=$DIR_LITERAL
tmp=\$(mktemp) || exit 1
trap 'rm -f "\$tmp"' EXIT
curl -fsSL https://raw.githubusercontent.com/Codemaster64/onionmind/main/update-onionmind.sh -o "\$tmp" || exit 1
bash "\$tmp" --install-dir "\$DIR"
status=\$?
rm -f "\$tmp"
trap - EXIT
exit "\$status"
UPDATE
  $SUDO chmod 755 /usr/local/bin/onionmind-update
fi

echo "Updated Onionmind native workbench and coding agent ($model). Model weights were not changed."
