package org.onionmind.core

import kotlin.test.Test
import kotlin.test.assertTrue

/**
 * Real-network tests, run inside the build container against a real tor daemon
 * (android/itest.sh). Excluded from plain `test` runs so nothing here needs a
 * network to compile-and-test the logic offline.
 */
class NetTest {

    @Test fun torCheckFindsTheDaemon() {
        val exit = Agent.torCheck()
        assertTrue(exit != null, "tor check failed - is tor on 9050?")
        System.err.println("[net-test] tor exit ip: $exit")
    }

    @Test fun searchRidesAFreshCircuit() {
        val r = Agent.webSearch("whoonion tor project")
        System.err.println("[net-test] results: " + r.take(200))
        assertTrue(r.startsWith("- "), "no results: $r")
    }
}
