package org.onionmind.core

import java.net.URI

/** What a model entry can be read off its own URL.
 *
 *  Adding a model used to mean typing the URL, the filename and the byte size.
 *  Two of those are not independent facts: the filename is the last path
 *  segment, and the size is whatever the server sends. Asking for them was
 *  three chances to get it wrong for one piece of real information.
 *
 *  Lives in :core because it is pure string work and therefore testable on a
 *  desktop JVM - and because it guards a path that writes to disk, it is worth
 *  testing rather than trusting.
 */
object ModelSource {
    // Deliberately strict: this value becomes a filename under the model dir.
    // Anything with a separator, a percent-escape or a parent reference is not
    // "sanitised" into something safe, it is rejected.
    private val FILENAME = Regex("[A-Za-z0-9._-]+")

    /** The filename a URL implies, or null if it does not imply a usable one. */
    fun filenameFrom(url: String): String? {
        val path = try { URI(url).path } catch (_: Exception) { return null } ?: return null
        val last = path.substringAfterLast('/')
        return last.takeIf { it.matches(FILENAME) && it != "." && it != ".." }
    }

    /** A human label for a model file: the name without the weights extension. */
    fun nameFrom(file: String): String =
        file.removeSuffix(".gguf").ifBlank { file }
}
