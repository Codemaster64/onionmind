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

    fun start(context: Context) {
        if (http != null) return
        ctx = context.applicationContext
        http = object : NanoHTTPD("127.0.0.1", PORT) {
            override fun serve(session: IHTTPSession): Response {
                return try {
                    when (session.uri) {
                        "/" -> page()
                        "/api/status" -> status()
                        "/api/install" -> install(session)
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
        return NanoHTTPD.newFixedLengthResponse(
            Response.Status.OK, "text/html", html.inputStream(), html.size.toLong())
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
            put("tor", ProcessManager.torReady())
            put("llama", ProcessManager.llamaReady())
            put("model", model?.tier
                ?: (if (ProcessManager.downloadProgress >= 0.0) ProcessManager.downloadTier else "none"))
            put("downloading", ProcessManager.downloadProgress in 0.0..0.99)
            put("progress", ProcessManager.downloadProgress)
            put("ramMb", memory.totalMem / (1024 * 1024))
            put("freeStorageMb", storage)
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
        val tier = session.parms["tier"]
            ?: formValue(files["postData"] ?: files["content"], "tier")
        if (ProcessManager.models().none { it.tier == tier })
            return NanoHTTPD.newFixedLengthResponse(
                Response.Status.BAD_REQUEST, "text/plain", "tier?")
        ProcessManager.downloadModel(ctx, tier)
        return json("{\"ok\":true}")
    }

    private fun formValue(body: String?, name: String): String {
        return body.orEmpty().split('&').asSequence()
            .map { it.split('=', limit = 2) }
            .firstOrNull { it.size == 2 && URLDecoder.decode(it[0], "UTF-8") == name }
            ?.let { URLDecoder.decode(it[1], "UTF-8") }
            .orEmpty()
    }

    private fun chat(session: IHTTPSession): Response {
        val files = HashMap<String, String>()
        session.parseBody(files)
        val messages = Json.parseToJsonElement(files["messages"] ?: "[]").jsonArray
            .map { it.jsonObject }.toMutableList()
        // the UI sends plain {role, content} turns; the agent extends the list
        val answer = try {
            chatLock.submit<String> { Agent.turn(LLAMA, messages) }.get()
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
