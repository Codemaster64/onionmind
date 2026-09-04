package org.onionmind.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class PreferencesTest {
    @Test
    fun `empty and absent storage yield the shipped defaults`() {
        assertEquals(WorkbenchPreferences.DEFAULT, WorkbenchPreferences.fromJson(null))
        assertEquals(WorkbenchPreferences.DEFAULT, WorkbenchPreferences.fromJson(""))
        assertEquals(WorkbenchPreferences.DEFAULT, WorkbenchPreferences.fromJson("{}"))
    }

    @Test
    fun `corrupt storage falls back to the defaults`() {
        assertEquals(WorkbenchPreferences.DEFAULT, WorkbenchPreferences.fromJson("{not json"))
        assertEquals(WorkbenchPreferences.DEFAULT, WorkbenchPreferences.fromJson("[1,2]"))
    }

    @Test
    fun `valid values survive a json round trip`() {
        val original = WorkbenchPreferences(
            textScale = "comfortable",
            enterSends = false,
            reduceMotion = "reduced",
        )
        assertEquals(original, WorkbenchPreferences.fromJson(original.toJson()))
    }

    @Test
    fun `junk values fall back to the system defaults`() {
        val parsed = WorkbenchPreferences.fromJson(
            """{"textScale":"enormous","enterSends":"yes","reduceMotion":7}"""
        )
        assertEquals("system", parsed.textScale)
        assertTrue(parsed.enterSends)  // a non-boolean keeps the default
        assertEquals("system", parsed.reduceMotion)
    }

    @Test
    fun `patch accepts valid values and ignores junk`() {
        val current = WorkbenchPreferences.DEFAULT
        assertEquals(
            WorkbenchPreferences(textScale = "compact", enterSends = false, reduceMotion = "full"),
            current.patch(textScale = "compact", enterSends = "false", reduceMotion = "full"),
        )
        assertEquals(
            current,
            current.patch(textScale = "bogus", enterSends = null, reduceMotion = "nope"),
        )
        // Enter-to-send fails closed to off only for explicit false-ish values.
        assertTrue(current.patch(enterSends = "anything").enterSends)
        assertFalse(current.patch(enterSends = "false").enterSends)
    }

    @Test
    fun `text scale factors match the desktop ladder`() {
        assertEquals(1.0, WorkbenchPreferences.textScaleFactor("system"))
        assertEquals(0.9, WorkbenchPreferences.textScaleFactor("compact"))
        assertEquals(1.15, WorkbenchPreferences.textScaleFactor("comfortable"))
        assertEquals(1.0, WorkbenchPreferences.textScaleFactor("unheard-of"))
    }
}
