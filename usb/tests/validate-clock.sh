# Validates the htpdate-pattern clock script end-to-end: a real tor daemon in
# the container, a real circuit, then the script's fetch+parse+decide path in
# dry-run. Unprivileged container + DRYRUN=1 are deliberate: containers share
# the host kernel clock, so actually stepping the clock here would change the
# clock of the machine running the test.
# NOTE: bash, not sh - the /dev/tcp port probe below is a bash-ism.
# Run via: docker run --rm -v "<repo>/usb/tests:/t" -v "<repo>/usb/config:/c" \
#            debian:trixie bash /t/validate-clock.sh
set -e
apt-get update -qq >/dev/null
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq tor curl bash >/dev/null 2>&1

# A bare container has no systemd-tmpfiles, so Debian's service defaults
# (control socket under /run/tor, syslog logging) kill tor right after it
# opens the SOCKS port. Give the test its own minimal config instead - the
# live image runs the stock packaged setup, where those dirs exist.
tor --SocksPort 9050 --DataDirectory /tmp/tor-data \
    --Log "notice file /tmp/tor.log" > /dev/null 2>&1 &
for i in $(seq 1 45); do
  (exec 3<>/dev/tcp/127.0.0.1/9050) 2>/dev/null && break
  sleep 2
done
(exec 3<>/dev/tcp/127.0.0.1/9050) 2>/dev/null || { echo "tor did not start:"; tail /tmp/tor.log; exit 1; }
echo "tor SOCKS up on 9050 (circuit bootstrap is the clock script's own retry loop)"

echo "== dry-run of the clock script (live Tor circuit) =="
cp /c/includes.chroot/usr/local/lib/onionmind/set-clock-over-tor.sh /tmp/clock.sh
ONIONMIND_CLOCK_DRYRUN=1 sh /tmp/clock.sh

echo "== parse sanity against a canned header =="
d='Thu, 21 Aug 2026 12:34:56 GMT'
want=$(TZ=UTC date -d "$d" +%s)
now=$(date +%s)
echo "canned '$d' -> epoch $want (delta vs now: $((want-now))s)"
[ "$((want-now))" -lt 400000 ] && [ "$((now-want))" -lt 400000 ] \
  || { echo "parse produced an implausible epoch"; exit 1; }

echo DONE_CLOCK_OK
