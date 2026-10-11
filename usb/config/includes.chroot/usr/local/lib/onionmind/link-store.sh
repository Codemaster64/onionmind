#!/bin/sh
# The blob store is inside the squashfs - read-only. Ollama wants a writable
# OLLAMA_MODELS root, so give it one in RAM: the manifests (a few KB) are
# copied, the big blobs are symlinked. Costs no meaningful RAM and leaves the
# stick untouched.
set -e
SRC=/usr/lib/onionmind/models
DST=/run/onionmind-models
rm -rf "$DST"
mkdir -p "$DST/blobs"
# manifests/ before ollama 0.40, manifests-v2/ from it on. A v2 entry is a
# relative link into ../blobs, so copied as-is it resolves to the blob link
# made below.
found=
for m in "$SRC"/manifests "$SRC"/manifests-v2; do
  [ -d "$m" ] || continue
  cp -a "$m" "$DST/"
  found=1
done
[ -n "$found" ] || { echo "link-store: no manifests in $SRC" >&2; exit 1; }
for b in "$SRC"/blobs/*; do
  [ -e "$b" ] || continue    # an unmatched glob is the literal pattern, and
                             # without this ln -s happily creates a link named '*'
  ln -s "$b" "$DST/blobs/$(basename "$b")"
done
