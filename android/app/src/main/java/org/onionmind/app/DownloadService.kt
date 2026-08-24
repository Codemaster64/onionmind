package org.onionmind.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.IBinder
import android.os.PowerManager

/** Hosts a model download so it survives the app being minimized. A plain
 *  background thread gets frozen once the process is cached (and the radio
 *  parked in Doze), which stalled multi-GB downloads the moment the user
 *  switched apps. A foreground service plus a partial wake lock keeps both
 *  the process and the CPU alive until the file lands. */
class DownloadService : Service() {
    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val id = intent?.getStringExtra("id")
        if (id == null) { stopSelf(); return START_NOT_STICKY }
        (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager)
            .createNotificationChannel(NotificationChannel(CHANNEL, "Model download", NotificationManager.IMPORTANCE_LOW))
        startForeground(NOTIFICATION_ID, notification())
        val wake = (getSystemService(Context.POWER_SERVICE) as PowerManager)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "onionmind:download")
            // ponytail: 6h cap so a wedged download can never pin the CPU forever
            .apply { acquire(6 * 60 * 60 * 1000L) }
        val worker = Thread { ProcessManager.runDownload(this, id) }.apply { start() }
        Thread {
            try {
                while (worker.isAlive) {
                    Thread.sleep(2000)
                    (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager)
                        .notify(NOTIFICATION_ID, notification())
                }
            } catch (_: Exception) {
            } finally {
                if (wake.isHeld) wake.release()
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
            }
        }.start()
        return START_NOT_STICKY
    }

    private fun notification(): Notification {
        val done = ProcessManager.downloadBytes / (1024 * 1024)
        val total = ProcessManager.downloadTotal / (1024 * 1024)
        val percent = (ProcessManager.downloadProgress.coerceAtLeast(0.0) * 100).toInt()
        val tap = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        return Notification.Builder(this, CHANNEL)
            .setContentTitle("Downloading model")
            .setContentText(if (total > 0) "$done MB / $total MB" else "$done MB")
            .setSmallIcon(android.R.drawable.stat_sys_download)
            .setProgress(100, percent, total <= 0)
            .setOngoing(true)
            .setContentIntent(tap)
            .build()
    }

    companion object {
        private const val CHANNEL = "download"
        private const val NOTIFICATION_ID = 1
    }
}
