package org.onionmind.app

import android.content.Context
import android.app.ActivityManager
import android.os.StatFs
import fi.iki.elonen.NanoHTTPD
import fi.iki.elonen.NanoHTTPD.IHTTPSession
import fi.iki.elonen.NanoHTTPD.Response
import kotlinx.serialization.json.*
import org.onionmind.core.Agent
import java.net.URLDecoder
import java.util.concurrent.ExecutionException
import java.util.concurrent.Executors

/** The app's whole backend: serves the chat page and a tiny JSON API on
 *  127.0.0.1:8081. The WebView talks only to this. */
object Server {
    private const val PORT = 8081
    private const val LLAMA = "http://127.0.0.1:8080"
    private var http: NanoHTTPD? = null
    private val chatLock = Executors.newSingleThreadExecutor()
    private lateinit var ctx: Context

    // Android shares loopback across apps: every other installed app can reach
    // 127.0.0.1:8081. Without this, any of them could read the conversation,
    // queue downloads, or turn Tor off. Generated once per process and pasted
    // into the page at serve time, so only our own WebView can present it.
    private val token: String = java.math.BigInteger(
        130, java.security.SecureRandom()).toString(32)

    fun start(context: Context) {
        if (http != null) return
        ctx = context.applicationContext
        http = object : NanoHTTPD("127.0.0.1", PORT) {
            override fun serve(session: IHTTPSession): Response {
                return try {
                    if (session.uri.startsWith("/api/") &&
                        session.headers["x-onionmind-token"] != token)
                        return error(Response.Status.FORBIDDEN, "bad or missing token")
                    when (session.uri) {
                        "/" -> page()
                        "/api/status" -> status()
                        "/api/install" -> install(session)
                        "/api/select" -> select(session)
                        "/api/remove" -> remove(session)
                        "/api/tor" -> tor(session)
                        "/api/chat" -> chat(session)
                        else -> NanoHTTPD.newFixedLengthResponse(
                            Response.Status.NOT_FOUND, "text/plain", "?")
                    }
                } catch (e: Exception) {
                    error(Response.Status.INTERNAL_ERROR, rootMessage(e))
                }
            }
        }.also { it.start(NanoHTTPD.SOCKET_READ_TIMEOUT, true) }
    }

    private fun page(): Response {
        val html = ctx.assets.open("index.html").readBytes()
            .toString(Charsets.UTF_8).replace("__ONIONMIND_TOKEN__", token)
            .toByteArray(Charsets.UTF_8)
        // no-store matters: the token changes every process start, and a WebView
        // serving a cached page would present a stale one and 403 on every call.
        return NanoHTTPD.newFixedLengthResponse(
            Response.Status.OK, "text/html", html.inputStream(), html.size.toLong())
            .apply { addHeader("Cache-Control", "no-store") }
    }

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
            put("torEnabled", ProcessManager.torEnabled(ctx))
            put("tor", ProcessManager.torEnabled(ctx) && ProcessManager.torReady(ctx))
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

    private fun tor(session: IHTTPSession): Response {
        val files = HashMap<String, String>(); session.parseBody(files)
        val raw = session.parms["enabled"] ?: formValue(files["postData"] ?: files["content"], "enabled")
        if (raw.isNullOrBlank()) return error(Response.Status.BAD_REQUEST, "enabled?")
        val enabled = raw.equals("true", ignoreCase = true)
        ProcessManager.setTorEnabled(ctx, enabled)
        return json(buildJsonObject { put("ok", true); put("enabled", enabled) }.toString())
    }

    /** null means ABSENT. It used to return "", which made every `?: return
     *  error(...)` guard dead code - most seriously in tor(), where a request
     *  with no `enabled` field read as `false` and silently switched Tor OFF. */
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
        val raw = session.parms["messages"]
            ?: formValue(files["postData"] ?: files["content"], "messages")
        val messages = Json.parseToJsonElement(raw?.ifBlank { null } ?: "[]").jsonArray
            .map { it.jsonObject }.toMutableList()
        // the UI sends plain {role, content} turns; the agent extends the list
        ProcessManager.ensureLlama(ctx)
        val answer = try {
            chatLock.submit<String> { Agent.turn(LLAMA, messages) { query ->
                if (!ProcessManager.torEnabled(ctx)) "(web search is disabled because Tor is off)"
                else Agent.webSearch(query)
            } }.get()
        } catch (e: ExecutionException) {
            return error(Response.Status.INTERNAL_ERROR, rootMessage(e))
        }
        return json(buildJsonObject {
            put("answer", answer)
            put("messages", JsonArray(messages))
        }.toString())
    }

    private fun rootMessage(error: Throwable): String =
        generateSequence(error) { it.cause }
            .lastOrNull()?.let { it.message?.takeIf(String::isNotBlank) ?: it.toString() }
            ?: "unknown server error"
}
