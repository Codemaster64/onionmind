package org.onionmind.core

import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlin.test.Test
import kotlin.test.assertEquals

class AgentStopTest {
    @Test
    fun `a stop requested before the turn starts never touches the network`() {
        val messages = mutableListOf(
            buildJsonObject { put("role", "user"); put("content", "hello") }
        )
        // Port 9 discards everything: if the turn ignored the stop and tried
        // to chat, it would raise here instead of returning the marker.
        val answer = Agent.turn("http://127.0.0.1:9", messages, shouldStop = { true })
        assertEquals("(stopped)", answer)
    }
}
