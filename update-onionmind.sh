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
model='inferno-27b'
if [ -f "$DIR/onionmind.py" ]; then
  model=$(sed -n 's/^MODEL[[:space:]]*=[[:space:]]*"\([^"]*\)"/\1/p' "$DIR/onionmind.py" | head -1)
  model=${model:-inferno-27b}
fi
sed -E "s/^MODEL[[:space:]]*=.*/MODEL  = \"$model\"/" "$tmp" > "$DIR/onionmind.py"
chmod 755 "$DIR/onionmind.py"
curl -fsSL https://raw.githubusercontent.com/Codemaster64/onionmind/main/dsh-onionmind-tor-search.js \
  -o "$DIR/dsh-onionmind-tor-search.js"
curl -fsSL https://raw.githubusercontent.com/Codemaster64/onionmind/main/dsh-onionmind-tor.patch.yml \
  -o "$DIR/dsh-onionmind-tor.patch.yml"
sed -i "s|@ONIONMIND_DSH_PLUGIN@|$DIR/dsh-onionmind-tor-search.js|" "$DIR/dsh-onionmind-tor.patch.yml"
echo "Updated Onionmind code ($model). Model weights were not changed."
