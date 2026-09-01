#!/usr/bin/env bash
set -euo pipefail

DIR="${ONIONMIND_DIR:-/var/lib/qwen}"
AUDIT=0
ALLOW_NETWORK=0
ASSUME_YES=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --install-dir) DIR="$2"; shift 2 ;;
    --audit) AUDIT=1; shift ;;
    --allow-network) ALLOW_NETWORK=1; shift ;;
    --yes) ASSUME_YES=1; shift ;;
    *) echo "usage: $0 [--install-dir DIR] [--audit] [--allow-network --yes]" >&2; exit 2 ;;
  esac
done

if [ "$AUDIT" = 1 ]; then
  echo "Onionmind updater local audit (no network, writes, downloads, or service starts)"
  printf '  %-24s %s\n' "install directory" "$DIR"
  for command_name in curl python3 ollama node; do
    if command -v "$command_name" >/dev/null 2>&1; then
      printf '  %-24s %s\n' "$command_name" "present ($(command -v "$command_name"))"
    else
      printf '  %-24s %s\n' "$command_name" "MISSING"
    fi
  done
  if [ -f "$DIR/.onionmind-revision" ]; then
    printf '  %-24s %s\n' "installed revision" "$(cat "$DIR/.onionmind-revision")"
  else
    printf '  %-24s %s\n' "installed revision" "unknown"
  fi
  for name in onionmind.py onionmind_desktop_core.py onionmind_desktop.py dsh-onionmind-tor-search.js dsh-onionmind-tor.patch.yml; do
    [ -s "$DIR/$name" ] && state=present || state=MISSING
    printf '  %-24s %s\n' "$name" "$state"
  done
  if [ -x "$DIR/desktop-env/bin/python" ]; then
    desktop_state=$("$DIR/desktop-env/bin/python" - <<'PY' 2>/dev/null || true
from importlib import metadata
import re

def version(name):
    value = metadata.version(name)
    return value, tuple(int(x) for x in re.findall(r"\d+", value)[:3])

rv, r = version("requests")
sv, s = version("PySocks")
qv, q = version("PySide6-Essentials")
ok = (2, 32) <= r < (3,) and (1, 7) <= s < (2,) and (6, 11) <= q < (6, 12)
print(f"requests={rv}, PySocks={sv}, PySide6-Essentials={qv} ({'SUPPORTED' if ok else 'OUTDATED/INCOMPATIBLE'})")
PY
    )
    desktop_state=${desktop_state:-MISSING/INCOMPATIBLE}
  else
    desktop_state=MISSING
  fi
  printf '  %-24s %s\n' "desktop runtime" "$desktop_state"
  printf '  %-24s %s\n' "remote freshness" "UNKNOWN by design; apply permits the GitHub check"
  exit 0
fi

echo "Direct-network plan (nothing has contacted the network yet):"
echo "  - https://api.github.com/repos/Codemaster64/onionmind/commits/main"
echo "      resolve one immutable source revision"
echo "  - https://raw.githubusercontent.com/Codemaster64/onionmind/<resolved-commit>/{onionmind.py,onionmind_desktop_core.py,onionmind_desktop.py,dsh-onionmind-tor-search.js,dsh-onionmind-tor.patch.yml}"
echo "      download only when the recorded local revision differs or files are missing"
echo "  - https://pypi.org/simple/requests/, https://pypi.org/simple/pysocks/, https://pypi.org/simple/pyside6-essentials/"
echo "      repair the isolated desktop runtime only when local version/import checks fail"
echo "  No Tor or Ollama service will be enabled, started, or restarted."
if [ "$ALLOW_NETWORK" != 1 ] || [ "$ASSUME_YES" != 1 ]; then
  [ -t 0 ] || { echo "Network consent required; rerun interactively or pass BOTH --allow-network and --yes." >&2; exit 1; }
  printf 'Continue with exactly the network actions above? [y/N] '
  read -r answer
  case "$answer" in y|Y|yes|YES) ;; *) echo "Aborted before all network activity." >&2; exit 1 ;; esac
fi

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

STAGE=""
PREPARED=""
cleanup() {
  if [ -n "$STAGE" ] && [ -d "$STAGE" ]; then
    rm -rf -- "$STAGE"
  fi
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
CODE_CURRENT=0
if [ -f "$DIR/.onionmind-revision" ] && [ "$(cat "$DIR/.onionmind-revision")" = "$revision" ]; then
  CODE_CURRENT=1
  for name in "${FILES[@]}"; do
    [ -s "$DIR/$name" ] || CODE_CURRENT=0
  done
fi

if [ "$CODE_CURRENT" = 1 ]; then
  echo "Onionmind code is already at $revision; skipping source downloads and replacement."
else
  STAGE=$(mktemp -d)
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
  printf '%s\n' "$revision" > "$DIR/.onionmind-revision"
fi

desktop_python="$DIR/desktop-env/bin/python"
desktop_marker="$DIR/desktop-env/.onionmind-desktop-ready"
desktop_runtime_ready() {
  [ -x "$desktop_python" ] || return 1
  "$desktop_python" - <<'PY' 2>/dev/null
from importlib import metadata
import re

def version(name):
    return tuple(int(x) for x in re.findall(r"\d+", metadata.version(name))[:3])

assert (2, 32) <= version("requests") < (3,)
assert (1, 7) <= version("PySocks") < (2,)
assert (6, 11) <= version("PySide6-Essentials") < (6, 12)
import requests, socks, PySide6.QtWidgets
PY
}
if desktop_runtime_ready; then
  echo "Native desktop dependencies already satisfy the pinned ranges; skipping pip."
  : > "$desktop_marker"
elif python3 -m venv "$DIR/desktop-env"; then
  rm -f -- "$desktop_marker"
  if ! "$desktop_python" -m pip install --quiet --disable-pip-version-check \
      'requests>=2.32,<3' 'PySocks>=1.7,<2' 'PySide6-Essentials>=6.11,<6.12'; then
    echo "Native desktop dependency download failed; checking the existing environment." >&2
  fi
  if desktop_runtime_ready; then
    : > "$desktop_marker"
  else
    echo "Native desktop dependencies are missing or outside supported ranges; the classic UI remains available." >&2
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
systemctl is-active --quiet tor 2>/dev/null || \
  echo "[tor] not running; Onionmind will not start it. Search remains unavailable until you explicitly start Tor." >&2
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
# The agent's only way off this machine is Tor: onionmind.py verifies the
# circuit, puts every child on it, and refuses to start without one.
exec python3 "\$DIR/onionmind.py" --agent --model "\$MODEL" "\$*"
AGENT
  $SUDO chmod 755 /usr/local/bin/onionmind-code

  $SUDO tee /usr/local/bin/onionmind-update >/dev/null <<UPDATE
#!/bin/sh
DIR=$DIR_LITERAL
audit=0
for arg in "\$@"; do [ "\$arg" = --audit ] && audit=1; done
if [ "\$audit" = 1 ]; then
  echo "Onionmind updater local audit (no network or writes)"
  echo "  install directory: \$DIR"
  [ -f "\$DIR/.onionmind-revision" ] && echo "  installed revision: \$(cat "\$DIR/.onionmind-revision")" || echo "  installed revision: unknown"
  for f in onionmind.py onionmind_desktop_core.py onionmind_desktop.py dsh-onionmind-tor-search.js dsh-onionmind-tor.patch.yml; do
    [ -s "\$DIR/\$f" ] && echo "  \$f: present" || echo "  \$f: MISSING"
  done
  echo "  remote freshness: UNKNOWN by design"
  exit 0
fi
echo "Direct-network plan:"
echo "  - https://raw.githubusercontent.com/Codemaster64/onionmind/main/update-onionmind.sh"
echo "      download the updater; it will separately disclose GitHub/PyPI actions"
allow=0; yes=0
for arg in "\$@"; do [ "\$arg" = --allow-network ] && allow=1; [ "\$arg" = --yes ] && yes=1; done
if [ "\$allow" != 1 ] || [ "\$yes" != 1 ]; then
  [ -t 0 ] || { echo "network consent required; pass BOTH --allow-network and --yes" >&2; exit 1; }
  printf 'Download the updater from that exact URL? [y/N] '
  read answer
  case "\$answer" in y|Y|yes|YES) ;; *) echo "aborted before network activity" >&2; exit 1 ;; esac
fi
tmp=\$(mktemp) || exit 1
trap 'rm -f "\$tmp"' EXIT
curl -fsSL https://raw.githubusercontent.com/Codemaster64/onionmind/main/update-onionmind.sh -o "\$tmp" || exit 1
bash "\$tmp" --install-dir "\$DIR" "\$@"
status=\$?
rm -f "\$tmp"
trap - EXIT
exit "\$status"
UPDATE
  $SUDO chmod 755 /usr/local/bin/onionmind-update
fi

echo "Updated Onionmind native workbench and coding agent ($model). Model weights were not changed."
