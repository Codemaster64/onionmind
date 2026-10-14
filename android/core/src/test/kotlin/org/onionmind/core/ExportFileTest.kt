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
        File("build/export-from-jvm.onionmind").writeBytes(ExportFile.write(password, ExportFile.fromConversation(listOf(
            buildJsonObject { put("role", "user"); put("content", "made on the JVM") },
            buildJsonObject { put("role", "assistant"); put("content", "and opened on the desktop") },
        ))))
    }

    @Test fun thePhonesConversationTravelsAsOneDesktopSession() {
        val messages = listOf(
            buildJsonObject { put("role", "user"); put("content", "Plan a trip\nto Bergen") },
            buildJsonObject { put("role", "assistant"); put("content", "Take the train."); put("_meta", "kept here") },
            buildJsonObject { put("role", "tool"); put("content", "dropped") },
        )
        val payload = ExportFile.fromConversation(messages, java.time.Instant.parse("2026-10-14T12:00:00Z"))
        val session = payload["sessions"]!!.jsonArray.single().jsonObject
        assertEquals("Plan a trip", session["title"]!!.jsonPrimitive.content)
        assertEquals(Regex("android-[0-9a-f]{32}").matches(session["id"]!!.jsonPrimitive.content), true)
        assertEquals(2, session["messages"]!!.jsonArray.size)
        assertFalse(session["messages"].toString().contains("_meta"))
        // Same turns, same id: importing twice on the desktop adds it once.
        assertEquals(session["id"], ExportFile.fromConversation(messages)["sessions"]!!.jsonArray[0].jsonObject["id"])
        val (back, total) = ExportFile.newestConversation(ExportFile.read(ExportFile.write(password, payload), password))!!
        assertEquals(listOf("user", "assistant"), back.map { it["role"]!!.jsonPrimitive.content })
        assertEquals(1, total)
        assertEquals(0, ExportFile.fromConversation(emptyList())["sessions"]!!.jsonArray.size)
    }

    @Test fun thePhoneTakesTheNewestLiveSessionFromADesktopExport() {
        fun session(id: String, updated: String, archived: String?) = buildJsonObject {
            put("id", id); put("updated_at", updated); put("archived_at", archived)
            put("messages", kotlinx.serialization.json.JsonArray(listOf(buildJsonObject { put("role", "user"); put("content", id) })))
        }
        val payload = buildJsonObject {
            put("sessions", kotlinx.serialization.json.JsonArray(listOf(
                session("old", "2026-01-01", null), session("newest-archived", "2026-12-01", "2026-12-02"),
                session("new", "2026-06-01", null))))
        }
        val (turns, total) = ExportFile.newestConversation(payload)!!
        assertEquals("new", turns.single()["content"]!!.jsonPrimitive.content)
        assertEquals(3, total)
        val sample = ExportFile.read(File(fixtures, "export-sample.onionmind").readBytes(), password)
        assertEquals(2, ExportFile.newestConversation(sample)!!.first.size)
    }
}
