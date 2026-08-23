package org.onionmind.core

import kotlin.test.Test
import kotlin.test.assertEquals
import java.io.DataInputStream
import java.net.InetSocketAddress
import java.net.ServerSocket
import kotlin.concurrent.thread

/**
 * Offline handshake test - no tor, no network, so it runs in a plain
 * `gradle :core:test`.
 *
 * The regression it exists for: a CONNECT reply that carries a DOMAIN bound
 * address (ATYP 0x03). The old code computed its length as `x and 0xff + 2`,
 * which Kotlin parses as `x and 0x101` because infix operators bind looser
 * than `+`, and then skipped the read entirely. The bound address stayed in
 * the stream and prepended itself to the first application bytes - a TLS
 * handshake failure with no obvious cause.
 */
class Socks5Test {

    private val bound = "tor.local".toByteArray()      // 9-byte domain bound address
    private val payload = "HTTP/1.1 200 OK\r\n".toByteArray()

    /** A SOCKS5 proxy that always answers CONNECT with an ATYP=3 bound address. */
    private fun fakeProxy(): ServerSocket {
        val srv = ServerSocket(0)
        thread(isDaemon = true) {
            srv.accept().use { c ->
                val i = DataInputStream(c.getInputStream())
                val o = c.getOutputStream()
                i.readNBytes(3)                                    // greeting
                o.write(byteArrayOf(5, 2))                         // username/password
                val ulen = i.readNBytes(2)[1].toInt()              // ver + ulen
                i.readNBytes(ulen)
                i.readNBytes(i.readNBytes(1)[0].toInt())           // plen + pass
                o.write(byteArrayOf(1, 0))                         // auth ok
                i.readNBytes(4)                                    // ver cmd rsv atyp
                i.readNBytes(i.readNBytes(1)[0].toInt() + 2)       // host + port
                o.write(byteArrayOf(5, 0, 0, 3, bound.size.toByte()))
                o.write(bound)
                o.write(byteArrayOf(0x01, 0xbb.toByte()))          // port 443
                o.write(payload)                                   // first app bytes
                o.flush()
            }
        }
        return srv
    }

    @Test fun domainBoundAddressIsDrainedBeforeApplicationData() {
        fakeProxy().use { srv ->
            val s = Socks5Socket(InetSocketAddress("127.0.0.1", srv.localPort), "deadbeef", "x")
            s.use {
                it.connect(InetSocketAddress("example.onion", 443), 5000)
                val got = DataInputStream(it.getInputStream()).readNBytes(payload.size)
                assertEquals(
                    String(payload), String(got),
                    "bound address leaked into the stream - the reply was not fully drained")
            }
        }
    }
}
