package org.onionmind.app

import android.app.Application

/** Starts only the app-local HTTP bridge. Native processes and external
 *  network access remain off until the user requests an action that needs
 *  them. The UI is a WebView pointed at the local server. */
class App : Application() {
    override fun onCreate() {
        super.onCreate()
        Server.start(this)
    }
}
