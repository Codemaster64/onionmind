package org.onionmind.app

import android.app.Activity
import android.os.Build
import android.os.Bundle
import android.view.WindowManager
import android.webkit.WebView

class MainActivity : Activity() {
    private lateinit var web: WebView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        // Download progress lives in a notification once the app is minimized;
        // the download itself runs either way, this only makes it visible.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU)
            requestPermissions(arrayOf(android.Manifest.permission.POST_NOTIFICATIONS), 0)
        web = WebView(this)
        web.settings.javaScriptEnabled = true
        web.settings.domStorageEnabled = true
        setContentView(web)
        web.loadUrl("http://127.0.0.1:8081/")
    }

    override fun onDestroy() {
        // the model server keeps running while the process lives; the OS will
        // reap everything eventually, and every boot resumes cleanly
        super.onDestroy()
    }
}
