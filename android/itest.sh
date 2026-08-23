#!/usr/bin/env bash
# The :core logic tests, run against a REAL tor daemon inside the build image:
# the SOCKS5-with-auth client, tor verification, the DDG onion search and the
# per-block result parser - the same things the phone will do, exercised for
# real before anything is packaged.
set -e
cd "$(dirname "$0")"

# Offline logic tests first - no tor needed, and a failure here is cheaper to
# read than the same bug surfacing as a TLS error three layers up. NetTest is
# excluded from a plain `test` run by core/build.gradle.kts.
gradle :core:test --console=plain 2>&1 | grep -E "e: |FAILED|Exception|BUILD" | head -20
test ${PIPESTATUS[0]} -eq 0 || exit 1
echo "CORE_UNIT_OK"

mkdir -p /run/tor-test
tor --SocksPort 9050 --DataDirectory /tmp/tor-data \
    --Log "notice file /tmp/tor.log" >/dev/null 2>&1 &
for i in $(seq 1 30); do
  (exec 3<>/dev/tcp/127.0.0.1/9050) 2>/dev/null && break
  sleep 2
done
(exec 3<>/dev/tcp/127.0.0.1/9050) 2>/dev/null || { echo "tor did not start"; tail /tmp/tor.log; exit 1; }
echo "tor up on 9050"
gradle :core:test -Ponionmind.net.tests=true --tests 'org.onionmind.core.NetTest' --console=plain 2>&1 \
  | grep -E "e: |FAILED|PASSED|Exception|went wrong|BUILD" | head -20
test ${PIPESTATUS[0]} -eq 0 || exit 1
echo "CORE_NET_OK"
