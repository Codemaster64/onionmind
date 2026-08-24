package org.onionmind.core

/**
 * Tracks one child process that is expected to own a loopback listener.
 *
 * A listening port is not ownership evidence on Android because every app
 * shares the same loopback interface. The port is considered ready only while
 * the exact child launched by this instance remains alive. State transitions
 * are serialized so concurrent requests cannot launch or adopt another child.
 */
class OwnedLoopbackProcess(
    private val listenerOpen: () -> Boolean,
    private val pause: (Long) -> Unit = { Thread.sleep(it) },
    private val nanoTime: () -> Long = System::nanoTime,
) {
    private val lock = Any()
    private var child: Process? = null

    /** Reuse our live child, reject an unowned listener, or launch a new child. */
    fun ensure(launch: () -> Process?): Boolean = synchronized(lock) {
        val current = child
        if (current?.isAlive == true) return@synchronized true
        child = null
        if (listenerOpen()) return@synchronized false

        val started = try {
            launch()
        } catch (_: Exception) {
            null
        } ?: return@synchronized false
        if (!started.isAlive) {
            started.destroy()
            return@synchronized false
        }
        child = started
        true
    }

    /** Wait until the same owned child is alive and its listener is reachable. */
    fun awaitReady(timeoutMillis: Long = 90_000): Boolean {
        val owned = synchronized(lock) { child?.takeIf { it.isAlive } } ?: return false
        val timeoutNanos = timeoutMillis.coerceAtLeast(0)
            .coerceAtMost(Long.MAX_VALUE / 1_000_000) * 1_000_000
        val startedAt = nanoTime()
        while (nanoTime() - startedAt < timeoutNanos) {
            if (!ownsLiveChild(owned)) return false
            if (listenerOpen() && ownsLiveChild(owned)) return true
            try {
                pause(250)
            } catch (_: InterruptedException) {
                Thread.currentThread().interrupt()
                return false
            }
        }
        return ownsLiveChild(owned) && listenerOpen() && ownsLiveChild(owned)
    }

    fun ready(): Boolean {
        val owned = synchronized(lock) { child } ?: return false
        return ownsLiveChild(owned) && listenerOpen() && ownsLiveChild(owned)
    }

    /** Destroy only the child this instance launched; never act on a port owner. */
    fun stop() {
        val owned = synchronized(lock) {
            val current = child
            child = null
            current
        }
        owned?.destroy()
    }

    private fun ownsLiveChild(owned: Process): Boolean = synchronized(lock) {
        child === owned && owned.isAlive
    }
}
