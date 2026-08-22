package org.onionmind.app

import android.content.Context
import java.io.File
import java.io.FileOutputStream
import java.net.HttpURLConnection
import java.net.InetSocketAddress
import java.net.Socket
import java.net.URL

/** Runs llama-server and tor as child processes, from the app's own native
 *  library dir (they are ordinary executables; extractNativeLibs keeps them
 *  real files on disk). Android will kill the app eventually - everything is
 *  written to survive that and restart cleanly. */
object ProcessManager {

    private var llama: Process? = null
    private var tor: Process? = null

    data class Model(val tier: String, val file: String, val url: String, val bytes: Long)

    fun models() = listOf(
        Model("lfm", "LFM2.5-2.6B-heretic-Q4_K_M.gguf",
              "https://huggingface.co/Abiray/LFM2.5-2.6B-Heretic-Abliterated-GGUF/resolve/main/LFM2.5-2.6B-heretic-Q4_K_M.gguf",
              1_674_454_432L),
        Model("4b", "Huihui-Qwen3.5-4B-abliterated.Q4_K_M.gguf",
              "https://huggingface.co/mradermacher/Huihui-Qwen3.5-4B-abliterated-GGUF/resolve/main/Huihui-Qwen3.5-4B-abliterated.Q4_K_M.gguf",
              2_707_514_688L),
        Model("9b", "Huihui-Qwen3.5-9B-abliterated.Q4_K_M.gguf",
              "https://huggingface.co/mradermacher/Huihui-Qwen3.5-9B-abliterated-GGUF/resolve/main/Huihui-Qwen3.5-9B-abliterated.Q4_K_M.gguf",
              5_627_045_248L),
    )

    fun modelDir(ctx: Context) = File(ctx.filesDir, "models").apply { mkdirs() }
    fun installedModel(ctx: Context): Model? =
        models().firstOrNull { File(modelDir(ctx), it.file).length() == it.bytes }

    @Volatile var downloadProgress: Double = -1.0   // -1 none, 0..1 active, 1 done
    @Volatile var downloadTier: String = ""

    fun downloadModel(ctx: Context, tier: String) {
        val m = models().firstOrNull { it.tier == tier } ?: return
        if (downloadProgress in 0.0..0.99) return          // one download at a time
        Thread {
            downloadProgress = 0.0; downloadTier = tier
            try {
                val out = File(modelDir(ctx), m.file)
                // A previous build could have left a truncated final name.
                // Never treat it as resumable or let it block finalization.
                if (out.exists() && out.length() != m.bytes) out.delete()
                val part = File(modelDir(ctx), m.file + ".part")
                while (part.length() < m.bytes) {
                    val offset = part.length()
                    val c = URL(m.url).openConnection() as HttpURLConnection
                    if (offset > 0) c.setRequestProperty("Range", "bytes=$offset-")
                    if (c.responseCode !in 200..299) { Thread.sleep(3000); continue }  // resume loop
                    // Some mirrors ignore Range and return the whole object.
                    // Appending that response would corrupt the resumable file.
                    val append = offset > 0 && c.responseCode == HttpURLConnection.HTTP_PARTIAL
                    if (offset > 0 && !append) part.delete()
                    c.inputStream.use { input ->
                        FileOutputStream(part, append).use { output ->
                            input.copyTo(output, 1 shl 16)
                        }
                    }
                }
                if (!part.renameTo(out)) throw IllegalStateException("could not finalize model")
                downloadProgress = 1.0
            } catch (e: Exception) {
                downloadProgress = -1.0
                File(modelDir(ctx), m.file + ".part").let { if (it.exists()) it.delete() }
            }
        }.start()
    }

    fun ensureLlama(ctx: Context) {
        if (llamaAlive()) return
        val m = installedModel(ctx) ?: return
        val bin = File(ctx.applicationInfo.nativeLibraryDir, "libllamaserver.so")
        val log = File(ctx.filesDir, "llama-server.log").outputStream()
        llama = ProcessBuilder(
            bin.absolutePath, "-m", File(modelDir(ctx), m.file).absolutePath,
            "--host", "127.0.0.1", "--port", "8080", "-c", "8192",
        ).apply {
            redirectErrorStream(true)
            // the engine's shared libs live in the app's nativeLibraryDir
            environment()["LD_LIBRARY_PATH"] = ctx.applicationInfo.nativeLibraryDir
        }.start().also { it.outputStream.use { } /* close stdin */ }
        // hold the log stream open for the process lifetime via a reader thread
        Thread {
            try { llama!!.inputStream.copyTo(log) } catch (_: Exception) {}
        }.start()
    }

    fun ensureTor(ctx: Context) {
        if (torAlive()) return
        val dir = File(ctx.filesDir, "tor").apply { mkdirs() }
        File(dir, "torrc").writeText(
            """
            SocksPort 9050
            DataDirectory ${File(dir, "data").apply { mkdirs() }.absolutePath}
            CookieAuthentication 0
            AvoidDiskWrites 1
            Log notice file ${File(dir, "log").absolutePath}
            """.trimIndent()
        )
        val bin = File(ctx.applicationInfo.nativeLibraryDir, "libtor.so")
        tor = ProcessBuilder(bin.absolutePath, "-f", File(dir, "torrc").absolutePath)
            .redirectErrorStream(true).start()
        Thread { try { tor!!.inputStream.readBytes() } catch (_: Exception) {} }.start()
    }

    private fun llamaAlive(): Boolean = portOpen(8080)
    private fun torAlive(): Boolean = portOpen(9050)
    fun llamaReady(): Boolean = portOpen(8080)
    fun torReady(): Boolean = portOpen(9050)

    private fun portOpen(port: Int): Boolean = try {
        Socket().use { s -> s.connect(InetSocketAddress("127.0.0.1", port), 300) }
        true
    } catch (_: Exception) { false }

    fun stopAll() {
        llama?.destroy(); tor?.destroy()
    }
}
