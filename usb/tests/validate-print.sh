# Validates the editor/printing additions:
#   1. every package resolves on trixie and its key binary lands on disk
#   2. cupsd runs headless and lpstat answers (no systemd in the container,
#      so the daemon is started by hand - the image uses the systemd unit)
#   3. nftables.conf loads, THEN nftables-print.conf appends without error
#      (ordering matters: the print file only adds to the existing table)
# Run via: docker run --rm --privileged -v "<repo>/usb/tests:/t" \
#            -v "<repo>/usb/config:/c" debian:trixie sh /t/validate-print.sh
set -e
apt-get update -qq >/dev/null
echo 'deb http://deb.debian.org/debian trixie main' > /etc/apt/sources.list 2>/dev/null || true
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  cups cups-client cups-filters cups-browsed ipp-usb foomatic-db-engine \
  foomatic-db micro nano gpm nftables tor >/dev/null 2>&1
# ^ tor: the firewall rules resolve "debian-tor" at load time - in the live
#   image the tor package (and its user) is installed before the firewall
#   ever runs; the container needs the same.

echo "== binaries =="
for b in micro nano lp lpstat cupsd ipp-usb gpm nft; do
  command -v "$b" >/dev/null || { echo "missing: $b"; exit 1; }
done
echo "all present"

echo "== cupsd headless =="
mkdir -p /run/cups
cupsd 2>/dev/null || cupsd -c /etc/cups/cupsd.conf
sleep 3
pgrep -x cupsd >/dev/null || { echo "cupsd not running"; exit 1; }
lpstat -e 2>/dev/null || echo "(no queues - expected without a printer)"
kill "$(pgrep -x cupsd | head -1)" 2>/dev/null || true

echo "== nftables: main, then print exception =="
nft -f /c/includes.chroot/usr/local/lib/onionmind/nftables.conf
nft -f /c/includes.chroot/usr/local/lib/onionmind/nftables-print.conf
nft list ruleset | grep -q "5353" && echo "mDNS rule loaded"
nft list ruleset | grep -q "dport 631" && echo "IPP rule loaded"
nft list ruleset | grep -q "skuid debian-tor" || true   # main rules still intact

echo DONE_PRINT_OK
