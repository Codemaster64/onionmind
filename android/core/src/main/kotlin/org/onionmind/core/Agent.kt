package org.onionmind.core

import kotlinx.serialization.json.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.FormBody
import okhttp3.Response
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Proxy
import java.util.concurrent.TimeUnit

/**
 * The search agent, ported line-for-line in spirit from onionmind.py: fails
 * closed without tor, one fresh circuit per attempt, the .onion DuckDuckGo
 * endpoint first, per-block result parsing, thinking-stripped answers.
 */
object Agent {

    // Tor Browser's own UA. A unique UA is a fingerprint; blending in is the point.
    const val UA =
        "Mozilla/5.0 (Windows NT 10.0; rv:128.0) Gecko/20100101 Firefox/128.0"

    // Onion first: it never leaves the Tor network (no exit sees the query),
    // and the clearnet endpoint 403s most tor exits anyway.
    val ENDPOINTS = listOf(
        "https://duckduckgogg42xjoc72x3sjasowoarfbgcmvfimaftt6twagswzczad.onion/html/",
        "https://html.duckduckgo.com/html/",
    )

    const val NUM_PREDICT = 16384  // reasoning models spend the budget thinking first

    val TOOLS = """[{"type":"function","function":{
        "name":"web_search",
        "description":"Search the web for current information. Use for anything recent, factual, or that you are unsure about. Returns titles, snippets and URLs. Answer from the snippets rather than searching repeatedly.",
        "parameters":{"type":"object","required":["query"],
                      "properties":{"query":{"type":"string","description":"search terms"}}}}}]"""

    private val json = Json { ignoreUnknownKeys = true }

    private fun client(user: String, pass: String): OkHttpClient {
        // okhttp layers TLS itself over whatever socket the factory hands it,
        // so a SOCKS5-auth socket below HTTPS just works.
        val factory = object : javax.net.SocketFactory() {
            // okhttp uses the no-arg createSocket() then connect(); the rest
            // are abstract on SocketFactory and must exist to compile
            private fun fresh() = Socks5Socket(InetSocketAddress("127.0.0.1", socksPort), user, pass)
            override fun createSocket() = fresh()
            override fun createSocket(host: String?, port: Int) = fresh()
            override fun createSocket(host: String?, port: Int, localHost: InetAddress?, localPort: Int) = fresh()
            override fun createSocket(address: InetAddress?, port: Int) = fresh()
            override fun createSocket(address: InetAddress?, port: Int, localAddress: InetAddress?, localPort: Int) = fresh()
        }
        return OkHttpClient.Builder()
            .socketFactory(factory)
            .proxy(Proxy.NO_PROXY)          // never the system proxy
            .connectTimeout(30, TimeUnit.SECONDS)
            .readTimeout(90, TimeUnit.SECONDS)
            .build()
    }

    @Volatile var socksPort: Int = 9050    // termux/daemon port; 9150 = Tor Browser

    /** Fails closed: verify the SOCKS port really is tor, or return null. */
    fun torCheck(ports: List<Int> = listOf(9050, 9150)): String? {
        for (port in ports) {
            socksPort = port
            val http = client("probe", "x")
            try {
                http.newCall(
                    Request.Builder().url("https://check.torproject.org/api/ip").build()
                ).execute().use { r ->
                    val body = r.body?.string()
                    if (body != null && body.contains("\"IsTor\":true"))
                        return Regex("\"IP\":\"([^\"]+)\"").find(body)?.groupValues?.get(1) ?: "?"
                }
            } catch (_: Exception) { /* try next port */ }
            finally { retire(http) }
        }
        return null
    }

    /**
     * One search attempt = one fresh tor circuit (random SOCKS credentials).
     * A 200 with zero parseable results is treated as a failure and retried,
     * same as onionmind.py.
     */
    /** Shut a per-circuit client down for good.
     *
     *  Each search needs its OWN connection pool - sharing one would let a
     *  later search reuse an earlier circuit's socket, which is exactly the
     *  linkability this whole class exists to prevent. The cost is that every
     *  client leaks a pool (with its 5-minute cleanup thread) and a dispatcher
     *  unless it is explicitly retired, so retire it. */
    private fun retire(c: OkHttpClient) {
        try {
            c.dispatcher.executorService.shutdown()
            c.connectionPool.evictAll()
            c.cache?.close()
        } catch (_: Exception) { /* teardown must never fail a search */ }
    }

    fun webSearch(query: String, n: Int = 5): String {
        var err: String? = null
        for (url in ENDPOINTS) {
            repeat(2) {
                val (u, p) = Socks5Socket.randomCreds()
                val http = client(u, p)
                try {
                    http.newCall(
                        Request.Builder().url(url)
                            .header("User-Agent", UA)
                            .post(FormBody.Builder().add("q", query).build())
                            .build()
                    ).execute().use { resp ->              // .use: close on every path
                        if (!resp.isSuccessful) { err = "HTTP ${resp.code}"; return@repeat }
                        val hits = parseResults(resp.body?.string() ?: "", n)
                        if (hits.isEmpty()) { err = "empty result page"; return@repeat }
                        System.err.println("[tor] searched \"$query\" -> ${hits.size} results")
                        return hits.joinToString("\n") { "- ${it.first}\n  ${it.second}\n  ${it.third}" }
                    }
                } catch (e: Exception) { err = e.message }
                finally { retire(http) }
            }
        }
        return "(search failed after trying both endpoints on fresh circuits: $err)"
    }

    /** Per-result-BLOCK parsing, ported from onionmind.py's parse_results:
     *  a result without a snippet must not shift later snippets onto the
     *  wrong titles - that failure is silent and mismatches citations. */
    fun parseResults(page: String, n: Int = 5): List<Triple<String, String, String>> {
        val out = mutableListOf<Triple<String, String, String>>()
        val seen = mutableSetOf<String>()
        val blocks = Regex("<div[^>]*\\bclass=\"[^\"]*\\bresult\\b[^\"]*\"").split(page).drop(1)
        for (b in blocks) {
            val m = Regex("result__a[^>]* href=\"([^\"]+)\"[^>]*>(.*?)</a>", RegexOption.DOT_MATCHES_ALL)
                .find(b) ?: continue
            var url = pctDecode(m.groupValues[1])
            if (url.contains("uddg=")) {          // DDG wraps results in a redirector
                val q = Regex("uddg=([^&]+)").find(url)?.groupValues?.get(1)
                if (q != null) url = pctDecode(q)
            }
            if (!url.startsWith("http") || !seen.add(url)) continue
            val ms = Regex("result__snippet[^>]*>(.*?)</a>", RegexOption.DOT_MATCHES_ALL).find(b)
            out.add(Triple(clean(m.groupValues[2]), clean(ms?.groupValues?.get(1) ?: ""), url))
            if (out.size >= n) break
        }
        return out
    }

    /** URLDecoder is WRONG for URLs: it is the form-encoding decoder, so it turns
     *  every '+' into a space and corrupts any result URL containing one. Python's
     *  urllib.parse.unquote - which this is a port of - leaves '+' alone. */
    private fun pctDecode(s: String): String {
        if (!s.contains('%')) return s
        val out = java.io.ByteArrayOutputStream(s.length)
        var i = 0
        while (i < s.length) {
            val c = s[i]
            val hex = if (c == '%' && i + 2 < s.length) s.substring(i + 1, i + 3) else null
            val b = hex?.toIntOrNull(16)
            if (b != null) { out.write(b); i += 3 } else { out.write(c.code); i++ }
        }
        return out.toString("UTF-8")
    }

    /** Mirrors Python's html.unescape closely enough for result text: the named
     *  entities DDG actually emits, plus numeric ones. The old hand-rolled list
     *  missed &#39; and every numeric entity, so they reached the model raw. */
    private fun clean(x: String): String {
        val noTags = Regex("<[^>]+>").replace(x, "")
        val unescaped = Regex("&(#[xX]?[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]*);").replace(noTags) { m ->
            val e = m.groupValues[1]
            when {
                e.startsWith("#x") || e.startsWith("#X") ->
                    e.drop(2).toIntOrNull(16)?.let { String(Character.toChars(it)) } ?: m.value
                e.startsWith("#") ->
                    e.drop(1).toIntOrNull()?.let { String(Character.toChars(it)) } ?: m.value
                else -> NAMED[e] ?: m.value
            }
        }
        // Collapse newlines: web_search emits three lines per result and the DSH
        // adapter strides through them 3 at a time. Kept in step with _clean.
        return Regex("\\s+").replace(unescaped, " ").trim()
    }

    private val NAMED = mapOf(
        "amp" to "&", "lt" to "<", "gt" to ">", "quot" to "\"", "apos" to "'",
        "nbsp" to " ", "hellip" to "\u2026", "mdash" to "\u2014", "ndash" to "\u2013",
        "rsquo" to "\u2019", "lsquo" to "\u2018", "ldquo" to "\u201c", "rdquo" to "\u201d")

    /** Ported from strip_thinking: a truncated monologue is not an answer. */
    fun stripThinking(text: String): String {
        if (text.contains("</think>")) return text.substringAfterLast("</think>").trim()
        if (text.contains("<think>")) return ""
        return text.trim()
    }

    /** llama-server is on loopback, so there is no circuit to isolate and one
     *  shared client is correct - a new one per turn leaked a pool and a
     *  dispatcher on every message. Contrast webSearch, which must NOT share. */
    private val localHttp: OkHttpClient by lazy {
        OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS).readTimeout(1800, TimeUnit.SECONDS).build()
    }

    /** One full user turn against llama-server: chat, tool calls, search, repeat. */
    fun turn(llamaUrl: String, messages: MutableList<JsonObject>,
             search: (String) -> String = { q -> webSearch(q) }): String {
        val http = localHttp
        for (round in 0 until 6) {
            val body = buildJsonObject {
                put("messages", JsonArray(messages))
                put("tools", Json.parseToJsonElement(TOOLS))
                put("stream", false)
                put("max_tokens", NUM_PREDICT)
            }
            val r = try {
                http.newCall(
                    Request.Builder().url("$llamaUrl/v1/chat/completions")
                        .post(body.toString().toRequestBody("application/json; charset=utf-8".toMediaType()))
                        .build()
                ).execute()
            } catch (e: Exception) {
                return "(local model request failed: ${e.message ?: e.javaClass.simpleName})"
            }
            r.use { response ->
                if (!response.isSuccessful) return "(llama-server returned HTTP ${response.code})"
                val wire = try {
                    json.parseToJsonElement(response.body?.string().orEmpty()).jsonObject
                } catch (e: Exception) {
                    return "(llama-server returned invalid JSON: ${e.message ?: "parse error"})"
                }
            val msg = try {
                wire["choices"]!!.jsonArray[0].jsonObject["message"]!!.jsonObject
            } catch (e: Exception) {
                return "(llama-server response missing a chat message: ${e.message ?: "invalid response"})"
            }
            val assistant = buildJsonObject {
                put("role", "assistant")
                put("content", msg["content"] ?: JsonNull)
                val calls = msg["tool_calls"]?.jsonArray
                if (calls != null) {
                    put("tool_calls", JsonArray(calls.mapIndexed { i, c ->
                        val f = c.jsonObject["function"]!!.jsonObject
                        val args = f["arguments"]?.let { a ->
                            // OpenAI ships arguments as a JSON string
                            if (a is JsonPrimitive) try {
                                Json.parseToJsonElement(a.content)
                            } catch (_: Exception) {
                                buildJsonObject { put("raw", a.content) }
                            }
                            else a
                        } ?: buildJsonObject { }
                        buildJsonObject {
                            put("id", "tc$i")
                            put("type", "function")
                            put("function", buildJsonObject {
                                put("name", f["name"]!!.jsonPrimitive.content)
                                put("arguments", args)
                            })
                        }
                    }))
                }
            }
            messages.add(assistant)
            val calls = assistant["tool_calls"]?.jsonArray ?: run {
                val answer = stripThinking((assistant["content"] as? JsonPrimitive)?.content ?: "")
                return if (answer.isEmpty())
                    "(the model spent its whole token budget thinking and never answered)"
                else answer
            }
            for (c in calls) {
                val f = c.jsonObject["function"]!!.jsonObject
                val name = f["name"]!!.jsonPrimitive.content
                val args = f["arguments"]?.jsonObject
                val result = if (name == "web_search")
                    search(args?.get("query")?.jsonPrimitive?.content ?: "")
                else "(unknown tool $name)"
                messages.add(buildJsonObject {
                    put("role", "tool")
                    put("tool_call_id", c.jsonObject["id"]!!.jsonPrimitive.content)
                    put("content", result)
                })
            }
            }
        }
        return "(gave up after 6 tool rounds)"
    }
}
