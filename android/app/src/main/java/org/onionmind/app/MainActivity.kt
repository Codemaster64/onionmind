package org.onionmind.app

import android.app.Activity
import android.os.Bundle
import android.view.WindowManager
import android.webkit.WebChromeClient
import android.webkit.WebView

class MainActivity : Activity() {
    private lateinit var web: WebView

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
        web.webChromeClient = WebChromeClient()
        // Nocturne ground behind the page, so the first frame is not Material grey.
        web.setBackgroundColor(0xFF161826.toInt())
        setContentView(web)
        web.loadUrl(Server.pageUrl())
    }

    private fun applySecure(wanted: Boolean) {
        if (wanted) window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        else window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
    }

    override fun onDestroy() {
        Workbench.onSecureChanged = null
        // the model server keeps running while the process lives; the OS will
        // reap everything eventually, and every boot resumes cleanly
        super.onDestroy()
    }
}
