package org.onionmind.core

import com.sun.net.httpserver.HttpServer
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import java.io.File
import java.net.InetSocketAddress
import java.time.Instant
import java.time.ZoneOffset
import java.util.concurrent.ConcurrentLinkedQueue
import kotlin.io.path.createTempDirectory
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

/** The phone's v2 workbench logic: flags, webAsk, presets, errors, fit,
 *  context, the egress log and the per-turn stats read from llama-server. */
class WorkbenchTest {
    @Test fun `flags default to the desktop's choice and ignore junk`() {
        val defaults = Features.fromJson(null)
        assertEquals(Features.KEYS.toSet(), defaults.keys)
        assertTrue(defaults.getValue("webAsk"))
        assertTrue(defaults.filterKeys { it != "webAsk" }.values.none { it })
        val stored = Features.fromJson("""{"provenance":true,"webAsk":false,"bogus":true,"lock":"yes"}""")
        assertTrue(stored.getValue("provenance"))
        assertFalse(stored.getValue("webAsk"))
        assertFalse(stored.getValue("lock"))
        assertFalse("bogus" in stored)
        assertEquals(Features.DEFAULTS, Features.fromJson("{not json"))
        assertEquals(stored, Features.fromJson(Features.toJson(stored)))
    }

    @Test fun `turn all on and off, with single keys winning`() {
        val on = Features.patch(Features.DEFAULTS, mapOf("all" to "on", "lock" to "false"))
        assertTrue(on.filterKeys { it != "lock" }.values.all { it })
        assertFalse(on.getValue("lock"))
        assertTrue(Features.patch(on, mapOf("all" to "off")).values.none { it })
        assertEquals(on, Features.patch(on, mapOf("lock" to "maybe")))
    }

    @Test fun `webAsk matches the desktop heuristic`() {
        assertTrue(WebAsk.needsWeb("What's the latest Firefox release?"))
        assertTrue(WebAsk.needsWeb("Is there a newer version of this?"))
        assertTrue(WebAsk.needsWeb("can you look it up"))
        assertFalse(WebAsk.needsWeb("Explain recursion simply"))
        assertFalse(WebAsk.needsWeb("researching"))   // word boundaries, like Python's \b
        assertEquals("the latest Firefox release", WebAsk.query("What's the latest Firefox release?"))
        assertEquals("a newer version of this", WebAsk.query("Is there a newer version of this?"))
        assertEquals(70, WebAsk.query("news " + "x".repeat(200)).length)
    }

    @Test fun `a preset is one system message first and only when chosen`() {
        val chat = listOf(
            buildJsonObject { put("role", "system"); put("content", "old") },
            buildJsonObject { put("role", "user"); put("content", "hi") },
        )
        val with = Presets.withSystemPrompt(chat, Presets.byId("concise"))
        assertEquals(2, with.size)
        assertEquals("system", with[0]["role"]!!.jsonPrimitive.content)
        assertEquals(Presets.byId("concise")!!.text, with[0]["content"]!!.jsonPrimitive.content)
        assertEquals(listOf("user"), Presets.withSystemPrompt(chat, null).map { it["role"]!!.jsonPrimitive.content })
        assertNull(Presets.byId("nope"))
    }

    @Test fun `errors are sorted from the real failure text`() {
        assertEquals(ErrorKind.SERVER, ErrorKind.classify("Local model server could not start safely"))
        assertEquals(ErrorKind.OOM, ErrorKind.classify("Local model server could not start safely",
            "llama_model_load: error: failed to allocate buffer of size 5200 MiB"))
        assertEquals(ErrorKind.SERVER, ErrorKind.classify("(local model request failed: Connection refused)"))
        assertEquals(ErrorKind.TOR, ErrorKind.classify("(Tor could not start safely; web search was not performed)"))
        assertNull(ErrorKind.classify("(gave up after 6 tool rounds)"))
    }

    @Test fun `fit reproduces the existing ram recommendation`() {
        val blaze = 5_627_045_248L; val ember = 2_707_514_688L; val spark = 1_674_454_432L
        assertEquals("Fits", ModelFit.label(blaze, 11_500))
        assertEquals("Too big", ModelFit.label(blaze, 6_000))
        assertEquals("Fits", ModelFit.label(ember, 7_200))
        assertEquals("Tight", ModelFit.label(ember, 4_800))
        assertEquals("Fits", ModelFit.label(spark, 4_000))
        assertEquals("", ModelFit.label(0, 8_000))
        assertEquals("", ModelFit.label(spark, 0))
    }

    @Test fun `context figures`() {
        assertEquals("900", ContextWindow.format(900))
        assertEquals("3.1k", ContextWindow.format(3_200))
        assertEquals("16k", ContextWindow.format(16_384))
        val msgs = listOf(buildJsonObject { put("role", "user"); put("content", "x".repeat(400)) })
        assertEquals(100, ContextWindow.estimate(msgs))
    }

    @Test fun `a long conversation is cut to the newest whole turns that fit`() {
        fun msg(role: String, n: Int) = buildJsonObject { put("role", role); put("content", "x".repeat(n)) }
        val system = msg("system", 400)                                  // 100 tokens
        val turns = (1..40).flatMap { listOf(msg("user", 2_000), msg("assistant", 2_000)) }   // 1000 tokens a turn
        val sent = ContextWindow.fit(listOf(system) + turns, budget = 8_000)
        assertEquals("system", sent.first()["role"]!!.jsonPrimitive.content)
        assertEquals("user", sent[1]["role"]!!.jsonPrimitive.content)   // cut at a user message
        assertTrue(ContextWindow.estimate(sent) <= 8_000)
        assertEquals(turns.last(), sent.last())                          // newest kept
        assertEquals(1 + 2 * 7, sent.size)                               // 100 + 7 turns of 1000
        // A short conversation goes as it is, and one huge turn is still sent.
        assertEquals(turns.take(4), ContextWindow.fit(turns.take(4), budget = 8_000))
        val huge = listOf(msg("user", 100_000))
        assertEquals(huge, ContextWindow.fit(huge, budget = 8_000))
    }

    @Test fun `the egress log keeps routes and hosts, never request text`() {
        val dir = createTempDirectory("onionmind-egress").toFile()
        val log = EgressLog(File(dir, "egress.jsonl"), maxEntries = 3)
        val noon = Instant.parse("2026-10-11T12:00:00Z")
        log.append(EgressLog.TOR, "duckduckgo onion service", "search", "5 results", noon.minusSeconds(86_400))
        log.append(EgressLog.TOR, "duckduckgo onion service", "search", "3 results", noon)
        log.append(EgressLog.DIRECT, "huggingface.co", "model download", "SPARK\n\"evil\"", noon)
        assertEquals(mapOf(EgressLog.TOR to 1, EgressLog.DIRECT to 1), log.today(ZoneOffset.UTC, noon))
        log.append(EgressLog.TOR, "duckduckgo onion service", "search", "", noon)
        assertEquals(3, log.entries().size)                   // capped
        val json = Json.parseToJsonElement(log.toJson()).jsonArray
        assertEquals("huggingface.co", json[1].jsonObject["destination"]!!.jsonPrimitive.content)
        assertFalse(log.toCsv().contains("\n\"evil\""))      // newlines and quotes are dropped on write
        assertTrue(log.toCsv().startsWith("time,route,destination,kind,detail\n"))
        log.clear()
        assertTrue(log.entries().isEmpty())
    }

    private class LlamaStub(vararg replies: String) : AutoCloseable {
        private val responses = ConcurrentLinkedQueue(replies.toList())
        private val server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0).apply {
            createContext("/v1/chat/completions") { exchange ->
                exchange.requestBody.readBytes()
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

    private fun conversation(): MutableList<JsonObject> =
        mutableListOf(buildJsonObject { put("role", "user"); put("content", "hello") })

    @Test fun `turn stats come from the server's own timings and usage`() {
        val reply = """{"choices":[{"finish_reason":"stop","message":{"role":"assistant",
            "content":"<think>private</think>Hi there","reasoning_content":null}}],
            "usage":{"prompt_tokens":40,"completion_tokens":9},
            "timings":{"prompt_n":12,"cache_n":28,"predicted_n":9,"predicted_per_second":7.25}}"""
        LlamaStub(reply).use { llama ->
            val stats = TurnStats()
            assertEquals("Hi there", Agent.turn(llama.url, conversation(), stats = stats))
            assertEquals(40, stats.promptTokens)
            assertEquals(9, stats.completionTokens)
            assertEquals(49, stats.contextTokens)
            assertEquals(7.25, stats.tokensPerSecond)
            assertTrue(stats.reasoningHidden)
            assertTrue(stats.elapsedMs >= 0)
        }
    }

    @Test fun `missing counts stay unknown and searches are recorded by the caller`() {
        val call = """{"choices":[{"message":{"role":"assistant","content":null,"tool_calls":[{"id":"c","type":"function","function":{"name":"web_search","arguments":"{\"query\":\"q\"}"}}]}}]}"""
        val done = """{"choices":[{"finish_reason":"stop","message":{"role":"assistant","content":"done"}}]}"""
        LlamaStub(call, done).use { llama ->
            val stats = TurnStats()
            Agent.turn(llama.url, conversation(), allowSearch = true, stats = stats) {
                stats.recordSearch(listOf(
                    Triple("a", "", "https://www.example.org/x"),
                    Triple("b", "", "https://docs.example.com/y"),
                    Triple("c", "", "https://example.org/z")), null)
                "results"
            }
            assertNull(stats.promptTokens)
            assertNull(stats.tokensPerSecond)
            assertFalse(stats.reasoningHidden)
            val search = stats.searches.single()
            assertTrue(search.ok)
            assertEquals(3, search.results)
            assertEquals(listOf("example.org", "docs.example.com"), search.domains)
        }
        val failed = TurnStats().apply { recordSearch(emptyList(), "HTTP 503") }
        assertFalse(failed.searches.single().ok)
    }

    @Test fun `the page, the phone core and the desktop share one webAsk pattern`() {
        // The WebView decides when to ask, so its copy must not drift from
        // WebAsk here or WEB_ASK_PATTERN in the desktop core.
        val repo = File(System.getProperty("onionmind.fixtures")).parentFile
        val page = File(repo, "android/app/src/main/assets/index.html").readText()
        val desktop = File(repo, "onionmind_desktop_core.py").readText()
        val words = "(latest|newest|newer|current(ly)?|today|this week|news|release[sd]?|version|" +
            "changelog|look (it )?up|search|online|price|docs? for)"
        assertTrue(page.contains("const WEB_ASK = /\\b$words\\b/i;"), "index.html WEB_ASK drifted")
        assertTrue(desktop.replace("\"\n    r\"", "").contains(words), "desktop WEB_ASK_PATTERN drifted")
        assertTrue(WebAsk.needsWeb("is there a newer release"))
    }

    @Test fun `the meter's limit is the context llama-server is started with`() {
        val repo = File(System.getProperty("onionmind.fixtures")).parentFile
        val manager = File(repo, "android/app/src/main/java/org/onionmind/app/ProcessManager.kt").readText()
        assertTrue(manager.contains("\"-c\", \"${ContextWindow.TOKENS}\""), "ProcessManager -c drifted from ContextWindow.TOKENS")
    }
}
