package org.onionmind.app

import android.content.Context
import android.net.Uri
import java.io.File
import java.io.FileOutputStream
import java.net.HttpURLConnection
import java.net.InetSocketAddress
import java.net.Socket
import java.net.URL
import java.util.UUID

/** Owns the downloadable model catalog and the llama/tor child processes. */
object ProcessManager {
    private var llama: Process? = null
    private var tor: Process? = null
    private const val PREFS = "models"
    private const val CUSTOM = "custom"
    private const val ACTIVE = "active"

    data class Model(val id: String, val name: String, val file: String, val url: String, val bytes: Long, val builtin: Boolean = false, val description: String = "")

    private val builtins = listOf(
        // Model names are ordered from least heavy to heaviest. Keep ids stable
        // because they are used for recommendations and persisted selections.
        Model("lfm", "SPARK", "LFM2.5-2.6B-heretic-Q4_K_M.gguf", "https://huggingface.co/Abiray/LFM2.5-2.6B-Heretic-Abliterated-GGUF/resolve/main/LFM2.5-2.6B-heretic-Q4_K_M.gguf", 1_674_454_432L, true, "1–3B params · low latency · mobile/edge optimized · quick replies · IoT · smart devices"),
        Model("4b", "EMBER", "Huihui-Qwen3.5-4B-abliterated.Q4_K_M.gguf", "https://huggingface.co/mradermacher/Huihui-Qwen3.5-4B-abliterated-GGUF/resolve/main/Huihui-Qwen3.5-4B-abliterated.Q4_K_M.gguf", 2_707_514_688L, true, "Lite / Local · Start something."),
        Model("9b", "BLAZE", "Huihui-Qwen3.5-9B-abliterated.Q4_K_M.gguf", "https://huggingface.co/mradermacher/Huihui-Qwen3.5-9B-abliterated-GGUF/resolve/main/Huihui-Qwen3.5-9B-abliterated.Q4_K_M.gguf", 5_627_045_248L, true, "7–14B params · efficient reasoning · runs on local GPUs · everyday assistant · coding · summarization · Pro / Real-time · After the burn."),
    )

    fun models(ctx: Context): List<Model> = builtins + readCustom(ctx)
    fun modelDir(ctx: Context) = File(ctx.filesDir, "models").apply { mkdirs() }
    fun isInstalled(ctx: Context, model: Model): Boolean = File(modelDir(ctx), model.file).let { it.exists() && (model.bytes <= 0 || it.length() == model.bytes) }
    fun installedModels(ctx: Context) = models(ctx).filter { isInstalled(ctx, it) }
    fun installedModel(ctx: Context): Model? {
        val installed = installedModels(ctx)
        val active = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(ACTIVE, null)
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
        require(file.matches(FILENAME)) { "invalid model filename" }
        require(url.matches(CLEAN)) { "invalid model URL" }
        require(name.trim().length in 1..80 && name.matches(CLEAN)) { "invalid model name" }
        val model = Model("custom-" + UUID.randomUUID().toString(), name.trim(), file, url, bytes.coerceAtLeast(0))
        val all = readCustom(ctx).toMutableList().apply { add(model) }
        writeCustom(ctx, all)
        return model
    }

    fun selectModel(ctx: Context, id: String): Boolean {
        val model = installedModels(ctx).firstOrNull { it.id == id } ?: return false
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(ACTIVE, model.id).apply()
        stopLlama()
        return true
    }

    fun removeModel(ctx: Context, id: String): Boolean {
        val model = models(ctx).firstOrNull { it.id == id } ?: return false
        if (installedModel(ctx)?.id == id) stopLlama()
        File(modelDir(ctx), model.file).delete()
        File(modelDir(ctx), model.file + ".part").delete()
        if (!model.builtin) writeCustom(ctx, readCustom(ctx).filterNot { it.id == id })
        val prefs = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        if (prefs.getString(ACTIVE, null) == id) prefs.edit().remove(ACTIVE).apply()
        return true
    }

    @Volatile var downloadProgress: Double = -1.0
    @Volatile var downloadId: String = ""
    @Volatile var downloadBytes: Long = 0
    @Volatile var downloadTotal: Long = 0
    @Volatile var downloadSpeedBps: Long = 0

    fun downloadModel(ctx: Context, id: String) {
        val m = models(ctx).firstOrNull { it.id == id } ?: return
        if (downloadProgress in 0.0..0.99 || isInstalled(ctx, m)) return
        Thread {
            downloadProgress = 0.0; downloadId = id; downloadBytes = 0; downloadTotal = m.bytes; downloadSpeedBps = 0
            try {
                val out = File(modelDir(ctx), m.file)
                val part = File(modelDir(ctx), m.file + ".part")
                if (out.exists()) out.delete()
                var expected = m.bytes
                // Attempts that move NO bytes. A 404 (or a size-0 body, which
                // also left `expected` at 0) used to spin here forever at 3s a
                // go with the UI stuck on "downloading". Real progress resets
                // the budget, so a long resumable download is never cut short.
                var stalled = 0
                val startedAt = System.nanoTime()
                while (expected <= 0 || part.length() < expected) {
                    if (stalled >= 10) throw IllegalStateException(
                        "download made no progress in $stalled attempts - check the model URL")
                    val offset = part.length()
                    val c = URL(m.url).openConnection() as HttpURLConnection
                    if (offset > 0) c.setRequestProperty("Range", "bytes=$offset-")
                    c.connectTimeout = 15_000; c.readTimeout = 30_000
                    if (c.responseCode !in 200..299) { c.disconnect(); stalled++; Thread.sleep(3000); continue }
                    val append = offset > 0 && c.responseCode == HttpURLConnection.HTTP_PARTIAL
                    if (offset > 0 && !append) { part.delete(); c.disconnect(); stalled++; continue }
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
                if (part.length() != expected || !part.renameTo(out)) throw IllegalStateException("could not finalize model")
                if (!m.builtin && m.bytes <= 0) writeCustom(ctx, readCustom(ctx).map { if (it.id == m.id) it.copy(bytes = expected) else it })
                downloadProgress = 1.0
            } catch (_: Exception) { downloadProgress = -1.0; downloadBytes = 0; downloadTotal = 0; downloadSpeedBps = 0; File(modelDir(ctx), m.file + ".part").delete() }
        }.start()
    }

    fun ensureLlama(ctx: Context) {
        if (llamaAlive()) return
        val m = installedModel(ctx) ?: return
        val bin = File(ctx.applicationInfo.nativeLibraryDir, "libllamaserver.so")
        val log = File(ctx.filesDir, "llama-server.log").outputStream()
        llama = ProcessBuilder(bin.absolutePath, "-m", File(modelDir(ctx), m.file).absolutePath, "--host", "127.0.0.1", "--port", "8080", "-c", "16384")
            .apply { redirectErrorStream(true); environment()["LD_LIBRARY_PATH"] = ctx.applicationInfo.nativeLibraryDir }
            .start().also { it.outputStream.use { } }
        Thread { try { llama!!.inputStream.copyTo(log) } catch (_: Exception) {} }.start()
    }

    fun ensureTor(ctx: Context): Boolean {
        if (tor?.isAlive == true) return true
        tor = null
        // Never mistake an arbitrary app's loopback listener for our Tor. If
        // 9050 is occupied, fail closed instead of sending a query through an
        // unverified proxy or racing a second daemon for the same port.
        if (portOpen(9050)) return false
        val dir = File(ctx.filesDir, "tor").apply { mkdirs() }
        File(dir, "torrc").writeText("SocksPort 9050\nDataDirectory ${File(dir, "data").apply { mkdirs() }.absolutePath}\nCookieAuthentication 0\nAvoidDiskWrites 1\nLog notice file ${File(dir, "log").absolutePath}")
        tor = ProcessBuilder(File(ctx.applicationInfo.nativeLibraryDir, "libtor.so").absolutePath, "-f", File(dir, "torrc").absolutePath).redirectErrorStream(true).start()
        Thread { try { tor!!.inputStream.readBytes() } catch (_: Exception) {} }.start()
        return true
    }

    fun awaitTorReady(timeoutMillis: Long = 90_000): Boolean {
        val deadline = System.nanoTime() + timeoutMillis * 1_000_000
        while (System.nanoTime() < deadline) {
            if (torReady()) return true
            if (tor?.isAlive != true) return false
            Thread.sleep(250)
        }
        return torReady()
    }

    private fun stopLlama() { llama?.destroy(); llama = null }
    private fun llamaAlive() = portOpen(8080)
    fun llamaReady() = portOpen(8080)
    fun torReady() = tor?.isAlive == true && portOpen(9050)
    private fun portOpen(port: Int) = try { Socket().use { it.connect(InetSocketAddress("127.0.0.1", port), 300) }; true } catch (_: Exception) { false }
    fun stopAll() { stopLlama(); tor?.destroy() }

    private fun readCustom(ctx: Context): List<Model> = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(CUSTOM, "")!!.lineSequence().mapNotNull {
        // Re-validate on the way OUT too: whatever sits in the store, only a
        // plain name may ever be joined onto modelDir.
        val p = it.split('\t')
        if (p.size == 5 && p[2].matches(FILENAME)) Model(p[0], p[1], p[2], p[3], p[4].toLongOrNull() ?: 0) else null
    }.toList()
    private fun writeCustom(ctx: Context, models: List<Model>) = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(CUSTOM, models.joinToString("\n") { listOf(it.id, it.name, it.file, it.url, it.bytes).joinToString("\t") }).apply()
}
