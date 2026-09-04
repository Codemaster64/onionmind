package org.onionmind.core

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import java.io.File

/**
 * The phone's single conversation, persisted as plain JSON on private storage
 * so closing the app or losing the WebView's memory does not lose the chat.
 * Writes are temp-file-then-rename; a corrupt or missing file loads as an
 * empty conversation rather than crashing the app.
 */
object ConversationStore {
    private const val FILE_NAME = "conversation.json"

    fun load(dir: File): MutableList<JsonObject> {
        val file = File(dir, FILE_NAME)
        if (!file.isFile) return mutableListOf()
        return try {
            Json.parseToJsonElement(file.readText())
                .jsonArray.map { it.jsonObject }.toMutableList()
        } catch (_: Exception) {
            mutableListOf()
        }
    }

    fun save(dir: File, messages: List<JsonObject>) {
        dir.mkdirs()
        val file = File(dir, FILE_NAME)
        val temp = File(dir, "$FILE_NAME.tmp")
        temp.writeText(JsonArray(messages).toString())
        // renameTo replaces an existing target on Linux but not on Windows,
        // where the tests run; the delete-retry keeps both honest.
        if (!temp.renameTo(file)) {
            file.delete()
            if (!temp.renameTo(file)) temp.delete()
        }
    }
}
