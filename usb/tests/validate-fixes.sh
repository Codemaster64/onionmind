# Smoke-tests the three privacy fixes: MAC verification, the shutdown RAM
# scrub, and the bridge-line transform build.sh applies to ONIONMIND_BRIDGES.
# Runs unprivileged for mac/bridges; the scrub needs --privileged to mount
# tmpfs (the live image always runs as root, where this is a non-issue).
# Run via: docker run --rm --privileged -v "<repo>/usb/tests:/t" \
#            -v "<repo>/usb/config:/c" debian:trixie sh /t/validate-fixes.sh
set -e
L=/c/includes.chroot/usr/local/lib/onionmind

echo "== mac-check: randomized MAC passes =="
mkdir -p /tmp/net/eth0 /tmp/net/eth1 /tmp/net/lo
echo "02:42:ac:11:00:02" > /tmp/net/eth0/address; echo up > /tmp/net/eth0/operstate
echo "72:1f:a2:9b:33:d1" > /tmp/net/eth1/address; echo down > /tmp/net/eth1/operstate
echo "00:00:00:00:00:00" > /tmp/net/lo/address;  echo unknown > /tmp/net/lo/operstate
SYSFS_NET=/tmp/net sh "$L/check-mac.sh" | grep -q "all active interfaces are randomized" \
  && echo "PASS path OK (down interface with burned-in MAC ignored, as intended)"

echo "== mac-check: burned-in MAC on UP interface warns =="
echo "3c:d9:2b:11:22:33" > /tmp/net/eth0/address
SYSFS_NET=/tmp/net sh "$L/check-mac.sh" 2>&1 | grep -q "WARNING.*3c:d9:2b:11:22:33" \
  && echo "WARN path OK"

echo "== ramscrub: overwrites the cap and unmounts (128MB) =="
mkdir -p /run/onionmind-scrub
ONIONMIND_SCRUB_KB=131072 sh "$L/ramscrub.sh"
mount | grep -q onionmind-scrub && { echo "tmpfs left mounted"; exit 1; }
[ -e /run/onionmind-scrub/f ] && { echo "scrub file left behind"; exit 1; }
echo "scrub OK"

echo "== ramscrub: garbage KB value exits clean =="
ONIONMIND_SCRUB_KB=banana sh "$L/ramscrub.sh" && echo "bad-value OK"

echo "== bridge-line transform (what build.sh does to ONIONMIND_BRIDGES) =="
out=$(printf '%s\n' "obfs4 1.2.3.4:9130 cert=abc iat-mode=0;Bridge obfs4 5.6.7.8:99 cert=zz" \
       | tr ';' '\n' | sed -e 's/^[Bb]ridge[[:space:]]*/Bridge /' -e t -e 's/^/Bridge /')
echo "$out"
echo "$out" | grep -qx "Bridge obfs4 1.2.3.4:9130 cert=abc iat-mode=0" \
  && echo "$out" | grep -qx "Bridge obfs4 5.6.7.8:99 cert=zz" \
  && echo "bridges OK"

echo DONE_FIXES_OK
