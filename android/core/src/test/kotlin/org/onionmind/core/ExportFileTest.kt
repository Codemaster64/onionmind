package org.onionmind.core

import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import java.io.File
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse

class ExportFileTest {
    private val fixtures = File(System.getProperty("onionmind.fixtures") ?: "../../tests")
    private val password = "correct horse battery"

    @Test fun readsTheDesktopsSample() {
        // Written by onionmind_desktop_core.export_bytes; test_vault reads it too.
        val payload = ExportFile.read(File(fixtures, "export-sample.onionmind").readBytes(), password)
        assertEquals("desktop", payload["from"]!!.jsonPrimitive.content)
        val session = payload["sessions"]!!.jsonArray[0].jsonObject
        assertEquals("What is the tor port? æøå",
            session["messages"]!!.jsonArray[0].jsonObject["content"]!!.jsonPrimitive.content)
    }

    @Test fun roundTripsAndHidesThePlaintext() {
        val payload = buildJsonObject { put("from", "android"); put("secret", "a private thing") }
        val blob = ExportFile.write(password, payload)
        assertFalse(String(blob, Charsets.ISO_8859_1).contains("a private thing"))
        assertEquals(JsonPrimitive("a private thing"), ExportFile.read(blob, password)["secret"])
    }

    @Test fun refusesWrongPasswordsTamperingAndStrangers() {
        val blob = ExportFile.write(password, buildJsonObject { put("x", 1) })
        assertFailsWith<Vault.WrongPassword> { ExportFile.read(blob, "not it") }
        val weaker = String(blob, Charsets.ISO_8859_1).replace("\"iterations\":600000", "\"iterations\":200000")
        assertFailsWith<Vault.WrongPassword> { ExportFile.read(weaker.toByteArray(Charsets.ISO_8859_1), password) }
        val tooWeak = String(blob, Charsets.ISO_8859_1).replace("\"iterations\":600000", "\"iterations\":1")
        assertFailsWith<ExportFile.Invalid> { ExportFile.read(tooWeak.toByteArray(Charsets.ISO_8859_1), password) }
        assertFailsWith<ExportFile.Invalid> { ExportFile.read("OMENC1\n".toByteArray() + blob, password) }
    }

    @Test fun writesWhatTheDesktopReads() {
        // A phone-made file in build/, for a by-hand check that the desktop opens it.
        File("build").mkdirs()
        File("build/export-from-jvm.onionmind").writeBytes(ExportFile.write(password, buildJsonObject {
            put("version", 1); put("from", "android"); put("note", "made on the JVM")
        }))
    }
}
