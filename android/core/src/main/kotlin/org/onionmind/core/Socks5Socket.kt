package org.onionmind.core

import java.io.InputStream
import java.net.InetAddress
import java.net.Socket
import java.net.SocketAddress
import java.security.SecureRandom

/**
 * A minimal SOCKS5 client with username/password auth (RFC 1928 + RFC 1929).
 *
 * Exists for one reason: Tor circuit isolation. A fresh random username per
 * request makes tor build a SEPARATE circuit - the Android port of the
 * random-credentials-per-search trick in onionmind.py. Without it every
 * search shares one exit node and is trivially linkable.
 *
 * Also note: always socks5h semantics (the proxy resolves the hostname) -
 * plain socks5 leaks every hostname to the local resolver.
 */
class Socks5Socket(
    private val proxy: SocketAddress,
    private val user: String,
    private val pass: String,
) : Socket() {

    override fun connect(endpoint: SocketAddress?, timeout: Int) {
        super.connect(proxy, timeout)   // raw TCP to the proxy - super, or we recurse into ourselves
        val out = getOutputStream()
        val inp = getInputStream()

        fun send(b: ByteArray) = out.write(b)
        fun recv(n: Int): ByteArray {
            val b = ByteArray(n)
            var got = 0
            while (got < n) {
                val r = inp.read(b, got, n - got)
                require(r > 0) { "socks connection closed early" }
                got += r
            }
            return b
        }

        // greeting: version 5, one auth method: username/password
        send(byteArrayOf(5, 1, 2))
        val m = recv(2)
        require(m[0] == 5.toByte() && m[1] == 2.toByte()) { "proxy refused username auth" }

        // username/password subnegotiation
        val u = user.toByteArray(); val p = pass.toByteArray()
        val auth = ByteArray(3 + u.size + p.size)
        auth[0] = 1
        auth[1] = u.size.toByte(); System.arraycopy(u, 0, auth, 2, u.size)
        auth[2 + u.size] = p.size.toByte(); System.arraycopy(p, 0, auth, 3 + u.size, p.size)
        send(auth)
        val a = recv(2)
        require(a[1] == 0.toByte()) { "socks auth failed" }

        // CONNECT with a hostname (ATYP 0x03): the proxy resolves it - socks5h
        val host = when (endpoint) {
            is java.net.InetSocketAddress -> endpoint.hostString
            else -> error("unsupported address $endpoint")
        }.toByteArray()
        val port = (endpoint as java.net.InetSocketAddress).port
        val req = ByteArray(7 + host.size)
        req[0] = 5; req[1] = 1; req[2] = 0; req[3] = 3
        req[4] = host.size.toByte(); System.arraycopy(host, 0, req, 5, host.size)
        req[5 + host.size] = (port shr 8).toByte()
        req[6 + host.size] = (port and 0xff).toByte()
        send(req)
        val h = recv(4)
        require(h[1] == 0.toByte()) { "socks CONNECT failed: ${h[1]}" }
        val extra = when (h[3]) {            // skip the bound address
            1.toByte() -> 6                  // ipv4: 4 addr + 2 port
            3.toByte() -> recv(1)[0].toInt() and 0xff + 2
            4.toByte() -> 18                 // ipv6
            else -> error("bad socks reply")
        }
        if (h[3] != 3.toByte()) recv(extra)
    }

    companion object {
        fun randomCreds(): Pair<String, String> {
            val r = SecureRandom()
            val hex = { n: Int -> (1..n).map { "0123456789abcdef"[r.nextInt(16)] }.joinToString("") }
            return hex(16) to "x"            // fresh username = fresh tor circuit
        }
    }
}
