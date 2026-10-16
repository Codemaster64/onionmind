package org.onionmind.app

import android.content.Context
import android.content.Intent
import android.net.Uri
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.onionmind.core.EgressLog
import org.onionmind.core.ModelSource
import org.onionmind.core.OwnedLoopbackProcess
import java.io.File
import java.io.FileOutputStream
import java.net.HttpURLConnection
import java.net.InetSocketAddress
import java.net.Socket
import java.net.URL
import java.util.concurrent.Callable
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong
import java.util.UUID

/** Owns the downloadable model catalog and the llama/tor child processes. */
object ProcessManager {
    private const val LLAMA_PORT = 8080
    private val llama = OwnedLoopbackProcess(listenerOpen = { portOpen(LLAMA_PORT) })
    private var tor: Process? = null
    @Volatile private var torLog: File? = null
    private const val PREFS = "models"
    private const val CUSTOM = "custom"
    private const val ACTIVE = "active"
    private const val DOWNLOAD_WORKERS = 4
    private val downloadClaimed = AtomicBoolean(false)

    data class Model(val id: String, val name: String, val file: String, val url: String, val bytes: Long, val builtin: Boolean = false, val description: String = "", val mirrorUrl: String? = null)

    private val modelMirrorBase = BuildConfig.MODEL_MIRROR_BASE.trimEnd('/')
    private fun mirrorUrl(file: String): String? = modelMirrorBase.takeIf { it.isNotEmpty() }?.let { "$it/$file" }

    private val builtins = listOf(
        // Model names are ordered from least heavy to heaviest. Keep ids stable
        // because they are used for recommendations and persisted selections.
        // Descriptions follow the desktop's _TIER_MODELS: the real model, then
        // its weight class (RAM here, not VRAM). INFERNO (Qwen3.8 27B) is not
        // offered: even its smallest quant needs ~10 GB and runs well under
        // 1 tok/s on a phone CPU. A custom link can still add it.
        Model("lfm", "SPARK", "LFM2.5-2.6B-heretic-Q4_K_M.gguf", "https://huggingface.co/Abiray/LFM2.5-2.6B-Heretic-Abliterated-GGUF/resolve/main/LFM2.5-2.6B-heretic-Q4_K_M.gguf", 1_674_454_432L, true, "LFM2.5 2.6B · very light - quick replies on most phones", mirrorUrl("LFM2.5-2.6B-heretic-Q4_K_M.gguf")),
        Model("4b", "EMBER", "Huihui-Qwen3.5-4B-abliterated.Q4_K_M.gguf", "https://huggingface.co/mradermacher/Huihui-Qwen3.5-4B-abliterated-GGUF/resolve/main/Huihui-Qwen3.5-4B-abliterated.Q4_K_M.gguf", 2_707_514_688L, true, "Qwen3.5 4B · light - best from about 7 GB of RAM", mirrorUrl("Huihui-Qwen3.5-4B-abliterated.Q4_K_M.gguf")),
        Model("9b", "BLAZE", "Huihui-Qwen3.5-9B-abliterated.Q4_K_M.gguf", "https://huggingface.co/mradermacher/Huihui-Qwen3.5-9B-abliterated-GGUF/resolve/main/Huihui-Qwen3.5-9B-abliterated.Q4_K_M.gguf", 5_627_045_248L, true, "Qwen3.5 9B · moderate - stronger reasoning, needs about 11 GB of RAM", mirrorUrl("Huihui-Qwen3.5-9B-abliterated.Q4_K_M.gguf")),
    )

    fun models(ctx: Context): List<Model> = builtins + readCustom(ctx)
    fun modelDir(ctx: Context) = File(ctx.filesDir, "models").apply { mkdirs() }
    private fun hasGgufHeader(file: File): Boolean = try {
        file.inputStream().use { input ->
            val header = ByteArray(4)
            input.read(header) == header.size && header.contentEquals(byteArrayOf(0x47, 0x47, 0x55, 0x46))
        }
    } catch (_: Exception) {
        false
    }

    fun isInstalled(ctx: Context, model: Model): Boolean = File(modelDir(ctx), model.file).let {
        it.isFile && it.length() > 0 && (model.bytes <= 0 || it.length() == model.bytes) && hasGgufHeader(it)
    }
    fun installedModels(ctx: Context) = models(ctx).filter { isInstalled(ctx, it) }
    fun installedModel(ctx: Context): Model? {
        val installed = installedModels(ctx)
        val active = Workbench.readSecret(ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE), ACTIVE)
        return installed.firstOrNull { it.id == active } ?: installed.firstOrNull()
    }

    // The catalog is stored as TSV, so a tab or newline in a field does not
    // corrupt one row - it FORGES extra ones, and a forged row's `file` was never
    // re-validated on read. That is a path-traversal write primitive reachable
    // from the local HTTP API.
    private val FILENAME = Regex("[A-Za-z0-9._-]+")
    private val CLEAN = Regex("[^\t\n\r]+")

    fun addCustom(ctx: Context, name: String, url: String, file: String, bytes: Long): Model {
        require(Uri.parse(url).scheme == "https") { "model URL must use HTTPS" }
        require(url.matches(CLEAN)) { "invalid model URL" }
        // Derive BEFORE validating: whatever we derive still has to clear the
        // same FILENAME gate, so a crafted URL cannot smuggle a path out of
        // modelDir any more than a typed filename could.
        val target = file.ifBlank { ModelSource.filenameFrom(url).orEmpty() }
        require(target.matches(FILENAME)) { "could not read a model filename from that URL" }
        val label = name.trim().ifBlank { ModelSource.nameFrom(target) }.take(80)
        require(label.isNotEmpty() && label.matches(CLEAN)) { "invalid model name" }
        val model = Model("custom-" + UUID.randomUUID().toString(), label, target, url, bytes.coerceAtLeast(0))
        val all = readCustom(ctx).toMutableList().apply { add(model) }
        writeCustom(ctx, all)
        return model
    }

    fun selectModel(ctx: Context, id: String): Boolean {
        val model = installedModels(ctx).firstOrNull { it.id == id } ?: return false
        Workbench.writeSecret(ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE), ACTIVE, model.id)
        stopLlama()
        return true
    }

    fun removeModel(ctx: Context, id: String): Boolean {
        val model = models(ctx).firstOrNull { it.id == id } ?: return false
        if (installedModel(ctx)?.id == id) stopLlama()
        val part = File(modelDir(ctx), model.file + ".part")
        File(modelDir(ctx), model.file).delete()
        part.delete()
        cleanupParallelParts(part)
        if (!model.builtin) writeCustom(ctx, readCustom(ctx).filterNot { it.id == id })
        val prefs = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        if (Workbench.readSecret(prefs, ACTIVE) == id) prefs.edit().remove(ACTIVE).apply()
        return true
    }

    @Volatile var downloadProgress: Double = -1.0
    @Volatile var downloadId: String = ""
    @Volatile var downloadBytes: Long = 0
    @Volatile var downloadTotal: Long = 0
    @Volatile var downloadSpeedBps: Long = 0

    /** Hands the download to a foreground service so it keeps running while the
     *  app is minimized; falls back to a bare thread if the OS refuses to start
     *  one (background-start restrictions on API 31+). */
    fun downloadModel(ctx: Context, id: String) {
        val m = models(ctx).firstOrNull { it.id == id } ?: return
        if (isInstalled(ctx, m) || !downloadClaimed.compareAndSet(false, true)) return
        // Claim the slot atomically before the service starts, so concurrent
        // requests and double taps cannot start multiple writers.
        downloadProgress = 0.0; downloadId = id; downloadBytes = 0; downloadTotal = m.bytes; downloadSpeedBps = 0
        try {
            ctx.startForegroundService(Intent(ctx, DownloadService::class.java).putExtra("id", id))
        } catch (_: Exception) {
            try {
                Thread { runDownload(ctx, id) }.start()
            } catch (_: Exception) {
                downloadClaimed.set(false)
                downloadProgress = -1.0
            }
        }
    }

    /** Blocks until the model is on disk or the attempt fails. */
    fun runDownload(ctx: Context, id: String) {
        val m = models(ctx).firstOrNull { it.id == id }
        if (m == null) {
            downloadClaimed.set(false)
            downloadProgress = -1.0
            return
        }
        downloadProgress = 0.0; downloadId = id; downloadBytes = 0; downloadTotal = m.bytes; downloadSpeedBps = 0
        try {
            val out = File(modelDir(ctx), m.file)
            val part = File(modelDir(ctx), m.file + ".part")
            if (out.exists()) out.delete()
            var expected = m.bytes
            val sources = listOfNotNull(m.url, m.mirrorUrl).distinct()
            // netlog: downloads go direct over HTTPS, not Tor - say so in the
            // log too. Host and model name only, never the full URL.
            sources.mapNotNull { runCatching { URL(it).host }.getOrNull() }.distinct().forEach { host ->
                Workbench.recordEgress(ctx, EgressLog.DIRECT, host, "model download", m.name)
            }
            var sourceIndex = 0
            if (expected > 0 && part.length() > expected) part.delete()

            // A prior four-way attempt may have left exact-length range files,
            // which are safe to resume. Never trust a full-length shared .part:
            // the old implementation preallocated it before any bytes arrived,
            // so a process kill could otherwise promote a sparse/corrupt model.
            var parallelReady = false
            if (expected > 0 && (
                    part.length() == 0L || part.length() >= expected ||
                        parallelPartFiles(part).any { it.exists() }
                )) {
                parallelReady = try {
                    downloadParallel(part, expected, sources)
                    true
                } catch (_: Exception) {
                    if (part.length() >= expected) part.delete()
                    false
                }
            }

            if (!parallelReady) {
                // Attempts that move NO bytes. Real progress resets the budget,
                // so a long resumable download is never cut short. This path is
                // also the fallback for servers that do not honor Range.
                var stalled = 0
                val startedAt = System.nanoTime()
                downloadBytes = part.length()
                while (expected <= 0 || part.length() < expected) {
                    if (stalled >= 10) throw IllegalStateException(
                        "download made no progress in $stalled attempts - check the model URL")
                    val offset = part.length()
                    val c = URL(sources[sourceIndex]).openConnection() as HttpURLConnection
                    if (offset > 0) c.setRequestProperty("Range", "bytes=$offset-")
                    c.connectTimeout = 15_000; c.readTimeout = 30_000
                    if (c.responseCode !in 200..299) {
                        c.disconnect(); stalled++
                        if (stalled >= 3 && sourceIndex + 1 < sources.size) { sourceIndex++; stalled = 0 }
                        else Thread.sleep(3000)
                        continue
                    }
                    val append = offset > 0 && c.responseCode == HttpURLConnection.HTTP_PARTIAL
                    if (offset > 0 && !append) {
                        part.delete(); c.disconnect(); stalled++
                        if (stalled >= 3 && sourceIndex + 1 < sources.size) { sourceIndex++; stalled = 0 }
                        continue
                    }
                    if (expected <= 0) expected = if (append) offset + c.contentLengthLong else c.contentLengthLong
                    if (expected > 0) downloadTotal = expected
                    c.inputStream.use { input -> FileOutputStream(part, append).use { output ->
                        val buffer = ByteArray(1 shl 16); var read: Int
                        while (input.read(buffer).also { read = it } != -1) {
                            output.write(buffer, 0, read)
                            downloadBytes = part.length()
                            downloadSpeedBps = (downloadBytes * 1_000_000_000L / (System.nanoTime() - startedAt).coerceAtLeast(1L))
                            downloadProgress = if (expected > 0) part.length().toDouble() / expected else 0.0
                        }
                    }}
                    c.disconnect()
                    if (expected <= 0) expected = part.length()
                    stalled = if (part.length() > offset) 0 else stalled + 1
                }
            }

            if (expected <= 0 || part.length() != expected)
                throw IllegalStateException("download size did not match the model catalog")
            if (!part.renameTo(out) || !isInstalled(ctx, m.copy(bytes = expected)))
                throw IllegalStateException("could not finalize model")
            cleanupParallelParts(part)
            if (!m.builtin && m.bytes <= 0) writeCustom(ctx, readCustom(ctx).map { if (it.id == m.id) it.copy(bytes = expected) else it })
            downloadProgress = 1.0
        } catch (_: Exception) {
            downloadProgress = -1.0; downloadBytes = 0; downloadTotal = 0; downloadSpeedBps = 0
        } finally {
            downloadClaimed.set(false)
        }
    }

    private data class DownloadRange(val index: Int, val start: Long, val end: Long, val file: File) {
        val length: Long get() = end - start + 1
    }

    private fun parallelPartFiles(part: File): List<File> =
        (0 until DOWNLOAD_WORKERS).map { File(part.parentFile, "${part.name}.range-$it") }

    private fun cleanupParallelParts(part: File) = parallelPartFiles(part).forEach { it.delete() }

    /** Fetch a model into independent, exact-length range files, then assemble
     * them. A process kill cannot turn filesystem preallocation into a valid
     * download, and every range can resume independently on the next attempt. */
    private fun downloadParallel(part: File, expected: Long, sources: List<String>) {
        val chunk = (expected + DOWNLOAD_WORKERS - 1) / DOWNLOAD_WORKERS
        val rangeFiles = parallelPartFiles(part)
        val ranges = (0 until DOWNLOAD_WORKERS).mapNotNull { index ->
            val start = index * chunk
            if (start >= expected) return@mapNotNull null
            DownloadRange(index, start, minOf(expected - 1, start + chunk - 1), rangeFiles[index])
        }
        ranges.forEach { range ->
            if (range.file.length() > range.length) range.file.delete()
        }
        val completed = AtomicLong(ranges.sumOf { it.file.length() })
        val startedAt = System.nanoTime()
        downloadBytes = completed.get()
        downloadTotal = expected
        downloadProgress = completed.get().toDouble() / expected
        val pool = Executors.newFixedThreadPool(ranges.size)
        try {
            val jobs = ranges.map { range ->
                Callable {
                    if (range.file.length() == range.length) return@Callable Unit
                    var lastFailure: Exception? = null
                    for (source in sources) {
                        repeat(3) {
                            try {
                                val offset = range.file.length()
                                if (offset == range.length) return@Callable Unit
                                val requestStart = range.start + offset
                                val c = (URL(source).openConnection() as HttpURLConnection).apply {
                                    setRequestProperty("Range", "bytes=$requestStart-${range.end}")
                                    connectTimeout = 15_000
                                    readTimeout = 30_000
                                }
                                try {
                                    val contentRange = "bytes $requestStart-${range.end}/$expected"
                                    if (c.responseCode != HttpURLConnection.HTTP_PARTIAL ||
                                        c.getHeaderField("Content-Range") != contentRange
                                    ) throw IllegalStateException("server did not honor the requested range")
                                    c.inputStream.use { input -> FileOutputStream(range.file, true).use { output ->
                                        val buffer = ByteArray(1 shl 16)
                                        var read: Int
                                        while (input.read(buffer).also { read = it } != -1) {
                                            if (range.file.length() + read > range.length)
                                                throw IllegalStateException("range response exceeded its requested length")
                                            output.write(buffer, 0, read)
                                            val done = completed.addAndGet(read.toLong())
                                            downloadBytes = done
                                            downloadSpeedBps = done * 1_000_000_000L / (System.nanoTime() - startedAt).coerceAtLeast(1L)
                                            downloadProgress = done.toDouble() / expected
                                        }
                                    }}
                                } finally {
                                    c.disconnect()
                                }
                                if (range.file.length() != range.length)
                                    throw IllegalStateException("range response was incomplete")
                                return@Callable Unit
                            } catch (e: Exception) {
                                lastFailure = e
                                Thread.sleep(500)
                            }
                        }
                    }
                    throw lastFailure ?: IllegalStateException("range download failed")
                }
            }
            pool.invokeAll(jobs).forEach { it.get() }
            if (completed.get() != expected || ranges.any { it.file.length() != it.length })
                throw IllegalStateException("parallel download was incomplete")

            FileOutputStream(part, false).use { output ->
                ranges.sortedBy { it.index }.forEach { range ->
                    range.file.inputStream().use { it.copyTo(output) }
                }
                output.fd.sync()
            }
            if (part.length() != expected)
                throw IllegalStateException("assembled download had the wrong size")
        } finally {
            pool.shutdownNow()
        }
    }

    fun ensureLlama(ctx: Context): Boolean = llama.ensure {
        val m = installedModel(ctx) ?: return@ensure null
        val bin = File(ctx.applicationInfo.nativeLibraryDir, "libllamaserver.so")
        val log = try {
            File(ctx.filesDir, "llama-server.log").outputStream()
        } catch (_: Exception) {
            return@ensure null
        }
        val owned = try {
            ProcessBuilder(
                bin.absolutePath,
                "-m", File(modelDir(ctx), m.file).absolutePath,
                "--host", "127.0.0.1",
                "--port", LLAMA_PORT.toString(),
                "-c", "16384",   // = ContextWindow.TOKENS (WorkbenchTest checks)
            ).apply {
                redirectErrorStream(true)
                environment()["LD_LIBRARY_PATH"] = ctx.applicationInfo.nativeLibraryDir
            }.start()
        } catch (_: Exception) {
            try { log.close() } catch (_: Exception) { }
            return@ensure null
        }
        try {
            owned.outputStream.close()
        } catch (_: Exception) {
            // llama-server does not read stdin; an already-closed pipe is fine.
        }
        Thread {
            try {
                owned.inputStream.use { input -> log.use { output -> input.copyTo(output) } }
            } catch (_: Exception) {
                try { log.close() } catch (_: Exception) { }
            }
        }.start()
        owned
    }

    // The port opens before the model has loaded, and llama-server answers 503
    // until it has: a chat sent on "port open" failed with HTTP 503.
    private fun llamaHealthy() = try {
        (URL("http://127.0.0.1:$LLAMA_PORT/health").openConnection(java.net.Proxy.NO_PROXY) as HttpURLConnection).run {
            connectTimeout = 500; readTimeout = 2000
            try { responseCode == 200 } finally { disconnect() }
        }
    } catch (_: Exception) { false }

    /** Tokens the busy slot has written so far, or null: replies are not
     *  streamed, so without this a slow phone looked exactly like a hang. */
    fun llamaGenerated(): Int? = try {
        (URL("http://127.0.0.1:$LLAMA_PORT/slots").openConnection(java.net.Proxy.NO_PROXY) as HttpURLConnection).run {
            connectTimeout = 500; readTimeout = 2000
            try {
                if (responseCode != 200) null
                else kotlinx.serialization.json.Json.parseToJsonElement(inputStream.bufferedReader().readText())
                    .jsonArray.map { it.jsonObject }
                    .filter { it["is_processing"]?.jsonPrimitive?.booleanOrNull == true }   // idle slots keep stale counts
                    .mapNotNull { it["next_token"]?.jsonArray?.firstOrNull()?.jsonObject
                        ?.get("n_decoded")?.jsonPrimitive?.intOrNull }
                    .maxOrNull()
            } finally { disconnect() }
        }
    } catch (_: Exception) { null }

    fun awaitLlamaReady(timeoutMillis: Long = 90_000): Boolean {
        val deadline = System.nanoTime() + timeoutMillis * 1_000_000
        if (!llama.awaitReady(timeoutMillis)) return false
        while (System.nanoTime() < deadline) {
            if (!llama.alive()) return false
            if (llamaHealthy()) return true
            Thread.sleep(250)
        }
        return llamaHealthy()
    }

    fun ensureTor(ctx: Context): Boolean {
        if (tor?.isAlive == true) return true
        tor = null
        // Never mistake an arbitrary app's loopback listener for our Tor. If
        // 9050 is occupied, fail closed instead of sending a query through an
        // unverified proxy or racing a second daemon for the same port.
        if (portOpen(9050)) return false
        val dir = File(ctx.filesDir, "tor").apply { mkdirs() }
        torLog = File(dir, "log").apply { delete() }     // this start's progress only
        File(dir, "torrc").writeText("SocksPort 9050\nDataDirectory ${File(dir, "data").apply { mkdirs() }.absolutePath}\nCookieAuthentication 0\nAvoidDiskWrites 1\nLog notice file ${File(dir, "log").absolutePath}")
        tor = ProcessBuilder(File(ctx.applicationInfo.nativeLibraryDir, "libtor.so").absolutePath, "-f", File(dir, "torrc").absolutePath).redirectErrorStream(true).start()
        Workbench.recordEgress(ctx, EgressLog.TOR, "Tor network", "start Tor", "relay directory and circuits")
        Thread { try { tor!!.inputStream.readBytes() } catch (_: Exception) {} }.start()
        return true
    }

    // A cold Tor needs one to two minutes to bootstrap (the desktop measured 110 s).
    fun awaitTorReady(timeoutMillis: Long = 180_000): Boolean {
        val deadline = System.nanoTime() + timeoutMillis * 1_000_000
        while (System.nanoTime() < deadline) {
            if (torReady()) return true
            if (tor?.isAlive != true) return false
            Thread.sleep(250)
        }
        return torReady()
    }

    private fun stopLlama() = llama.stop()
    fun llamaReady(): Boolean = llama.ready() && llamaHealthy()
    fun llamaLoading(): Boolean = llama.alive() && !llamaReady()
    // The SOCKS port opens at once; a search sent before a circuit exists fails.
    // Same rule as the desktop's _await_tor_ready: wait for "Bootstrapped 100%".
    fun torReady() = tor?.isAlive == true && portOpen(9050) &&
        torLog?.let { try { it.readText().contains("Bootstrapped 100%") } catch (_: Exception) { false } } == true
    private fun portOpen(port: Int) = try { Socket().use { it.connect(InetSocketAddress("127.0.0.1", port), 300) }; true } catch (_: Exception) { false }
    fun stopAll() { stopLlama(); tor?.destroy() }

    // The custom catalog names what was downloaded from where: encrypted like the rest.
    private fun readCustom(ctx: Context): List<Model> = Workbench.readSecret(ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE), CUSTOM).orEmpty().lineSequence().mapNotNull {
        // Re-validate on the way OUT too: whatever sits in the store, only a
        // plain name may ever be joined onto modelDir.
        val p = it.split('\t')
        if (p.size == 5 && p[2].matches(FILENAME)) Model(p[0], p[1], p[2], p[3], p[4].toLongOrNull() ?: 0) else null
    }.toList()
    private fun writeCustom(ctx: Context, models: List<Model>) = Workbench.writeSecret(ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE), CUSTOM, models.joinToString("\n") { listOf(it.id, it.name, it.file, it.url, it.bytes).joinToString("\t") })
}
