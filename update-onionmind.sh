#!/usr/bin/env bash
set -euo pipefail
DIR="${ONIONMIND_DIR:-/var/lib/qwen}"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --install-dir) DIR="$2"; shift 2 ;;
    *) echo "usage: $0 [--install-dir DIR]" >&2; exit 2 ;;
  esac
done
raw='https://raw.githubusercontent.com/Codemaster64/onionmind/main/onionmind.py'
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
curl -fsSL "$raw" -o "$tmp"
model='inferno'
if [ -f "$DIR/onionmind.py" ]; then
  model=$(sed -n 's/^MODEL[[:space:]]*=[[:space:]]*"\([^"]*\)"/\1/p' "$DIR/onionmind.py" | head -1)
  model=${model:-inferno}
fi
sed -E "s/^MODEL[[:space:]]*=.*/MODEL  = \"$model\"/" "$tmp" > "$DIR/onionmind.py"
chmod 755 "$DIR/onionmind.py"
curl -fsSL https://raw.githubusercontent.com/Codemaster64/onionmind/main/dsh-onionmind-tor-search.js \
  -o "$DIR/dsh-onionmind-tor-search.js"
curl -fsSL https://raw.githubusercontent.com/Codemaster64/onionmind/main/dsh-onionmind-tor.patch.yml \
  -o "$DIR/dsh-onionmind-tor.patch.yml"
sed -i "s|@ONIONMIND_DSH_PLUGIN@|$DIR/dsh-onionmind-tor-search.js|" "$DIR/dsh-onionmind-tor.patch.yml"

# Rewrite the coding launcher too. Installs from before the placeholder fix carry
# a literal $DIR/$MODEL_NAME (a quoted heredoc never expanded them), so they run
# `ollama launch dsh --model ""` and have stayed broken through every update.
if [ -w /usr/local/bin ] || command -v sudo >/dev/null 2>&1; then
  SUDO=""; [ -w /usr/local/bin ] || SUDO=sudo
  $SUDO tee /usr/local/bin/onionmind-code >/dev/null <<'AGENT'
#!/bin/sh
export ONIONMIND_PY="@DIR@/onionmind.py"
export ONIONMIND_PYTHON=python3
exec ollama launch dsh --model "@MODEL@" -- --patch "@DIR@/dsh-onionmind-tor.patch.yml" "$@"
AGENT
  $SUDO sed -i -e "s|@DIR@|$DIR|g" -e "s|@MODEL@|$model|g" /usr/local/bin/onionmind-code
  $SUDO chmod 755 /usr/local/bin/onionmind-code
fi
echo "Updated Onionmind code ($model). Model weights were not changed."
