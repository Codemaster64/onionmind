package org.onionmind.app

import android.app.Activity
import android.os.Bundle
import android.view.WindowManager
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
        web = WebView(this)
        web.settings.javaScriptEnabled = true
        web.settings.domStorageEnabled = true
        setContentView(web)
        web.loadUrl(Server.pageUrl())
    }

    override fun onDestroy() {
        // the model server keeps running while the process lives; the OS will
        // reap everything eventually, and every boot resumes cleanly
        super.onDestroy()
    }
}
