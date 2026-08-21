#!/bin/sh
# Tails' htpdate pattern, minimal: once Tor has a circuit, read a Date header
# over Tor and step the clock if it's off by more than two minutes. A live box
# has no RTC it can trust - Windows dual-boot machines often carry local-time
# hardware clocks, and wrong clocks break Tor consensus and TLS before
# anything else can run.
#
# -k is deliberate: with a badly wrong clock, certificate validation would
# fail before we ever got the Date header needed to fix the clock. Tor's
# onion hop authenticates the transport; the header only has to be roughly
# right.
#
# Known limit, same one Tails' design doc discusses: if the clock is wrong
# enough that Tor cannot bootstrap at all, nothing over Tor can fix it - we
# print manual instructions and fail. Tails solves that with an out-of-Tor
# rough sync first; doing that here would leak timing to the local network.
DRYRUN="${ONIONMIND_CLOCK_DRYRUN:-0}"

for i in $(seq 1 60); do
  d=$(curl -k -sf --socks5-hostname 127.0.0.1:9050 -m 20 -sI \
        https://check.torproject.org/api/ip \
      | tr -d '\r' | sed -n 's/^[Dd]ate: //p' | head -1)
  if [ -n "$d" ]; then
    want=$(TZ=UTC date -d "$d" +%s 2>/dev/null) || continue
    now=$(date +%s)
    if [ $((want - now)) -gt 120 ] || [ $((now - want)) -gt 120 ]; then
      echo "[clock] off by $((want - now))s, setting from: $d"
      if [ "$DRYRUN" = 1 ]; then
        echo "[clock] dry-run: would run date -s @$want"
      else
        date -s "@$want"
      fi
    else
      echo "[clock] ok (within 120s)"
    fi
    exit 0
  fi
  sleep 5
done
echo "[clock] no Tor circuit in 5min - if the clock is far off, Tor cannot even" >&2
echo "bootstrap. Set it roughly right by hand, then restart Tor:" >&2
echo "  sudo timedatectl set-time 'YYYY-MM-DD HH:MM'   # UTC, minute-accurate is plenty" >&2
echo "  sudo systemctl restart tor@default" >&2
exit 1
