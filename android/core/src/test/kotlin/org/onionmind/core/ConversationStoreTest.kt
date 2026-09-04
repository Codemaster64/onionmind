package org.onionmind.core

import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.io.File
import kotlin.io.path.createTempDirectory
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

class ConversationStoreTest {
    private fun user(text: String) = buildJsonObject { put("role", "user"); put("content", text) }
    private fun bot(text: String) = buildJsonObject { put("role", "assistant"); put("content", text) }

    @Test
    fun `a missing conversation loads as empty`() {
        val dir = createTempDirectory("onionmind-conv").toFile()
        assertTrue(ConversationStore.load(dir).isEmpty())
    }

    @Test
    fun `a conversation survives a save and reload`() {
        val dir = createTempDirectory("onionmind-conv").toFile()
        val messages = mutableListOf(user("hello"), bot("hi, locally"))
        ConversationStore.save(dir, messages)
        assertEquals(messages, ConversationStore.load(dir))
    }

    @Test
    fun `saving replaces an existing conversation`() {
        val dir = createTempDirectory("onionmind-conv").toFile()
        ConversationStore.save(dir, mutableListOf(user("first")))
        ConversationStore.save(dir, mutableListOf(user("second"), bot("second answer")))
        val reloaded = ConversationStore.load(dir)
        assertEquals(2, reloaded.size)
        assertEquals("second", (reloaded[0]["content"] as? JsonPrimitive)?.content)
    }

    @Test
    fun `a corrupt file loads as empty and heals on the next save`() {
        val dir = createTempDirectory("onionmind-conv").toFile()
        dir.mkdirs()
        File(dir, "conversation.json").writeText("{not json")
        assertTrue(ConversationStore.load(dir).isEmpty())
        ConversationStore.save(dir, mutableListOf(user("after corruption")))
        assertEquals(1, ConversationStore.load(dir).size)
    }
}
