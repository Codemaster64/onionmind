package org.onionmind.core

import kotlinx.serialization.json.*
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

class AgentConfigTest {

    @Test fun reasoningBudgetExceedsTheExhaustedCeiling() {
        assertTrue(
            Agent.NUM_PREDICT > 8192,
            "reasoning models can spend the old 8192-token budget before answering",
        )
    }

    @Test fun recoveryBudgetIsBounded() {
        assertTrue(Agent.FINAL_NUM_PREDICT < Agent.NUM_PREDICT)
    }

    @Test fun exhaustedReasoningIsCompressedIntoSavedHistory() {
        val server = MockWebServer()
        server.enqueue(MockResponse().setBody("""
            {"choices":[{"finish_reason":"length","message":{
              "role":"assistant","content":null,"reasoning_content":"derived useful state"
            }}]}
        """.trimIndent()))
        server.enqueue(MockResponse().setBody("""
            {"choices":[{"finish_reason":"stop","message":{
              "role":"assistant","content":"Compact recovered answer."
            }}]}
        """.trimIndent()))
        server.start()
        try {
            val history = mutableListOf(buildJsonObject {
                put("role", "user")
                put("content", "Solve the long task")
            })
            val answer = Agent.turn(server.url("/").toString().removeSuffix("/"), history)
            assertEquals("Compact recovered answer.", answer)
            assertEquals(2, server.requestCount)
            val retry = Json.parseToJsonElement(server.takeRequest().body.readUtf8())
            val recovery = Json.parseToJsonElement(server.takeRequest().body.readUtf8()).jsonObject
            assertTrue(retry is JsonObject)
            assertEquals(Agent.FINAL_NUM_PREDICT,
                recovery["max_tokens"]!!.jsonPrimitive.int)
            assertEquals(false, recovery["chat_template_kwargs"]!!.jsonObject
                ["enable_thinking"]!!.jsonPrimitive.boolean)
            assertTrue(recovery["messages"]!!.jsonArray.any {
                it.jsonObject["reasoning_content"]?.jsonPrimitive?.content == "derived useful state"
            })
            assertEquals(2, history.size)
            assertEquals(answer, history.last()["content"]!!.jsonPrimitive.content)
            assertTrue("reasoning_content" !in history.last())
        } finally {
            server.shutdown()
        }
    }
}
