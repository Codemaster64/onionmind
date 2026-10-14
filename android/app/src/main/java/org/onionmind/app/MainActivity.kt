package org.onionmind.app

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.view.WindowManager
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebView

class MainActivity : Activity() {
    private lateinit var web: WebView
    private var pendingExport: ByteArray? = null
    private var pendingChooser: ValueCallback<Array<Uri>>? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        // Download progress rides the foreground service's own notification,
        // which Android 13+ still shows without the runtime notification grant,
        // so startup asks for nothing and nothing is requested behind the
        // user's back.
        // lock: with the private screen on, the conversation never shows in
        // the app switcher or in screenshots.
        applySecure(Workbench.secureWanted(this))
        Workbench.onSecureChanged = { wanted -> runOnUiThread { applySecure(wanted) } }
        web = WebView(this)
        web.settings.javaScriptEnabled = true
        web.settings.domStorageEnabled = true
        // Without a chrome client a WebView drops confirm()/alert() and confirm()
        // returns false: the download consent, model removal, clearing and the
        // erase-after-forgotten-password prompts could never be accepted.
        web.webChromeClient = object : WebChromeClient() {
            // Import: the page's <input type=file> opens the system picker.
            override fun onShowFileChooser(view: WebView, callback: ValueCallback<Array<Uri>>,
                                           params: FileChooserParams): Boolean {
                pendingChooser?.onReceiveValue(null)
                pendingChooser = callback
                val pick = Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("*/*")
                return try { startActivityForResult(pick, PICK_IMPORT); true } catch (_: Exception) {
                    pendingChooser = null; false
                }
            }
        }
        // Export: the server sealed the file; the person picks where it goes.
        Workbench.onExport = { bytes -> runOnUiThread { saveExport(bytes) } }
        // Nocturne ground behind the page, so the first frame is not Material grey.
        web.setBackgroundColor(0xFF161826.toInt())
        setContentView(web)
        web.loadUrl(Server.pageUrl())
    }

    private fun saveExport(bytes: ByteArray) {
        pendingExport?.fill(0)
        pendingExport = bytes
        val create = Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE)
            .setType("application/octet-stream").putExtra(Intent.EXTRA_TITLE, "onionmind-export.onionmind")
        try { startActivityForResult(create, SAVE_EXPORT) } catch (_: Exception) { exportDone(false) }
    }

    private fun exportDone(saved: Boolean) {
        pendingExport?.fill(0)
        pendingExport = null
        web.evaluateJavascript("exportSaved($saved)", null)
    }

    @Deprecated("Activity result API needs androidx; this app uses the platform Activity")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        val uri = data?.data.takeIf { resultCode == RESULT_OK }
        when (requestCode) {
            PICK_IMPORT -> {
                pendingChooser?.onReceiveValue(uri?.let { arrayOf(it) })
                pendingChooser = null
            }
            SAVE_EXPORT -> {
                val bytes = pendingExport
                val saved = uri != null && bytes != null && try {
                    contentResolver.openOutputStream(uri, "wt")?.use { it.write(bytes) } != null
                } catch (_: Exception) { false }
                exportDone(saved)
            }
            else -> @Suppress("DEPRECATION") super.onActivityResult(requestCode, resultCode, data)
        }
    }

    private fun applySecure(wanted: Boolean) {
        if (wanted) window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        else window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
    }

    override fun onStart() {
        super.onStart()
        Server.movedToForeground()
    }

    override fun onStop() {
        // With a password set, the data key is wiped after a while out of sight.
        Server.movedToBackground()
        super.onStop()
    }

    override fun onDestroy() {
        Workbench.onSecureChanged = null
        Workbench.onExport = null
        pendingExport?.fill(0)
        // the model server keeps running while the process lives; the OS will
        // reap everything eventually, and every boot resumes cleanly
        super.onDestroy()
    }

    private companion object {
        const val PICK_IMPORT = 1
        const val SAVE_EXPORT = 2
    }
}
