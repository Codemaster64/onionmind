package org.onionmind.core

import com.sun.net.httpserver.HttpServer
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import java.net.InetSocketAddress
import java.util.concurrent.ConcurrentLinkedQueue
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/** Offline contracts for exact-turn web-search permission. */
class AgentPrivacyTest {
    private class LlamaStub(vararg replies: String) : AutoCloseable {
        val requests = mutableListOf<String>()
        private val responses = ConcurrentLinkedQueue(replies.toList())
        private val server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0).apply {
            createContext("/v1/chat/completions") { exchange ->
                val request = exchange.requestBody.bufferedReader().use { it.readText() }
                synchronized(requests) { requests.add(request) }
                val bytes = responses.remove().toByteArray(Charsets.UTF_8)
                exchange.responseHeaders.add("Content-Type", "application/json")
                exchange.sendResponseHeaders(200, bytes.size.toLong())
                exchange.responseBody.use { it.write(bytes) }
            }
            start()
        }

        val url = "http://127.0.0.1:${server.address.port}"
        override fun close() = server.stop(0)
    }

    private fun messages(): MutableList<JsonObject> = mutableListOf(buildJsonObject {
        put("role", "user")
        put("content", "hello")
    })

    @Test fun toolsAreOmittedWithoutOneTurnPermission() {
        LlamaStub(answer("local only")).use { llama ->
            var searches = 0
            val result = Agent.turn(llama.url, messages(), allowSearch = false) {
                searches++
                "should not run"
            }
            assertEquals("local only", result)
            assertEquals(0, searches)
            assertFalse(llama.requests.single().contains("\"tools\""))
        }
    }

    @Test fun spuriousSearchCallIsDeniedWithoutNetwork() {
        val messages = messages()
        LlamaStub(toolCall("private query"), answer("finished locally")).use { llama ->
            var searches = 0
            val result = Agent.turn(llama.url, messages, allowSearch = false) {
                searches++
                "network result"
            }
            assertEquals("finished locally", result)
            assertEquals(0, searches)
            assertTrue(llama.requests.all { !it.contains("\"tools\"") })
            val refusal = messages.first { it["role"]?.jsonPrimitive?.content == "tool" }
            assertEquals(
                "(web search was not allowed for this turn)",
                refusal["content"]?.jsonPrimitive?.content,
            )
        }
    }

    @Test fun toolsArePresentOnlyWhenThisCallAllowsSearch() {
        LlamaStub(answer("permission scoped")).use { llama ->
            assertEquals(
                "permission scoped",
                Agent.turn(llama.url, messages(), allowSearch = true) { "unused" },
            )
            assertTrue(llama.requests.single().contains("\"tools\""))
        }
    }

    private fun answer(text: String) =
        """{"choices":[{"message":{"role":"assistant","content":"$text"}}]}"""

    private fun toolCall(query: String) =
        """{"choices":[{"message":{"role":"assistant","content":null,"tool_calls":[{"id":"call-1","type":"function","function":{"name":"web_search","arguments":"{\"query\":\"$query\"}"}}]}}]}"""
}
