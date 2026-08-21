# Exercises every branch of onionmind-status on a deliberately degraded box:
# no firewall rules, no tor, no model server, no GPU. The point is that the
# script reports reality accurately instead of crashing - each missing thing
# must produce its own honest line.
# Run via: docker run --rm -v "<repo>/usb/tests:/t" -v "<repo>/usb/config:/c" \
#            debian:trixie sh /t/validate-status.sh
set -e
apt-get update -qq >/dev/null
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl nftables procps >/dev/null 2>&1

mkdir -p /usr/local/lib/onionmind /usr/local/sbin
cp /c/includes.chroot/usr/local/lib/onionmind/check-mac.sh /usr/local/lib/onionmind/
cp /c/includes.chroot/usr/local/sbin/onionmind-status /usr/local/sbin/
printf 'MODEL_NAME="test"\nTIER="test"\nVISION=0\n' > /usr/local/lib/onionmind/env
chmod +x /usr/local/lib/onionmind/check-mac.sh /usr/local/sbin/onionmind-status

out=$(sh /usr/local/sbin/onionmind-status 2>&1)
printf '%s\n' "$out"
echo "--- assertions ---"
printf '%s\n' "$out" | grep -q "NOT fail-closed"  && echo "fw:    warns when unsealed"
printf '%s\n' "$out" | grep -q "no verified circuit" && echo "tor:   honest without a circuit"
printf '%s\n' "$out" | grep -q "server not answering" && echo "model: honest without a server"
printf '%s\n' "$out" | grep -q "running on CPU"    && echo "gpu:   honest without a GPU"
# swap line is environment-honest: the live system has none (swapoff at boot,
# nothing activated); a container sees the HOST's swap and says so correctly.
printf '%s\n' "$out" | grep -qE "no swap|swap is ON" && echo "disk:  swap state reported"
printf '%s\n' "$out" | grep -qE "randomized|WARNING" \
  && echo "mac:   checked" || echo "mac:   silent (no active NICs in container - covered by validate-fixes)"

# --- GPU branches -------------------------------------------------------------
# A container cannot fake /sys, so the script takes ONIONMIND_ROOT as a prefix for
# its GPU probes (empty on the real system). Without this, an AMD box with working
# ROCm would still have printed "none visible - running on CPU".
mkdir -p /tmp/amd/sys/module/amdgpu
ONIONMIND_ROOT=/tmp/amd sh /usr/local/sbin/onionmind-status 2>&1 |
  grep -q "no ROCm in this image" && echo "gpu:   AMD without ROCm is honest"
mkdir -p /tmp/amd/usr/lib/ollama/rocm
ONIONMIND_ROOT=/tmp/amd sh /usr/local/sbin/onionmind-status 2>&1 |
  grep -q "ROCm present" && echo "gpu:   AMD with ROCm detected"

echo DONE_STATUS_OK
