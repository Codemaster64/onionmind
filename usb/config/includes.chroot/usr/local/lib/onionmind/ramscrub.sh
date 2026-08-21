#!/bin/sh
# Best-effort RAM scrub at clean shutdown: overwrite all currently-FREE
# memory with zeros so prompt text and model context that was already freed
# stop being recoverable by a cold-boot attacker.
#
# What this is NOT: the Tails-grade answer. Kernel-allocated pages and the
# scrubber itself stay resident, and NOTHING runs if the machine is power-cut
# or the battery is pulled - only a clean shutdown triggers it. The full fix
# is kexec'ing into a dedicated wipe environment (Tails' "erase memory on
# shutdown" blueprint, Kicksecure's ram-wipe); not portable here without
# hardware to test on.
#
# Every failure is swallowed on purpose: shutdown must always proceed.
# ONIONMIND_SCRUB_KB caps the scrub size (test hook / small-RAM machines).
avail="${ONIONMIND_SCRUB_KB:-$(awk '/MemAvailable/ {print int($2*0.9)}' /proc/meminfo)}"
case "$avail" in ''|*[!0-9]*) exit 0 ;; esac
[ "$avail" -gt 0 ] || exit 0
mkdir -p /run/onionmind-scrub 2>/dev/null || exit 0
mount -t tmpfs -o "size=${avail}k" tmpfs /run/onionmind-scrub 2>/dev/null || exit 0
dd if=/dev/zero of=/run/onionmind-scrub/f bs=1M count=$((avail / 1024)) 2>/dev/null
sync
rm -f /run/onionmind-scrub/f
umount /run/onionmind-scrub 2>/dev/null
echo "[ramscrub] overwrote ~${avail}kB of free RAM"
exit 0
