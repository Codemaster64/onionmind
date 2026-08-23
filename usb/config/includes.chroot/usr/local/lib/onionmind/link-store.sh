#!/bin/sh
# The blob store is inside the squashfs - read-only. Ollama wants a writable
# OLLAMA_MODELS root, so give it one in RAM: the manifests (a few KB) are
# copied, the big blobs are symlinked. Costs no meaningful RAM and leaves the
# stick untouched.
set -e
SRC=/usr/lib/onionmind/models
DST=/run/onionmind-models
rm -rf "$DST"
mkdir -p "$DST/blobs" "$DST/manifests"
cp -a "$SRC/manifests/." "$DST/manifests/"
for b in "$SRC"/blobs/*; do
  [ -e "$b" ] || continue    # an unmatched glob is the literal pattern, and
                             # without this ln -s happily creates a link named '*'
  ln -s "$b" "$DST/blobs/$(basename "$b")"
done
