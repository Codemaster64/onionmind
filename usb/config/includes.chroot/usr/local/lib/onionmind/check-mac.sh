#!/bin/sh
# Tails fails CLOSED when MAC spoofing fails; this appliance warns LOUDLY
# instead - blocking networking outright could brick the stick on odd
# hardware that can't change its MAC. A burned-in MAC on an UP interface
# means the LAN can link this session to the physical device: that is a
# privacy incident, so it goes to the console and the journal.
# SYSFS_NET exists so the test harness can feed a fake interface tree.
SYSFS_NET="${SYSFS_NET:-/sys/class/net}"
bad=0
for link in "$SYSFS_NET"/*; do
  name=$(basename "$link")
  [ "$name" = "lo" ] && continue
  [ -r "$link/address" ] || continue
  addr=$(cat "$link/address")
  [ -n "$addr" ] || continue
  grep -qx up "$link/operstate" 2>/dev/null || continue
  first=$(printf %s "$addr" | cut -c1-2)
  if [ "$(( 0x$first & 0x02 ))" -eq 0 ]; then
    msg="[mac] WARNING: $name is UP with its burned-in MAC $addr - the LAN can identify this device"
    echo "$msg" >&2
    echo "$msg" > /dev/console 2>/dev/null || true
    bad=1
  fi
done
if [ "$bad" -eq 0 ]; then
  echo "[mac] all active interfaces are randomized"
fi
exit 0
