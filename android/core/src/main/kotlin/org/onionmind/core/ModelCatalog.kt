package org.onionmind.core

/**
 * Name screen for refusal-removed models. A marker in a model's name or URL
 * is strong evidence its refusals were removed; an absent marker says nothing.
 * The UI must present it exactly that way.
 */
object ModelCatalog {
    val UNCENSORED_MARKERS = listOf(
        "abliterated",
        "abliterate",
        "uncensored",
        "unfiltered",
        "unaligned",
        "unhinged",
        "brainwash",
        "never-resist",
        "no-refusal",
    )

    fun uncensoredMarker(vararg texts: String?): String? {
        for (marker in UNCENSORED_MARKERS) {
            for (text in texts) {
                if (text != null && marker in text.lowercase()) return marker
            }
        }
        return null
    }
}
