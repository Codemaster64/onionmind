# Validates the two ollama mechanisms the live image depends on, in a container
# that stands in for the live-build chroot:
#   1. `ollama create` from a GGUF bakes blobs+manifests into OLLAMA_MODELS at
#      BUILD time, so no registration (and no RAM copy) happens at boot.
#   2. ollama can SERVE that store when it is read-only (the squashfs case),
#      via a tiny /run store that symlinks the big blobs and copies the manifests.
# Expects usb/cache mounted at /cache with ollama-linux-amd64.tar.zst and a small
# test GGUF. Run with --privileged (bind-mount test).
# Run via: docker run --rm --privileged -v "<repo>/usb/cache:/cache" \
#            -v "<repo>/usb/tests:/t" debian:trixie sh /t/validate-ollama-bake.sh
set -e
apt-get update -qq >/dev/null
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl ca-certificates zstd python3 >/dev/null 2>&1

echo "== 1. tarball layout =="
tar --zstd -tf /cache/ollama-linux-amd64.tar.zst | head -4
echo "   ($(tar --zstd -tf /cache/ollama-linux-amd64.tar.zst | grep -c .) files)"
tar -C /usr --zstd -xf /cache/ollama-linux-amd64.tar.zst
ls /usr/bin/ollama
ls /usr/lib/ollama | head -4

echo "== 2. serve + create (what the build hook does) =="
export OLLAMA_MODELS=/store/models OLLAMA_HOST=127.0.0.1:11434 HOME=/root
/usr/bin/ollama serve >/tmp/ollama.log 2>&1 &
SRV=$!
for i in $(seq 1 30); do curl -sf --noproxy '*' -m 2 http://127.0.0.1:11434/api/version >/dev/null && break; sleep 1; done
curl -sf --noproxy '*' http://127.0.0.1:11434/api/version; echo
printf 'FROM /cache/Qwen3-0.6B.Q4_K_M.gguf\nPARAMETER num_ctx 2048\n' > /tmp/MF
ollama create test-model -f /tmp/MF
echo "-- store layout --"
find /store/models -type f -printf '%12s  %p\n' | sort -rn | head -6
echo "-- manifest --"
cat /store/models/manifests/registry.ollama.ai/library/test-model/latest; echo

echo "== 2b. GC: ollama create keeps BOTH the raw GGUF blob and its converted"
echo "    layer (same bytes twice). Drop what the manifest doesn't reference -"
echo "    exactly what the bake hook does. Then prove inference still works."
python3 - <<'PY'
import json, glob, os
root = "/store/models"
keep = set()
for m in glob.glob(root + "/manifests/**/*", recursive=True):
    if not os.path.isfile(m):
        continue
    d = json.load(open(m))
    keep.add(d["config"]["digest"].split(":")[1])
    keep.update(l["digest"].split(":")[1] for l in d.get("layers", []))
for b in glob.glob(root + "/blobs/sha256-*"):
    if os.path.basename(b).split("-", 1)[1] not in keep:
        print(f"GC unreferenced blob: {os.path.basename(b)}")
        os.unlink(b)
print(f"referenced: {len(keep)} blobs kept")
PY
du -sh /store/models
find /store /root -name 'id_ed25519*' -printf 'id-key: %p\n' || true
kill "$SRV"; sleep 2

echo "== 3. serve from a READ-ONLY store AFTER GC (the squashfs case) =="
mount --bind /store /store && mount -o remount,ro,bind /store
mkdir -p /run2/blobs /run2/manifests
cp -a /store/models/manifests/. /run2/manifests/
for b in /store/models/blobs/*; do ln -s "$b" "/run2/blobs/$(basename "$b")"; done
OLLAMA_MODELS=/run2 OLLAMA_HOST=127.0.0.1:11435 /usr/bin/ollama serve >/tmp/ollama2.log 2>&1 &
SRV2=$!
for i in $(seq 1 30); do curl -sf --noproxy '*' -m 2 http://127.0.0.1:11435/api/version >/dev/null && break; sleep 1; done
OLLAMA_HOST=127.0.0.1:11435 ollama run test-model 'Reply with exactly: GC_RO_STORE_OK' | head -1
kill "$SRV2" 2>/dev/null || true
echo DONE_ALL_OK
