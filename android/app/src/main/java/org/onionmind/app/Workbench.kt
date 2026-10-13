package org.onionmind.app

import android.content.Context
import android.content.SharedPreferences
import org.onionmind.core.ConversationStore
import org.onionmind.core.EgressLog
import org.onionmind.core.Features
import org.onionmind.core.Vault
import org.onionmind.core.WorkbenchPreferences
import java.io.File

/** Where the phone keeps the v2 feature flags, preferences and egress log.
 *  All private storage, all encrypted through the vault; nothing here is
 *  reachable except through the token-gated API. */
object Workbench {
    private const val UI_PREFS = "onionmind_ui"
    private const val PREFS_JSON = "preferences_json"
    private const val FEATURES_JSON = "features_json"
    /** Every SharedPreferences file whose values are encrypted. */
    val SECRET_PREFS = listOf(UI_PREFS, "models")

    private fun store(ctx: Context) = ctx.getSharedPreferences(UI_PREFS, Context.MODE_PRIVATE)

    // --- encryption at rest ------------------------------------------------
    // Opened at process start without a password; with one, the app stays
    // locked (every data route answers 423) until /api/vault/unlock.

    @Volatile var vault: Vault? = null
        private set
    @Volatile var vaultProblem: String? = null
        private set

    fun locked(): Boolean = vault == null

    /** Open (or create) the vault; a password is needed only when one is set. */
    @Synchronized
    fun openVault(ctx: Context, password: String? = null): Vault {
        val opened = Vault.open(ctx.filesDir, KeystoreWrap, password)
        vault = opened
        vaultProblem = null
        encryptLeftovers(ctx, opened)
        secureChanged(ctx)
        return opened
    }

    /**
     * Lock: the key is wiped from memory and every data route answers 423
     * until the password is typed again. Only with a password - without one
     * the Android Keystore would hand the key straight back.
     */
    @Synchronized
    fun lock(): Boolean {
        val open = vault ?: return false
        if (!open.hasPassword) return false
        vault = null
        open.close()
        return true
    }

    /** At process start: open without a password when none is set. */
    fun startVault(ctx: Context) {
        try {
            if (!Vault.needsPassword(ctx.filesDir)) openVault(ctx)
        } catch (e: Exception) {
            vaultProblem = e.message ?: "The encrypted data could not be opened."
        }
    }

    /** The files a forgotten password erases (models and Tor stay). */
    fun dataFiles(ctx: Context): List<File> =
        listOf(ConversationStore.file(ctx.filesDir), egressFile(ctx), File(ctx.filesDir, "llama-server.log"))

    @Synchronized
    fun eraseAll(ctx: Context) {
        Vault.erase(ctx.filesDir, KeystoreWrap, dataFiles(ctx))
        for (name in SECRET_PREFS) ctx.getSharedPreferences(name, Context.MODE_PRIVATE).edit().clear().commit()
        vault = null
        openVault(ctx)
    }

    /** What an older version left in plaintext is encrypted now. */
    private fun encryptLeftovers(ctx: Context, vault: Vault) {
        val conversation = ConversationStore.file(ctx.filesDir)
        for (file in listOf(conversation, egressFile(ctx))) {
            if (file.isFile && !Vault.isEncrypted(file.readBytes())) Vault.writeAtomic(file, vault.encrypt(file.readBytes()))
        }
        for (name in SECRET_PREFS) {
            val prefs = ctx.getSharedPreferences(name, Context.MODE_PRIVATE)
            val editor = prefs.edit()
            for ((key, value) in prefs.all) {
                if (value is String && !value.startsWith(Vault.LINE)) editor.putString(key, vault.encryptString(value))
            }
            editor.commit()
        }
    }

    /** A SharedPreferences string through the vault; null while locked or missing. */
    fun readSecret(prefs: SharedPreferences, key: String): String? {
        val raw = prefs.getString(key, null) ?: return null
        if (!raw.startsWith(Vault.LINE)) return raw
        return try { vault?.decryptString(raw) } catch (_: Exception) { null }
    }

    /** Store a value encrypted. While locked nothing is written, never plaintext over ciphertext. */
    fun writeSecret(prefs: SharedPreferences, key: String, value: String) {
        val open = vault ?: return
        prefs.edit().putString(key, open.encryptString(value)).apply()
    }

    fun preferences(ctx: Context): WorkbenchPreferences =
        WorkbenchPreferences.fromJson(readSecret(store(ctx), PREFS_JSON))

    fun savePreferences(ctx: Context, prefs: WorkbenchPreferences) {
        writeSecret(store(ctx), PREFS_JSON, prefs.toJson())
        secureChanged(ctx)
    }

    fun features(ctx: Context): Map<String, Boolean> =
        Features.fromJson(readSecret(store(ctx), FEATURES_JSON))

    fun saveFeatures(ctx: Context, flags: Map<String, Boolean>) {
        writeSecret(store(ctx), FEATURES_JSON, Features.toJson(flags))
        secureChanged(ctx)
    }

    fun on(ctx: Context, key: String): Boolean = features(ctx)[key] == true

    // --- egress log -------------------------------------------------------
    // Kept only while netlog or boundary is on: the default app writes no
    // record of where it connected, which is itself the privacy choice.

    private fun egressFile(ctx: Context) = File(ctx.filesDir, "egress.jsonl")
    fun egress(ctx: Context) = EgressLog(egressFile(ctx), vault = vault)
    fun egressEnabled(ctx: Context) = features(ctx).let { it["netlog"] == true || it["boundary"] == true }

    fun recordEgress(ctx: Context, route: String, destination: String, kind: String, detail: String = "") {
        if (locked() || !egressEnabled(ctx)) return
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
