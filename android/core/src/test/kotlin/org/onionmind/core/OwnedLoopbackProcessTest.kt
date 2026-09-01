package org.onionmind.core

import java.io.InputStream
import java.io.OutputStream
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class OwnedLoopbackProcessTest {
    @Test fun occupiedPortIsRejectedWithoutLaunchingOrBecomingReady() {
        var launches = 0
        val runtime = OwnedLoopbackProcess(listenerOpen = { true })

        assertFalse(runtime.ensure { launches++; FakeProcess() })
        assertEquals(0, launches)
        assertFalse(runtime.ready())
    }

    @Test fun exactLiveChildBecomesReadyAndIsReused() {
        var portOpen = false
        var launches = 0
        val child = FakeProcess()
        val runtime = OwnedLoopbackProcess(listenerOpen = { portOpen })

        assertTrue(runtime.ensure { launches++; child })
        assertFalse(runtime.ready())
        portOpen = true
        assertTrue(runtime.awaitReady(timeoutMillis = 0))
        assertTrue(runtime.ready())
        assertTrue(runtime.ensure { launches++; FakeProcess() })
        assertEquals(1, launches)
    }

    @Test fun deadChildDoesNotTurnAnAttackersListenerIntoOurs() {
        var portOpen = false
        var launches = 0
        val child = FakeProcess()
        val runtime = OwnedLoopbackProcess(listenerOpen = { portOpen })
        assertTrue(runtime.ensure { launches++; child })

        child.running = false
        portOpen = true
        assertFalse(runtime.ready())
        assertFalse(runtime.ensure { launches++; FakeProcess() })
        assertEquals(1, launches)
    }

    @Test fun childDeathWhileWaitingFailsClosed() {
        var portOpen = false
        var now = 0L
        val child = FakeProcess()
        val runtime = OwnedLoopbackProcess(
            listenerOpen = { portOpen },
            pause = {
                child.running = false
                portOpen = true
                now += 250_000_000
            },
            nanoTime = { now },
        )
        assertTrue(runtime.ensure { child })

        assertFalse(runtime.awaitReady(timeoutMillis = 1_000))
    }

    @Test fun stopDestroysOnlyTheStoredChild() {
        var portOpen = false
        val child = FakeProcess()
        val runtime = OwnedLoopbackProcess(listenerOpen = { portOpen })
        assertTrue(runtime.ensure { child })

        runtime.stop()
        assertEquals(1, child.destroyCount)
        assertFalse(runtime.ready())

        portOpen = true
        runtime.stop()
        assertEquals(1, child.destroyCount)
    }

    private class FakeProcess(var running: Boolean = true) : Process() {
        var destroyCount = 0

        override fun getOutputStream(): OutputStream = OutputStream.nullOutputStream()
        override fun getInputStream(): InputStream = InputStream.nullInputStream()
        override fun getErrorStream(): InputStream = InputStream.nullInputStream()
        override fun waitFor(): Int { running = false; return 0 }
        override fun exitValue(): Int = if (running) throw IllegalThreadStateException() else 0
        override fun destroy() { destroyCount++; running = false }
        override fun isAlive(): Boolean = running
    }
}
