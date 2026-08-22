package org.onionmind.app

import android.app.Application

/** Boots the whole stack once per process: the local HTTP server, then the
 *  native binaries (llama-server, tor) as needed. The UI is just a WebView
 *  pointed at the local server - all logic lives in :core and Server.kt. */
class App : Application() {
    override fun onCreate() {
        super.onCreate()
        Server.start(this)
        Thread { ProcessManager.ensureTor(this) }.start()   // circuits take a minute on a phone
    }
}
