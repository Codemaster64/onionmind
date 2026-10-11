package org.onionmind.core

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.put
import java.io.File
import java.net.URI
import java.time.Instant
import java.time.ZoneId
import java.time.ZonedDateTime

/**
 * The phone's share of the desktop's v2 workbench features (see
 * docs/android-v2.md for which ones fit a phone and why). Same keys as the
 * desktop core's FEATURE_FLAGS so the two can be read side by side, and the
 * same default: everything off except the one that makes the app ask before
 * guessing. Pure logic, no Android imports, tested on a desktop JVM.
 */
object Features {
    data class Flag(val key: String, val name: String, val description: String)

    val FLAGS = listOf(
        Flag("webAsk", "Ask before searching the web",
            "When a question needs the web and Tor search is not allowed for the turn, Onionmind asks first instead of guessing."),
        Flag("provenance", "Where replies came from",
            "Each reply says it was answered on this phone, by which model, and whether a Tor search was used."),
        Flag("thinking", "Reply details",
            "Says when the model's reasoning was hidden, and how long the answer took."),
        Flag("modelPlus", "Live model stats",
            "Shows the last reply's speed and the phone's free memory under the header."),
        Flag("contextMeter", "Context-window meter",
            "Shows how much of the model's context this conversation uses, and warns before it is full."),
        Flag("ladder", "Model fit and loading",
            "The model list says what fits this phone's memory, and switching loads the model right away."),
        Flag("boundary", "Boundary map",
            "A map of what stays on this phone, what leaves over Tor and what goes direct."),
        Flag("netlog", "Network log export",
            "Keeps a log of every connection that left this phone (never what you asked) and shares it as JSON or CSV."),
        Flag("msgActions", "Edit and regenerate",
            "Edit and resend any of your messages, or ask again for a new answer."),
        Flag("presets", "Presets",
            "Pick a system prompt such as Concise or Explain step by step above the composer."),
        Flag("errors", "Error and recovery",
            "Clear messages with fix-it buttons when the model cannot start, memory runs out or a Tor search fails."),
        Flag("empty", "Starter prompts",
            "Suggestions to start from when the conversation is empty."),
        Flag("lock", "Auto-delete and private screen",
            "Delete the conversation after a set time, and hide it from the app switcher and screenshots."),
        Flag("appearance", "Light theme",
            "Adds a light theme alongside the dark one."),
    )

    val KEYS: List<String> = FLAGS.map { it.key }
    val DEFAULTS: Map<String, Boolean> = KEYS.associateWith { it == "webAsk" }

    /** Stored flags over the defaults; unknown keys and non-booleans are ignored. */
    fun fromJson(raw: String?): Map<String, Boolean> {
        val stored = try {
            Json.parseToJsonElement(raw?.takeIf { it.isNotBlank() } ?: "{}") as? JsonObject
        } catch (_: Exception) { null } ?: JsonObject(emptyMap())
        return KEYS.associateWith { key ->
            (stored[key] as? JsonPrimitive)?.booleanOrNull ?: DEFAULTS.getValue(key)
        }
    }

    fun toJson(flags: Map<String, Boolean>): String = buildJsonObject {
        for (key in KEYS) put(key, flags[key] ?: DEFAULTS.getValue(key))
    }.toString()

    /** A form update: "true"/"false" per known key, or all=on|off. */
    fun patch(current: Map<String, Boolean>, values: Map<String, String?>): Map<String, Boolean> {
        val all = when (values["all"]) { "on", "true" -> true; "off", "false" -> false; else -> null }
        return KEYS.associateWith { key ->
            when (values[key]) {
                "true", "1", "on" -> true
                "false", "0", "off" -> false
                else -> all ?: current[key] ?: DEFAULTS.getValue(key)
            }
        }
    }

    fun catalogJson(): JsonArray = buildJsonArray {
        for (flag in FLAGS) add(buildJsonObject {
            put("key", flag.key); put("name", flag.name); put("description", flag.description)
        })
    }
}

/**
 * webAsk: the prompt shapes that need the web. A heuristic on the user's own
 * words, run on the phone - it decides only whether to ASK, never to search.
 * Kept identical to WEB_ASK_PATTERN in onionmind_desktop_core.py.
 */
object WebAsk {
    private val PATTERN = Regex(
        "\\b(latest|newest|newer|current(ly)?|today|this week|news|release[sd]?|version|" +
            "changelog|look (it )?up|search|online|price|docs? for)\\b",
        RegexOption.IGNORE_CASE,
    )
    private val LEAD = Regex(
        "^(is there|are there|can you|could you|please|what('s| is)|do you know)\\s+",
        RegexOption.IGNORE_CASE,
    )

    fun needsWeb(text: String): Boolean = PATTERN.containsMatchIn(text)

    /** The one query the card shows: the prompt minus its framing. */
    fun query(text: String): String =
        LEAD.replaceFirst(text.trim().replace(Regex("[?.!]+$"), ""), "").take(70)
}

/**
 * presets: a system prompt sent before the conversation. Sent only while the
 * presets feature is on, so with it off the request is exactly what the app
 * always sent. Adapted from the desktop's PRESET_DEFAULTS for a phone.
 */
object Presets {
    data class Preset(val id: String, val name: String, val short: String, val text: String)

    val DEFAULTS = listOf(
        Preset("default", "Default", "Balanced answers for everyday questions",
            "You are Onionmind, a private assistant running on this phone. Answer clearly and say when you are unsure."),
        Preset("concise", "Concise", "Short answers, no extra explanation",
            "Answer in as few words as possible. Use a list only when there are several steps."),
        Preset("teach", "Explain step by step", "Walks through the reasoning",
            "Explain step by step, as if to someone new to the topic. Define terms the first time you use them."),
        Preset("review", "Reviewer", "Looks for mistakes and risks first",
            "Review what you are given like a careful expert. Point out mistakes and risks first, then smaller issues. Quote the parts you mean."),
    )

    fun byId(id: String?): Preset? = DEFAULTS.firstOrNull { it.id == id }

    /** The request with the preset's system message first; any earlier system message is replaced. */
    fun withSystemPrompt(messages: List<JsonObject>, preset: Preset?): MutableList<JsonObject> {
        val rest = messages.filterNot { (it["role"] as? JsonPrimitive)?.contentOrNull == "system" }
        if (preset == null) return rest.toMutableList()
        return (listOf(buildJsonObject { put("role", "system"); put("content", preset.text) }) + rest)
            .toMutableList()
    }

    fun catalogJson(): JsonArray = buildJsonArray {
        for (p in DEFAULTS) add(buildJsonObject {
            put("id", p.id); put("name", p.name); put("short", p.short)
        })
    }
}

/** errors: sort a real failure into something the UI can offer a fix for. */
object ErrorKind {
    const val OOM = "oom"
    const val SERVER = "server"
    const val TOR = "tor"

    private val OOM_TEXT = Regex(
        "out of memory|failed to allocate|cannot allocate|not enough memory|" +
            "insufficient memory|std::bad_alloc|unable to allocate|lowmemorykiller",
        RegexOption.IGNORE_CASE,
    )
    private val SERVER_TEXT = Regex(
        "could not start safely|local model request failed|llama-server returned|" +
            "connection refused|failed to connect",
        RegexOption.IGNORE_CASE,
    )
    private val TOR_TEXT = Regex(
        "tor could not start|search failed on the onion service",
        RegexOption.IGNORE_CASE,
    )

    /**
     * [message] is the error the chat returned; [serverLog] the tail of
     * llama-server's own log, which is where an allocation failure is
     * actually written - the chat only sees that the server went away.
     */
    fun classify(message: String, serverLog: String = ""): String? = when {
        OOM_TEXT.containsMatchIn(message) -> OOM
        // Before SERVER: "Tor could not start safely" also says "could not start".
        TOR_TEXT.containsMatchIn(message) -> TOR
        SERVER_TEXT.containsMatchIn(message) ->
            if (OOM_TEXT.containsMatchIn(serverLog)) OOM else SERVER
        else -> null
    }
}

/** ladder: does a model fit this phone's memory? An estimate, labelled as one. */
object ModelFit {
    // Android keeps much of the RAM for itself and other apps, so a phone
    // model gets far less of it than a desktop's 85%. 60% reproduces the
    // existing recommendation (BLAZE from 11 GB, EMBER from 7 GB).
    private const val BUDGET_SHARE = 0.60
    private const val KV_HEADROOM = 1.2

    fun label(bytes: Long, totalRamMb: Long): String {
        if (bytes <= 0 || totalRamMb <= 0) return ""
        val needMb = bytes / (1024.0 * 1024.0) * KV_HEADROOM
        val budget = totalRamMb * BUDGET_SHARE
        return when {
            needMb <= budget -> "Fits"
            needMb <= budget * 1.35 -> "Tight"
            else -> "Too big"
        }
    }
}

/** contextMeter: the model runs with this context (llama-server -c). */
object ContextWindow {
    const val TOKENS = 16384

    /** Characters / 4: shown with "≈" until the model reports a real count. */
    fun estimate(messages: List<JsonObject>): Int {
        var chars = 0
        for (m in messages) chars += (m["content"] as? JsonPrimitive)?.contentOrNull?.length ?: 0
        return (chars + 3) / 4
    }

    /** "3.1k", "16k": k is 1024 tokens, the unit context windows are sized in. */
    fun format(count: Int): String = when {
        count < 1000 -> count.toString()
        count / 1024.0 < 10 -> String.format(java.util.Locale.ROOT, "%.1fk", count / 1024.0)
        else -> "${Math.round(count / 1024.0)}k"
    }
}

/**
 * What one turn really did, for provenance, thinking and modelPlus. Filled
 * in by [Agent.turn] from llama-server's own timings and by the caller's
 * search wrapper; anything the server did not report stays null.
 */
class TurnStats {
    data class Search(val ok: Boolean, val results: Int, val domains: List<String>)

    val searches = mutableListOf<Search>()
    var promptTokens: Int? = null
    var completionTokens: Int? = null
    var tokensPerSecond: Double? = null
    var reasoningHidden = false
    var elapsedMs: Long = 0

    fun recordSearch(hits: List<Triple<String, String, String>>, error: String?) {
        val domains = hits.mapNotNull { hostOf(it.third) }.distinct().take(5)
        searches.add(Search(error == null && hits.isNotEmpty(), hits.size, domains))
    }

    /** Tokens the conversation occupies after this turn, if the server said. */
    val contextTokens: Int?
        get() = promptTokens?.let { it + (completionTokens ?: 0) }

    fun toJson(): JsonObject = buildJsonObject {
        put("searches", buildJsonArray {
            for (s in searches) add(buildJsonObject {
                put("ok", s.ok); put("results", s.results)
                put("domains", buildJsonArray { s.domains.forEach { add(JsonPrimitive(it)) } })
            })
        })
        promptTokens?.let { put("promptTokens", it) }
        completionTokens?.let { put("completionTokens", it) }
        contextTokens?.let { put("contextTokens", it) }
        tokensPerSecond?.let { put("tokensPerSecond", Math.round(it * 10) / 10.0) }
        put("reasoningHidden", reasoningHidden)
        put("elapsedMs", elapsedMs)
        put("contextLimit", ContextWindow.TOKENS)
    }

    companion object {
        fun hostOf(url: String): String? = try {
            URI(url).host?.removePrefix("www.")?.takeIf { it.isNotBlank() }
        } catch (_: Exception) { null }
    }
}

/**
 * netlog and boundary: every connection that left the phone - when, which
 * route, which host and what kind of request. NEVER what was asked: no query
 * text, no URL path. Written only while netlog or boundary is on, so the
 * default app keeps no such record (same choice as the desktop).
 */
class EgressLog(private val file: File, private val maxEntries: Int = 1000) {
    data class Entry(val time: Instant, val route: String, val destination: String, val kind: String, val detail: String)

    companion object {
        const val TOR = "tor"
        const val DIRECT = "direct"
        private val SAFE = Regex("[^A-Za-z0-9 .,:()+/_-]")
        private fun clean(text: String) = SAFE.replace(text, "").take(120)
    }

    @Synchronized
    fun append(route: String, destination: String, kind: String, detail: String = "", now: Instant = Instant.now()) {
        val line = buildJsonObject {
            put("time", now.toString()); put("route", clean(route))
            put("destination", clean(destination)); put("kind", clean(kind)); put("detail", clean(detail))
        }.toString()
        file.parentFile?.mkdirs()
        val lines = (if (file.isFile) file.readLines() else emptyList()) + line
        val tmp = File(file.path + ".tmp")
        tmp.writeText(lines.takeLast(maxEntries).joinToString("\n", postfix = "\n"))
        if (!tmp.renameTo(file)) { file.delete(); tmp.renameTo(file) }
    }

    @Synchronized
    fun entries(): List<Entry> {
        if (!file.isFile) return emptyList()
        return file.readLines().mapNotNull { line ->
            try {
                val o = Json.parseToJsonElement(line).jsonObject
                fun s(k: String) = (o[k] as? JsonPrimitive)?.contentOrNull.orEmpty()
                Entry(Instant.parse(s("time")), s("route"), s("destination"), s("kind"), s("detail"))
            } catch (_: Exception) { null }
        }
    }

    /** Counts per route since local midnight. */
    fun today(zone: ZoneId = ZoneId.systemDefault(), now: Instant = Instant.now()): Map<String, Int> {
        val midnight = ZonedDateTime.ofInstant(now, zone).toLocalDate().atStartOfDay(zone).toInstant()
        return entries().filter { !it.time.isBefore(midnight) }.groupingBy { it.route }.eachCount()
    }

    fun toJson(): String = buildJsonArray {
        for (e in entries()) add(buildJsonObject {
            put("time", e.time.toString()); put("route", e.route)
            put("destination", e.destination); put("kind", e.kind); put("detail", e.detail)
        })
    }.toString()

    fun toCsv(): String = buildString {
        append("time,route,destination,kind,detail\n")
        for (e in entries()) append(listOf(e.time.toString(), e.route, e.destination, e.kind, e.detail)
            .joinToString(",") { "\"" + it.replace("\"", "\"\"") + "\"" }).append('\n')
    }

    @Synchronized
    fun clear() { file.delete() }
}
