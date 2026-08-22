#!/usr/bin/env bash
# Launcher icons from the repo's own onionmind.ico (which is logo.svg rendered
# at 16-256px) - single source of truth, no hand-drawn Android icons.
set -e
cd "$(dirname "$0")/.."
for d in mdpi:48 hdpi:72 xhdpi:96 xxhdpi:144 xxxhdpi:192; do
  density="${d%%:*}"; size="${d##*:}"
  mkdir -p "android/app/src/main/res/mipmap-$density"
  convert "onionmind.ico[0]" -resize "${size}x${size}" \
    "android/app/src/main/res/mipmap-$density/ic_launcher.png"
done
echo "icons: $(ls android/app/src/main/res/mipmap-*/ic_launcher.png | wc -l) densities"
