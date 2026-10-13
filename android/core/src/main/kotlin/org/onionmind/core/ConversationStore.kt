package org.onionmind.core

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import java.io.File

/**
 * The phone's single conversation, persisted on private storage so closing
 * the app or losing the WebView's memory does not lose the chat - encrypted
 * through the [Vault] when one is open. Writes are temp-file-then-rename; a
 * corrupt or missing file loads as an empty conversation rather than crashing
 * the app. An encrypted file is never read or overwritten without the vault.
 */
object ConversationStore {
    private const val FILE_NAME = "conversation.json"

    fun file(dir: File) = File(dir, FILE_NAME)

    fun load(dir: File, vault: Vault? = null): MutableList<JsonObject> {
        val file = File(dir, FILE_NAME)
        if (!file.isFile) return mutableListOf()
        return try {
            var bytes = file.readBytes()
            if (Vault.isEncrypted(bytes)) bytes = vault?.decrypt(bytes) ?: return mutableListOf()
            Json.parseToJsonElement(bytes.toString(Charsets.UTF_8))
                .jsonArray.map { it.jsonObject }.toMutableList()
        } catch (_: Exception) {
            mutableListOf()
        }
    }

    /**
     * lock: delete the conversation when it has not changed for [days] days
     * (0 = keep). Returns true when it deleted one. Age is the file's last
     * write, i.e. the last turn - reading it does not keep it alive.
     */
    fun expire(dir: File, days: Int, now: Long = System.currentTimeMillis()): Boolean {
        if (days <= 0) return false
        val file = File(dir, FILE_NAME)
        if (!file.isFile) return false
        if (now - file.lastModified() < days * 86_400_000L) return false
        return file.delete()
    }

    fun save(dir: File, messages: List<JsonObject>, vault: Vault? = null) {
        dir.mkdirs()
        val file = File(dir, FILE_NAME)
        if (vault == null && file.isFile && Vault.isEncrypted(file.readBytes()))
            throw IllegalStateException("the conversation is encrypted; it can only be written through the open vault")
        val temp = File(dir, "$FILE_NAME.tmp")
        val plain = JsonArray(messages).toString().toByteArray(Charsets.UTF_8)
        temp.writeBytes(vault?.encrypt(plain) ?: plain)
        // renameTo replaces an existing target on Linux but not on Windows,
        // where the tests run; the delete-retry keeps both honest.
        if (!temp.renameTo(file)) {
            file.delete()
            if (!temp.renameTo(file)) temp.delete()
        }
    }
}
