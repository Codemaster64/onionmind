package org.onionmind.app

import android.content.Context
import android.content.Intent
import android.app.ActivityManager
import android.os.Handler
import android.os.Looper
import android.os.StatFs
import fi.iki.elonen.NanoHTTPD
import fi.iki.elonen.NanoHTTPD.IHTTPSession
import fi.iki.elonen.NanoHTTPD.Response
import kotlinx.serialization.json.*
import org.onionmind.core.Agent
import org.onionmind.core.ContextWindow
import org.onionmind.core.ConversationStore
import org.onionmind.core.EgressLog
import org.onionmind.core.ErrorKind
import org.onionmind.core.ExportFile
import org.onionmind.core.Features
import org.onionmind.core.ModelCatalog
import org.onionmind.core.ModelFit
import org.onionmind.core.PasswordThrottle
import org.onionmind.core.Presets
import org.onionmind.core.TurnStats
import org.onionmind.core.Vault
import org.onionmind.core.WorkbenchPreferences
import java.io.File
import java.io.RandomAccessFile
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
    private const val BACKGROUND_LOCK_MS = 5 * 60_000L
    private const val SAVE_GRACE_MS = 15_000L
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
        Workbench.startVault(ctx)
        http = object : NanoHTTPD("127.0.0.1", PORT) {
            override fun serve(session: IHTTPSession): Response {
                val response = try {
                    when {
                        session.uri == pagePath -> page()
                        session.uri == "/" -> notFound()
                        session.uri in API_PATHS &&
                            session.headers["x-onionmind-token"] != apiToken ->
                            error(Response.Status.FORBIDDEN, "bad or missing token")
                        session.uri == "/api/vault" -> vaultState()
                        session.uri == "/api/vault/unlock" -> unlock(session)
                        session.uri == "/api/vault/erase" -> erase(session)
                        // Encrypted and not unlocked: no data route answers.
                        Workbench.locked() && session.uri in API_PATHS -> lockedResponse()
                        session.uri == "/api/vault/password" -> setPassword(session)
                        session.uri == "/api/vault/remove" -> removePassword(session)
                        session.uri == "/api/status" -> status()
                        session.uri == "/api/preferences" -> preferences(session)
                        session.uri == "/api/features" -> features(session)
                        session.uri == "/api/netlog" -> netlog(session)
                        session.uri == "/api/share" -> share(session)
                        session.uri == "/api/conversation" -> conversation(session)
                        session.uri == "/api/export" -> export(session)
                        session.uri == "/api/import" -> import(session)
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
        val totalRamMb = memory.totalMem / (1024 * 1024)
        val flags = Workbench.features(ctx)
        return json(buildJsonObject {
            put("tor", ProcessManager.torReady())
            put("llama", ProcessManager.llamaReady())
            // ladder: our llama-server is running but not listening yet.
            put("llamaLoading", ProcessManager.llamaLoading())
            // Only while a reply is being written; the page turns it into progress.
            if (stopFlag != null && ProcessManager.llamaReady())
                ProcessManager.llamaGenerated()?.let { put("generated", it) }
            put("freeRamMb", memory.availMem / (1024 * 1024))
            put("contextLimit", ContextWindow.TOKENS)
            put("features", JsonObject(flags.mapValues { JsonPrimitive(it.value) }))
            if (flags["netlog"] == true || flags["boundary"] == true) {
                val today = Workbench.egress(ctx).today()
                put("egressToday", buildJsonObject {
                    put("tor", today[EgressLog.TOR] ?: 0)
                    put("direct", today[EgressLog.DIRECT] ?: 0)
                })
            }
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
                // An estimate from the real file size and this phone's RAM.
                ModelFit.label(m.bytes, totalRamMb).takeIf { it.isNotEmpty() }?.let { put("fit", it) }
                // A name screen, not a guarantee; the UI says exactly that.
                ModelCatalog.uncensoredMarker(m.id, m.name, m.url)?.let { put("uncensored", it) }
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
        if (session.method == NanoHTTPD.Method.GET) {
            return json(Workbench.preferences(ctx).toJson())
        }
        val files = HashMap<String, String>()
        session.parseBody(files)
        val body = files["postData"] ?: files["content"]
        fun value(name: String) = session.parms[name] ?: formValue(body, name)
        val next = Workbench.preferences(ctx).patch(
            textScale = value("textScale"),
            enterSends = value("enterSends"),
            reduceMotion = value("reduceMotion"),
            theme = value("theme"),
            preset = value("preset"),
            autoDeleteDays = value("autoDeleteDays"),
            secureScreen = value("secureScreen"),
        )
        Workbench.savePreferences(ctx, next)
        return json(next.toJson())
    }

    /** The v2 feature flags: GET the catalog and current state, POST a patch
     *  (one key per flag, or all=on|off). Presets ride along for the picker. */
    private fun features(session: IHTTPSession): Response {
        if (session.method != NanoHTTPD.Method.GET) {
            val files = HashMap<String, String>()
            session.parseBody(files)
            val body = files["postData"] ?: files["content"]
            val values = (Features.KEYS + "all").associateWith { session.parms[it] ?: formValue(body, it) }
            Workbench.saveFeatures(ctx, Features.patch(Workbench.features(ctx), values))
        }
        val flags = Workbench.features(ctx)
        return json(buildJsonObject {
            put("flags", JsonObject(flags.mapValues { JsonPrimitive(it.value) }))
            put("catalog", Features.catalogJson())
            put("presets", Presets.catalogJson())
        }.toString())
    }

    /** netlog: GET ?format=json|csv returns the log; DELETE clears it. */
    private fun netlog(session: IHTTPSession): Response {
        val log = Workbench.egress(ctx)
        if (session.method == NanoHTTPD.Method.DELETE) { log.clear(); return json("{\"ok\":true}") }
        val csv = session.parms["format"] == "csv"
        return NanoHTTPD.newFixedLengthResponse(Response.Status.OK,
            if (csv) "text/csv" else "application/json", if (csv) log.toCsv() else log.toJson())
    }

    /** Hand text to Android's share sheet - the user picks where it goes.
     *  The WebView has no navigator.share, so the page asks the app. */
    private fun share(session: IHTTPSession): Response {
        val files = HashMap<String, String>()
        session.parseBody(files)
        val body = files["postData"] ?: files["content"]
        val text = session.parms["text"] ?: formValue(body, "text")
        if (text.isNullOrEmpty()) return error(Response.Status.BAD_REQUEST, "nothing to share")
        val subject = (session.parms["subject"] ?: formValue(body, "subject")).orEmpty().take(80)
        val send = Intent(Intent.ACTION_SEND).apply {
            type = "text/plain"
            putExtra(Intent.EXTRA_TEXT, text)
            if (subject.isNotEmpty()) putExtra(Intent.EXTRA_SUBJECT, subject)
        }
        ctx.startActivity(Intent.createChooser(send, subject.ifEmpty { "Share" })
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        return json("{\"ok\":true}")
    }

    /**
     * The single conversation, on private storage only: GET returns it,
     * POST replaces it (the UI saves after every turn), DELETE clears it.
     */
    private fun conversation(session: IHTTPSession): Response {
        val vault = Workbench.vault
        if (session.method == NanoHTTPD.Method.DELETE) {
            ConversationStore.save(ctx.filesDir, emptyList(), vault)
            return json("{\"ok\":true}")
        }
        if (session.method == NanoHTTPD.Method.GET) {
            // lock: an old conversation is deleted before it is ever shown.
            if (Workbench.on(ctx, "lock"))
                ConversationStore.expire(ctx.filesDir, Workbench.preferences(ctx).autoDeleteDays)
            return json(JsonArray(ConversationStore.load(ctx.filesDir, vault)).toString())
        }
        val files = HashMap<String, String>()
        session.parseBody(files)
        val raw = session.parms["messages"]
            ?: formValue(files["postData"] ?: files["content"], "messages")
        val messages = try {
            Json.parseToJsonElement(raw?.ifBlank { null } ?: "[]")
                .jsonArray.map { it.jsonObject }
        } catch (_: Exception) {
            return error(Response.Status.BAD_REQUEST, "invalid conversation")
        }
        ConversationStore.save(ctx.filesDir, messages, vault)
        return json(JsonArray(messages).toString())
    }

    // --- export and import ---------------------------------------------------
    // The desktop's .onionmind file (core ExportFile): its own password, so
    // it opens on another phone or computer without this phone's key.

    /** Seal the conversation into an export; the activity asks where to save it. */
    private fun export(session: IHTTPSession): Response {
        val password = field(session, "password").orEmpty()
        if (password.length < 8) return error(Response.Status.BAD_REQUEST, "Use at least 8 characters.")
        val messages = ConversationStore.load(ctx.filesDir, Workbench.vault)
        val payload = ExportFile.fromConversation(messages)
        val count = payload["sessions"]!!.jsonArray.size
        if (count == 0) return error(Response.Status.BAD_REQUEST, "There is no conversation to export.")
        val save = Workbench.onExport ?: return error(Response.Status.CONFLICT, "Open Onionmind to export.")
        save(ExportFile.write(password, payload))
        return json(buildJsonObject { put("ok", true); put("sessions", count) }.toString())
    }

    /** Replace the conversation with the newest one in an export (the page confirms first). */
    private fun import(session: IHTTPSession): Response {
        val files = HashMap<String, String>(); session.parseBody(files)
        val body = files["postData"] ?: files["content"]
        val password = session.parms["password"] ?: formValue(body, "password").orEmpty()
        val data = session.parms["data"] ?: formValue(body, "data")
        val blob = try { Base64.getDecoder().decode(data.orEmpty()) } catch (_: IllegalArgumentException) {
            return error(Response.Status.BAD_REQUEST, "That file could not be read.")
        }
        val payload = try { ExportFile.read(blob, password) } catch (e: Vault.WrongPassword) {
            return error(Response.Status.FORBIDDEN, e.message ?: "That password does not open this export.")
        } catch (e: ExportFile.Invalid) {
            return error(Response.Status.BAD_REQUEST, e.message ?: "This is not an Onionmind export file.")
        }
        val (messages, total) = ExportFile.newestConversation(payload)
            ?: return error(Response.Status.BAD_REQUEST, "The export has no conversation in it.")
        stopFlag?.set(true)
        ConversationStore.save(ctx.filesDir, messages, Workbench.vault)
        return json(buildJsonObject { put("messages", messages.size); put("sessions", total) }.toString())
    }

    // --- encryption at rest -------------------------------------------------

    private object Locked : Response.IStatus {
        override fun getDescription() = "423 Locked"
        override fun getRequestStatus() = 423
    }

    private fun lockedResponse(): Response = NanoHTTPD.newFixedLengthResponse(Locked, "application/json",
        buildJsonObject { put("locked", true); put("error", "Onionmind is locked") }.toString())

    private fun vaultState(): Response = json(buildJsonObject {
        val vault = Workbench.vault
        put("locked", vault == null)
        put("hasPassword", vault?.hasPassword ?: Vault.needsPassword(ctx.filesDir))
        vault?.let { put("describe", it.describe()) }
        Workbench.vaultProblem?.let { put("problem", it) }
    }.toString())

    private fun field(session: IHTTPSession, name: String): String? {
        val files = HashMap<String, String>()
        if (session.method == NanoHTTPD.Method.POST) session.parseBody(files)
        return session.parms[name] ?: formValue(files["postData"] ?: files["content"], name)
    }

    private fun unlock(session: IHTTPSession): Response {
        throttled()?.let { return it }
        val password = field(session, "password").orEmpty()
        return try {
            Workbench.openVault(ctx, password)
            throttle.succeeded()
            vaultState()
        } catch (e: Vault.WrongPassword) {
            throttle.failed()
            error(Response.Status.FORBIDDEN, "That password is not right.")
        } catch (e: Vault.Unavailable) {
            error(Response.Status.CONFLICT, e.message ?: "The encrypted data could not be opened.")
        }
    }

    // Every route that checks a password shares one back-off.
    private val throttle = PasswordThrottle()

    private fun throttled(): Response? {
        val wait = throttle.waitMillis()
        if (wait == 0L) return null
        val seconds = (wait + 999) / 1000
        return NanoHTTPD.newFixedLengthResponse(TooMany, "application/json", buildJsonObject {
            put("error", "Too many wrong passwords. Try again in $seconds second${if (seconds == 1L) "" else "s"}.")
        }.toString())
    }

    private object TooMany : Response.IStatus {
        override fun getDescription() = "429 Too Many Requests"
        override fun getRequestStatus() = 429
    }

    // --- lock in the background ---------------------------------------------
    // With a password set, the key is wiped once the app has been out of sight
    // for BACKGROUND_LOCK_MS. A reply still being written keeps it until it has
    // finished and the page has had time to save it.
    private val main = Handler(Looper.getMainLooper())
    @Volatile private var lockAfterChat = false
    private val lockTask = Runnable {
        if (stopFlag != null) lockAfterChat = true else Workbench.lock()
    }

    /** MainActivity.onStop. */
    fun movedToBackground() {
        main.removeCallbacks(lockTask)
        main.postDelayed(lockTask, BACKGROUND_LOCK_MS)
    }

    /** MainActivity.onStart. */
    fun movedToForeground() {
        main.removeCallbacks(lockTask)
        lockAfterChat = false
    }

    private fun chatEnded() {
        if (!lockAfterChat) return
        lockAfterChat = false
        main.postDelayed(lockTask, SAVE_GRACE_MS)
    }

    private fun setPassword(session: IHTTPSession): Response {
        val files = HashMap<String, String>(); session.parseBody(files)
        val body = files["postData"] ?: files["content"]
        val password = session.parms["password"] ?: formValue(body, "password")
        val current = session.parms["current"] ?: formValue(body, "current")
        if (password == null || password.length < 8)
            return error(Response.Status.BAD_REQUEST, "Use at least 8 characters.")
        return try {
            throttled()?.let { return it }
            Workbench.vault!!.setPassword(password, current)
            throttle.succeeded()
            vaultState()
        } catch (e: Vault.WrongPassword) {
            throttle.failed()
            error(Response.Status.FORBIDDEN, e.message ?: "The current password is not right.")
        }
    }

    private fun removePassword(session: IHTTPSession): Response = throttled() ?: try {
        Workbench.vault!!.removePassword(field(session, "current").orEmpty())
        throttle.succeeded()
        vaultState()
    } catch (e: Vault.WrongPassword) {
        throttle.failed()
        error(Response.Status.FORBIDDEN, e.message ?: "That password is not right.")
    }

    /** Forgotten password: erase the conversation, log and settings (models stay). */
    private fun erase(session: IHTTPSession): Response {
        if (field(session, "confirm") != "erase") return error(Response.Status.BAD_REQUEST, "not confirmed")
        stopFlag?.set(true)
        Workbench.eraseAll(ctx)
        return vaultState()
    }

    private fun select(session: IHTTPSession): Response {
        val files = HashMap<String, String>(); session.parseBody(files)
        val id = session.parms["id"] ?: formValue(files["postData"] ?: files["content"], "id")
        if (id == null || !ProcessManager.selectModel(ctx, id))
            return error(Response.Status.BAD_REQUEST, "model is not installed")
        // ladder: load the new model now, so switching shows it loading
        // instead of the next message silently paying for it.
        if (Workbench.on(ctx, "ladder")) chatLock.submit {
            if (ProcessManager.ensureLlama(ctx)) ProcessManager.awaitLlamaReady()
        }
        return json("{\"ok\":true}")
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
        val flags = Workbench.features(ctx)
        val conversation = Json.parseToJsonElement(raw?.ifBlank { null } ?: "[]").jsonArray
            .map { it.jsonObject }
        // presets: one system message first, only while the feature is on;
        // with it off the request is exactly what the app always sent.
        val preset = if (flags["presets"] == true) Presets.byId(Workbench.preferences(ctx).preset) else null
        val messages = Presets.withSystemPrompt(conversation, preset)
        val stats = TurnStats()
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
                    Agent.turn(LLAMA, messages, allowSearch, shouldStop = { flag.get() }, stats = stats) { query ->
                        // Starting Tor is lazy: checking the one-turn box merely makes
                        // the tool available. No Tor process or network is touched
                        // unless the model actually asks to search during this turn.
                        if (!ProcessManager.ensureTor(ctx) || !ProcessManager.awaitTorReady()) {
                            stats.recordSearch(emptyList(), "tor")
                            "(Tor could not start safely; web search was not performed)"
                        } else {
                            val result = Agent.searchWeb(query)
                            stats.recordSearch(result.hits, result.error)
                            // The route and the result count - never the query.
                            Workbench.recordEgress(ctx, EgressLog.TOR, "DuckDuckGo onion service", "search",
                                if (result.error == null) "${result.hits.size} results" else "failed")
                            result.text
                        }
                    }
                }
            }.get()
        } catch (e: ExecutionException) {
            return failure(Response.Status.INTERNAL_ERROR, rootMessage(e))
        } finally {
            stopFlag = null
            chatEnded()
        }
        if (answer == null) return failure(
            Response.Status.SERVICE_UNAVAILABLE,
            "Local model server could not start safely",
        )
        // Agent.turn reports a failed model call as its answer text; give
        // that a kind too, so the errors card can offer the right fix.
        val kind = if (answer.startsWith("(local model request failed") ||
            answer.startsWith("(llama-server returned")) ErrorKind.classify(answer, llamaLogTail()) else null
        val saved = messages.filterNot { (it["role"] as? JsonPrimitive)?.contentOrNull == "system" }
        // Only the page used to save a finished reply, so a page reloaded
        // mid-reply (theme switch, split screen, the activity rebuilt after
        // switching apps on a phone short of memory) lost it for good. Keep it
        // here too; the page's own save afterwards just adds its details.
        if (kind == null && !flag.get()) try {
            ConversationStore.save(ctx.filesDir, saved, Workbench.vault)
        } catch (_: Exception) { }
        return json(buildJsonObject {
            put("answer", answer)
            // The preset's system message is per request, never part of the
            // saved conversation.
            put("messages", JsonArray(saved))
            put("stats", stats.toJson())
            ProcessManager.installedModel(ctx)?.let { put("model", it.name) }
            kind?.let { put("kind", it) }
        }.toString())
    }

    /** An error the errors card can act on: the message plus its kind. */
    private fun failure(status: Response.Status, message: String): Response =
        NanoHTTPD.newFixedLengthResponse(status, "application/json", buildJsonObject {
            put("error", message)
            ErrorKind.classify(message, llamaLogTail())?.let { put("kind", it) }
        }.toString())

    /** The end of llama-server's own log: an allocation failure is written
     *  there, while the chat only sees that the server went away. */
    private fun llamaLogTail(bytes: Int = 4096): String = try {
        RandomAccessFile(File(ctx.filesDir, "llama-server.log"), "r").use { f ->
            val start = (f.length() - bytes).coerceAtLeast(0)
            f.seek(start)
            ByteArray((f.length() - start).toInt()).also { f.readFully(it) }.toString(Charsets.UTF_8)
        }
    } catch (_: Exception) { "" }

    private fun rootMessage(error: Throwable): String =
        generateSequence(error) { it.cause }
            .lastOrNull()?.let { it.message?.takeIf(String::isNotBlank) ?: it.toString() }
            ?.replace(apiToken, "[redacted]")
            ?.replace(pageCapability, "[redacted]")
            ?: "unknown server error"

    private val API_PATHS = setOf(
        "/api/status", "/api/preferences", "/api/conversation", "/api/stop",
        "/api/install", "/api/select", "/api/remove", "/api/chat",
        "/api/features", "/api/netlog", "/api/share", "/api/export", "/api/import",
        "/api/vault", "/api/vault/unlock", "/api/vault/password", "/api/vault/remove", "/api/vault/erase"
    )

    // Set by /api/stop from outside the chat lock; the running turn polls it
    // between rounds and before tool calls. A fresh flag per chat request
    // means a late stop can never kill the next turn.
    @Volatile private var stopFlag: AtomicBoolean? = null

    private fun stopChat(): Response {
        stopFlag?.set(true)
        return json("{\"ok\":true}")
    }

}
