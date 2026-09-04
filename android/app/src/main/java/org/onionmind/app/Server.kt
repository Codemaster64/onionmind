package org.onionmind.app

import android.content.Context
import android.content.SharedPreferences
import android.app.ActivityManager
import android.os.StatFs
import fi.iki.elonen.NanoHTTPD
import fi.iki.elonen.NanoHTTPD.IHTTPSession
import fi.iki.elonen.NanoHTTPD.Response
import kotlinx.serialization.json.*
import org.onionmind.core.Agent
import org.onionmind.core.WorkbenchPreferences
import java.net.URLDecoder
import java.security.SecureRandom
import java.util.Base64
import java.util.concurrent.ExecutionException
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/** The app's whole backend: serves the chat page and a tiny JSON API on
 *  127.0.0.1:8081. The WebView talks only to this. */
object Server {
    private const val PORT = 8081
    private const val LLAMA = "http://127.0.0.1:8080"
    private var http: NanoHTTPD? = null
    private val chatLock = Executors.newSingleThreadExecutor()
    private lateinit var ctx: Context

    private val random = SecureRandom()
    private fun secret(): String = ByteArray(32).also { random.nextBytes(it) }.let {
        Base64.getUrlEncoder().withoutPadding().encodeToString(it)
    }

    // Android shares loopback across apps: every installed app can connect to
    // 127.0.0.1. The high-entropy page capability gates disclosure of a
    // separate header token, keeping the API token out of the URL; neither is
    // persisted.
    private val apiToken = secret()
    private val pageCapability = secret()
    private val pagePath = "/app/$pageCapability"

    /** The capability stays inside this process and is handed only to our WebView. */
    fun pageUrl(): String = "http://127.0.0.1:$PORT$pagePath"

    fun start(context: Context) {
        if (http != null) return
        ctx = context.applicationContext
        http = object : NanoHTTPD("127.0.0.1", PORT) {
            override fun serve(session: IHTTPSession): Response {
                val response = try {
                    when {
                        session.uri == pagePath -> page()
                        session.uri == "/" -> notFound()
                        session.uri in API_PATHS &&
                            session.headers["x-onionmind-token"] != apiToken ->
                            error(Response.Status.FORBIDDEN, "bad or missing token")
                        session.uri == "/api/status" -> status()
                        session.uri == "/api/preferences" -> preferences(session)
                        session.uri == "/api/stop" -> stopChat()
                        session.uri == "/api/install" -> install(session)
                        session.uri == "/api/select" -> select(session)
                        session.uri == "/api/remove" -> remove(session)
                        session.uri == "/api/chat" -> chat(session)
                        else -> notFound()
                    }
                } catch (e: Exception) {
                    error(Response.Status.INTERNAL_ERROR, rootMessage(e))
                }
                return noStore(response)
            }
        }.also { it.start(NanoHTTPD.SOCKET_READ_TIMEOUT, true) }
    }

    private fun page(): Response {
        val html = ctx.assets.open("index.html").readBytes()
            .toString(Charsets.UTF_8).replace("__ONIONMIND_TOKEN__", apiToken)
            .toByteArray(Charsets.UTF_8)
        return NanoHTTPD.newFixedLengthResponse(
            Response.Status.OK, "text/html", html.inputStream(), html.size.toLong())
    }

    private fun noStore(response: Response): Response = response.apply {
        // Both capabilities change at process start. Cached content could leak
        // the old API token or make the WebView repeatedly fail authentication.
        addHeader("Cache-Control", "no-store")
        addHeader("Pragma", "no-cache")
    }

    private fun notFound(): Response = NanoHTTPD.newFixedLengthResponse(
        Response.Status.NOT_FOUND, "text/plain", "")

    private fun json(body: String): Response =
        NanoHTTPD.newFixedLengthResponse(Response.Status.OK, "application/json", body)

    private fun error(status: Response.Status, message: String): Response =
        NanoHTTPD.newFixedLengthResponse(status, "application/json", buildJsonObject {
            put("error", message)
        }.toString())

    private fun status(): Response {
        val model = ProcessManager.installedModel(ctx)
        val memory = ActivityManager.MemoryInfo()
        (ctx.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager)
            .getMemoryInfo(memory)
        val storage = StatFs(ctx.filesDir.path).availableBytes / (1024 * 1024)
        return json(buildJsonObject {
            put("tor", ProcessManager.torReady())
            put("llama", ProcessManager.llamaReady())
            put("model", model?.id
                ?: (if (ProcessManager.downloadProgress >= 0.0) ProcessManager.downloadId else "none"))
            put("downloading", ProcessManager.downloadProgress in 0.0..0.99)
            put("progress", ProcessManager.downloadProgress)
            put("downloadBytes", ProcessManager.downloadBytes)
            put("downloadTotal", ProcessManager.downloadTotal)
            put("downloadSpeedBps", ProcessManager.downloadSpeedBps)
            put("ramMb", memory.totalMem / (1024 * 1024))
            put("freeStorageMb", storage)
            put("models", JsonArray(ProcessManager.models(ctx).map { m -> buildJsonObject {
                put("id", m.id); put("name", m.name); put("file", m.file); put("url", m.url)
                put("bytes", m.bytes); put("description", m.description)
                put("installed", ProcessManager.isInstalled(ctx, m)); put("builtin", m.builtin)
            }}))
        }.toString())
    }

    private fun install(session: IHTTPSession): Response {
        val files = HashMap<String, String>()
        session.parseBody(files)
        // NanoHTTPD stores an application/x-www-form-urlencoded POST body in
        // POST_DATA. It does not split it into one map entry per form field.
        // The old lookup therefore rejected every install request silently.
        // NanoHTTPD decodes form-urlencoded POST fields into parms; older
        // versions only exposed the raw body through postData.
        val body = files["postData"] ?: files["content"]
        fun value(name: String) = session.parms[name] ?: formValue(body, name)
        val existing = value("id") ?: value("tier")
        val id = if (!existing.isNullOrBlank()) existing else try {
            ProcessManager.addCustom(ctx, value("name").orEmpty(), value("url").orEmpty(), value("file").orEmpty(), value("bytes")?.toLongOrNull() ?: 0)
                .id
        } catch (e: IllegalArgumentException) { return error(Response.Status.BAD_REQUEST, e.message ?: "invalid model") }
        if (ProcessManager.models(ctx).none { it.id == id }) return error(Response.Status.BAD_REQUEST, "unknown model")
        ProcessManager.downloadModel(ctx, id)
        return json(buildJsonObject { put("ok", true); put("id", id) }.toString())
    }

    /**
     * Presentation-only preferences. They never leave the phone: reads come
     * from private storage, writes go back to it, and the endpoint sits behind
     * the API token like every other route - shared loopback sees nothing.
     * GET returns the current set; POST applies a partial update.
     */
    private fun preferences(session: IHTTPSession): Response {
        val store = ctx.getSharedPreferences(UI_PREFS, Context.MODE_PRIVATE)
        if (session.method == NanoHTTPD.Method.GET) {
            return json(storedPreferences(store).toJson())
        }
        val files = HashMap<String, String>()
        session.parseBody(files)
        val body = files["postData"] ?: files["content"]
        fun value(name: String) = session.parms[name] ?: formValue(body, name)
        val next = storedPreferences(store).patch(
            textScale = value("textScale"),
            enterSends = value("enterSends"),
            reduceMotion = value("reduceMotion"),
        )
        store.edit().putString(UI_PREFS_JSON, next.toJson()).apply()
        return json(next.toJson())
    }

    private fun storedPreferences(store: SharedPreferences): WorkbenchPreferences =
        WorkbenchPreferences.fromJson(store.getString(UI_PREFS_JSON, null))

    private fun select(session: IHTTPSession): Response {
        val files = HashMap<String, String>(); session.parseBody(files)
        val id = session.parms["id"] ?: formValue(files["postData"] ?: files["content"], "id")
        return if (id != null && ProcessManager.selectModel(ctx, id)) json("{\"ok\":true}")
        else error(Response.Status.BAD_REQUEST, "model is not installed")
    }

    private fun remove(session: IHTTPSession): Response {
        val files = HashMap<String, String>(); session.parseBody(files)
        val id = session.parms["id"] ?: formValue(files["postData"] ?: files["content"], "id")
        return if (id != null && ProcessManager.removeModel(ctx, id)) json("{\"ok\":true}")
        else error(Response.Status.BAD_REQUEST, "unknown model")
    }

    /** null means ABSENT so callers can distinguish a missing field from an
     *  explicitly empty field. */
    private fun formValue(body: String?, name: String): String? =
        body.orEmpty().split('&').asSequence()
            .map { it.split('=', limit = 2) }
            .firstOrNull { it.size == 2 && URLDecoder.decode(it[0], "UTF-8") == name }
            ?.let { URLDecoder.decode(it[1], "UTF-8") }

    private fun chat(session: IHTTPSession): Response {
        val files = HashMap<String, String>()
        session.parseBody(files)
        // Same lookup every other endpoint uses, and for the same reason: fetch()
        // with a plain string body sends text/plain, so NanoHTTPD never splits the
        // form fields into parms - it drops the whole body into postData. Reading
        // files["messages"] alone always came back null, i.e. the model was asked
        // an EMPTY conversation and answered something unrelated to the question.
        val body = files["postData"] ?: files["content"]
        val raw = session.parms["messages"] ?: formValue(body, "messages")
        // Permission belongs to this request only. Missing, malformed, and all
        // non-true values fail closed; nothing is saved in preferences.
        val allowSearch = (session.parms["allowSearch"]
            ?: formValue(body, "allowSearch")).equals("true", ignoreCase = true)
        val messages = Json.parseToJsonElement(raw?.ifBlank { null } ?: "[]").jsonArray
            .map { it.jsonObject }.toMutableList()
        // the UI sends plain {role, content} turns; the agent extends the list
        // Stop belongs to this request only: a fresh flag per chat, so a stop
        // pressed for one answer can never cancel a different one.
        val flag = AtomicBoolean()
        stopFlag = flag
        val answer = try {
            chatLock.submit<String?> {
                // Starting and checking the child are serialized with its chat.
                // An occupied shared-loopback port is never allowed to reach
                // Agent.turn unless our exact llama-server child is still live.
                if (!ProcessManager.ensureLlama(ctx) || !ProcessManager.awaitLlamaReady()) {
                    null
                } else {
                    Agent.turn(LLAMA, messages, allowSearch, shouldStop = { flag.get() }) { query ->
                        // Starting Tor is lazy: checking the one-turn box merely makes
                        // the tool available. No Tor process or network is touched
                        // unless the model actually asks to search during this turn.
                        if (!ProcessManager.ensureTor(ctx) || !ProcessManager.awaitTorReady())
                            "(Tor could not start safely; web search was not performed)"
                        else Agent.webSearch(query)
                    }
                }
            }.get()
        } catch (e: ExecutionException) {
            return error(Response.Status.INTERNAL_ERROR, rootMessage(e))
        } finally {
            stopFlag = null
        }
        if (answer == null) return error(
            Response.Status.SERVICE_UNAVAILABLE,
            "Local model server could not start safely",
        )
        return json(buildJsonObject {
            put("answer", answer)
            put("messages", JsonArray(messages))
        }.toString())
    }

    private fun rootMessage(error: Throwable): String =
        generateSequence(error) { it.cause }
            .lastOrNull()?.let { it.message?.takeIf(String::isNotBlank) ?: it.toString() }
            ?.replace(apiToken, "[redacted]")
            ?.replace(pageCapability, "[redacted]")
            ?: "unknown server error"

    private val API_PATHS = setOf(
        "/api/status", "/api/preferences", "/api/stop", "/api/install", "/api/select", "/api/remove", "/api/chat"
    )

    // Set by /api/stop from outside the chat lock; the running turn polls it
    // between rounds and before tool calls. A fresh flag per chat request
    // means a late stop can never kill the next turn.
    @Volatile private var stopFlag: AtomicBoolean? = null

    private fun stopChat(): Response {
        stopFlag?.set(true)
        return json("{\"ok\":true}")
    }

    private const val UI_PREFS = "onionmind_ui"
    private const val UI_PREFS_JSON = "preferences_json"
}
