package org.onionmind.app

import android.content.Context
import org.onionmind.core.EgressLog
import org.onionmind.core.Features
import org.onionmind.core.WorkbenchPreferences
import java.io.File

/** Where the phone keeps the v2 feature flags, preferences and egress log.
 *  All private storage; nothing here is reachable except through the
 *  token-gated API. */
object Workbench {
    private const val UI_PREFS = "onionmind_ui"
    private const val PREFS_JSON = "preferences_json"
    private const val FEATURES_JSON = "features_json"

    private fun store(ctx: Context) = ctx.getSharedPreferences(UI_PREFS, Context.MODE_PRIVATE)

    fun preferences(ctx: Context): WorkbenchPreferences =
        WorkbenchPreferences.fromJson(store(ctx).getString(PREFS_JSON, null))

    fun savePreferences(ctx: Context, prefs: WorkbenchPreferences) {
        store(ctx).edit().putString(PREFS_JSON, prefs.toJson()).apply()
        secureChanged(ctx)
    }

    fun features(ctx: Context): Map<String, Boolean> =
        Features.fromJson(store(ctx).getString(FEATURES_JSON, null))

    fun saveFeatures(ctx: Context, flags: Map<String, Boolean>) {
        store(ctx).edit().putString(FEATURES_JSON, Features.toJson(flags)).apply()
        secureChanged(ctx)
    }

    fun on(ctx: Context, key: String): Boolean = features(ctx)[key] == true

    // --- egress log -------------------------------------------------------
    // Kept only while netlog or boundary is on: the default app writes no
    // record of where it connected, which is itself the privacy choice.

    private fun egressFile(ctx: Context) = File(ctx.filesDir, "egress.jsonl")
    fun egress(ctx: Context) = EgressLog(egressFile(ctx))
    fun egressEnabled(ctx: Context) = features(ctx).let { it["netlog"] == true || it["boundary"] == true }

    fun recordEgress(ctx: Context, route: String, destination: String, kind: String, detail: String = "") {
        if (!egressEnabled(ctx)) return
        try { egress(ctx).append(route, destination, kind, detail) } catch (_: Exception) { }
    }

    // --- private screen (lock) -------------------------------------------
    // FLAG_SECURE keeps the conversation out of the app switcher and
    // screenshots. The activity listens; the server only flips the wish.

    @Volatile var onSecureChanged: ((Boolean) -> Unit)? = null

    fun secureWanted(ctx: Context): Boolean = on(ctx, "lock") && preferences(ctx).secureScreen

    private fun secureChanged(ctx: Context) {
        onSecureChanged?.invoke(secureWanted(ctx))
    }
}
