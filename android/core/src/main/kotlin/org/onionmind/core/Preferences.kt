package org.onionmind.core

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.put

/**
 * Presentation-only workbench preferences, mirrored from the desktop core.
 * Everything defaults to the shipped UI, and the Tor boundary plus the
 * per-turn search permission are deliberately not part of this surface:
 * privacy behavior is never a preference.
 */
data class WorkbenchPreferences(
    val textScale: String = TEXT_SCALE_SYSTEM,
    val enterSends: Boolean = true,
    val reduceMotion: String = MOTION_SYSTEM,
) {
    companion object {
        const val TEXT_SCALE_SYSTEM = "system"
        const val TEXT_SCALE_COMPACT = "compact"
        const val TEXT_SCALE_COMFORTABLE = "comfortable"
        const val MOTION_SYSTEM = "system"
        const val MOTION_REDUCED = "reduced"
        const val MOTION_FULL = "full"

        val TEXT_SCALES = setOf(TEXT_SCALE_SYSTEM, TEXT_SCALE_COMPACT, TEXT_SCALE_COMFORTABLE)
        val MOTIONS = setOf(MOTION_SYSTEM, MOTION_REDUCED, MOTION_FULL)
        val DEFAULT = WorkbenchPreferences()

        /** Multiplier for the UI base font; unknown scales are the system default. */
        fun textScaleFactor(textScale: String): Double = when (textScale) {
            TEXT_SCALE_COMPACT -> 0.9
            TEXT_SCALE_COMFORTABLE -> 1.15
            else -> 1.0
        }

        fun fromJson(raw: String?): WorkbenchPreferences = try {
            val root = Json.parseToJsonElement(raw?.takeIf { it.isNotBlank() } ?: "{}")
            val obj = root as? JsonObject ?: JsonObject(emptyMap())
            WorkbenchPreferences(
                textScale = obj.stringIn("textScale", TEXT_SCALES, TEXT_SCALE_SYSTEM),
                enterSends = (obj["enterSends"] as? JsonPrimitive)?.booleanOrNull ?: true,
                reduceMotion = obj.stringIn("reduceMotion", MOTIONS, MOTION_SYSTEM),
            )
        } catch (_: Exception) {
            DEFAULT
        }

        private fun JsonObject.stringIn(
            name: String,
            choices: Set<String>,
            fallback: String,
        ): String =
            (this[name] as? JsonPrimitive)?.contentOrNull?.takeIf(choices::contains) ?: fallback
    }

    fun toJson(): String = buildJsonObject {
        put("textScale", textScale)
        put("enterSends", enterSends)
        put("reduceMotion", reduceMotion)
    }.toString()

    /**
     * A partial update from the settings screen: valid values win, invalid or
     * missing values keep the current state. Enter-to-send needs the string
     * form because the form API carries everything as text.
     */
    fun patch(
        textScale: String? = null,
        enterSends: String? = null,
        reduceMotion: String? = null,
    ): WorkbenchPreferences = WorkbenchPreferences(
        textScale = textScale?.takeIf(TEXT_SCALES::contains) ?: this.textScale,
        enterSends = when (enterSends) {
            "true", "1", "on" -> true
            "false", "0", "off" -> false
            else -> this.enterSends   // junk keeps the current setting, like the other knobs
        },
        reduceMotion = reduceMotion?.takeIf(MOTIONS::contains) ?: this.reduceMotion,
    )
}
