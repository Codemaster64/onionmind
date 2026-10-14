package org.onionmind.core

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.put
import java.io.File
import java.security.SecureRandom
import java.util.Base64
import javax.crypto.AEADBadTagException
import javax.crypto.Cipher
import javax.crypto.SecretKeyFactory
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.PBEKeySpec
import javax.crypto.spec.SecretKeySpec

/**
 * Encryption at rest, the phone's half of the desktop vault
 * (onionmind_desktop_core.py): the same AES-256-GCM file format under one
 * random data key. vault.json is the only plaintext file and holds no secret.
 *
 * Without a password the data key is wrapped by [DeviceKeyWrap] - on the
 * phone an Android Keystore key that never leaves the secure hardware - so a
 * copied backup or a rooted read of the files gets ciphertext only. With a
 * password the key is wrapped with PBKDF2 instead and the device wrap is
 * deleted: nothing on the phone can open the data until the password is typed,
 * and a forgotten password means the data is gone. No recovery, by design.
 */
class Vault private constructor(
    private val dir: File,
    private val key: ByteArray,
    header: JsonObject,
    private val device: DeviceKeyWrap,
) {
    /** What wraps the data key when there is no password. */
    interface DeviceKeyWrap {
        val label: String
        fun wrap(key: ByteArray): ByteArray
        /** null when the wrapping key is gone (app data cleared, device reset). */
        fun unwrap(blob: ByteArray): ByteArray?
        fun delete()
    }

    class WrongPassword(message: String) : Exception(message)
    class Unavailable(message: String) : Exception(message)

    @Volatile var header: JsonObject = header
        private set

    val hasPassword: Boolean get() = header["password"] is JsonObject

    @Volatile var closed: Boolean = false
        private set

    /**
     * Overwrite the key in memory. Only the password (through [open]) brings it
     * back; after this every encrypt or decrypt throws [Unavailable], so nothing
     * is ever written under a zeroed key.
     */
    @Synchronized
    fun close() {
        key.fill(0)
        closed = true
    }

    @Synchronized
    fun encrypt(plain: ByteArray): ByteArray = seal(liveKey(), plain, AAD)

    @Synchronized
    fun decrypt(blob: ByteArray): ByteArray {
        require(isEncrypted(blob)) { "not an Onionmind encrypted file" }
        return open(liveKey(), blob, AAD) ?: throw Unavailable("the file was not encrypted with this key, or it was changed")
    }

    private fun liveKey(): ByteArray = if (closed) throw Unavailable("Onionmind is locked") else key

    /** One value for a text store (SharedPreferences): "enc1:" + base64. */
    fun encryptString(text: String): String =
        LINE + Base64.getEncoder().encodeToString(encrypt(text.toByteArray(Charsets.UTF_8)))

    /** The plaintext of [value]; a value an older version stored in plain text passes through. */
    fun decryptString(value: String): String =
        if (!value.startsWith(LINE)) value
        else decrypt(Base64.getDecoder().decode(value.removePrefix(LINE))).toString(Charsets.UTF_8)

    fun checkPassword(password: String): Boolean {
        val record = header["password"] as? JsonObject ?: return false
        return try { unwrapWithPassword(record, password).contentEquals(key) } catch (_: Exception) { false }
    }

    /** Set or change the password; changing needs the current one. */
    fun setPassword(password: String, current: String? = null) {
        require(password.isNotEmpty()) { "The password cannot be empty." }
        if (hasPassword && !checkPassword(current.orEmpty())) throw WrongPassword("The current password is not right.")
        val next = JsonObject(header + mapOf(
            "mode" to JsonPrimitive("password"),
            "password" to wrapWithPassword(key, password),
        ) - "device")
        writeHeader(dir, next)                          // the password wrap first...
        header = next
        device.delete()                                 // ...then the device copy goes
    }

    /** Back to the device key: the data stays encrypted, no password asked. */
    fun removePassword(current: String) {
        if (!checkPassword(current)) throw WrongPassword("That password is not right.")
        val next = JsonObject(header + mapOf(
            "mode" to JsonPrimitive("device"),
            "device" to JsonPrimitive(b64(device.wrap(key))),
        ) - "password")
        writeHeader(dir, next)
        header = next
    }

    fun describe(): String = if (hasPassword)
        "Encrypted (AES-256-GCM). The key is unlocked by your password, which is never stored."
    else "Encrypted (AES-256-GCM). The key is kept in ${device.label}."

    companion object {
        const val FILE = "vault.json"
        const val LINE = "enc1:"
        const val PBKDF2_ITERATIONS = 310_000
        private val MAGIC = "OMENC1\n".toByteArray(Charsets.US_ASCII)
        private val AAD = "onionmind/v1".toByteArray(Charsets.US_ASCII)
        private val KEY_AAD = "onionmind/key".toByteArray(Charsets.US_ASCII)
        private const val NONCE = 12
        private val random = SecureRandom()

        fun isEncrypted(blob: ByteArray): Boolean =
            blob.size >= MAGIC.size && blob.copyOfRange(0, MAGIC.size).contentEquals(MAGIC)

        fun header(dir: File): JsonObject? {
            val file = File(dir, FILE)
            if (!file.isFile) return null
            val value = try { Json.parseToJsonElement(file.readText()).jsonObject } catch (e: Exception) {
                throw Unavailable("vault.json is unreadable")
            }
            if ((value["version"] as? JsonPrimitive)?.intOrNull != 1) throw Unavailable("vault.json is not a version this app understands")
            return value
        }

        fun needsPassword(dir: File): Boolean = header(dir)?.get("password") is JsonObject

        /**
         * Open the vault in [dir], creating it the first time (a new random key,
         * wrapped by [device]). Throws [WrongPassword] when a password is set and
         * [password] is missing or wrong, [Unavailable] when the device key is gone.
         * Never falls back to plaintext on its own.
         */
        fun open(dir: File, device: DeviceKeyWrap, password: String? = null): Vault {
            val existing = header(dir)
            if (existing == null) {
                val key = ByteArray(32).also(random::nextBytes)
                val created = buildJsonObject {
                    put("version", 1); put("cipher", "AES-256-GCM"); put("mode", "device")
                    put("device", b64(device.wrap(key)))
                }
                writeHeader(dir, created)
                return Vault(dir, key, created, device)
            }
            val record = existing["password"] as? JsonObject
            if (record != null) {
                if (password == null) throw WrongPassword("A password is needed to open Onionmind's data.")
                return Vault(dir, unwrapWithPassword(record, password), existing, device)
            }
            val wrapped = (existing["device"] as? JsonPrimitive)?.contentOrNull
                ?: throw Unavailable("vault.json names no key")
            val key = device.unwrap(unb64(wrapped))
                ?: throw Unavailable("The data key is missing from ${device.label}. The encrypted data cannot be read without it.")
            return Vault(dir, key, existing, device)
        }

        /** Forgotten password: delete the encrypted files, the header and the device key. */
        fun erase(dir: File, device: DeviceKeyWrap, files: Iterable<File>) {
            for (file in files) file.delete()
            File(dir, FILE).delete()
            device.delete()
        }

        private fun seal(key: ByteArray, plain: ByteArray, aad: ByteArray): ByteArray {
            val nonce = ByteArray(NONCE).also(random::nextBytes)
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.ENCRYPT_MODE, SecretKeySpec(key, "AES"), GCMParameterSpec(128, nonce))
            cipher.updateAAD(aad)
            return MAGIC + nonce + cipher.doFinal(plain)
        }

        private fun open(key: ByteArray, blob: ByteArray, aad: ByteArray): ByteArray? {
            val start = MAGIC.size
            if (blob.size < start + NONCE + 16) return null
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, SecretKeySpec(key, "AES"),
                GCMParameterSpec(128, blob.copyOfRange(start, start + NONCE)))
            cipher.updateAAD(aad)
            return try { cipher.doFinal(blob, start + NONCE, blob.size - start - NONCE) } catch (_: AEADBadTagException) { null }
        }

        private fun kek(password: String, salt: ByteArray, iterations: Int): ByteArray {
            val spec = PBEKeySpec(password.toCharArray(), salt, iterations, 256)
            try {
                return SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256").generateSecret(spec).encoded
            } finally {
                spec.clearPassword()
            }
        }

        private fun wrapWithPassword(key: ByteArray, password: String): JsonObject {
            val salt = ByteArray(16).also(random::nextBytes)
            val sealed = seal(kek(password, salt, PBKDF2_ITERATIONS), key, KEY_AAD)
            return buildJsonObject {
                put("kdf", "PBKDF2WithHmacSHA256"); put("iterations", PBKDF2_ITERATIONS)
                put("salt", b64(salt)); put("wrapped", b64(sealed))
            }
        }

        private fun unwrapWithPassword(record: JsonObject, password: String): ByteArray {
            fun field(name: String) = (record[name] as? JsonPrimitive)?.contentOrNull
                ?: throw Unavailable("vault.json is damaged")
            if (field("kdf") != "PBKDF2WithHmacSHA256") throw Unavailable("vault.json names an unknown key derivation")
            val iterations = field("iterations").toIntOrNull() ?: throw Unavailable("vault.json is damaged")
            return open(kek(password, unb64(field("salt")), iterations), unb64(field("wrapped")), KEY_AAD)
                ?: throw WrongPassword("That password is not right.")
        }

        private fun writeHeader(dir: File, header: JsonObject) = writeAtomic(File(dir, FILE), header.toString().toByteArray())

        /** Temp file then rename, as ConversationStore does. */
        fun writeAtomic(file: File, bytes: ByteArray) {
            file.parentFile?.mkdirs()
            val temp = File(file.path + ".tmp")
            temp.writeBytes(bytes)
            if (!temp.renameTo(file)) {
                file.delete()
                if (!temp.renameTo(file)) { temp.delete(); throw java.io.IOException("could not write ${file.name}") }
            }
        }

        private fun b64(bytes: ByteArray) = Base64.getEncoder().encodeToString(bytes)
        private fun unb64(text: String) = Base64.getDecoder().decode(text)
    }
}

/**
 * Back-off after wrong passwords: 1 s, then 2, 4, 8 ... up to 5 minutes, reset
 * by a right one. The API is already token-gated against other apps; this
 * keeps guessing slow even for a caller that has the token.
 */
class PasswordThrottle(private val now: () -> Long = System::currentTimeMillis) {
    private var failures = 0
    private var until = 0L

    /** How long to refuse password checks for; 0 when one may be tried now. */
    @Synchronized
    fun waitMillis(): Long = maxOf(0L, until - now())

    @Synchronized
    fun failed() {
        failures++
        until = now() + minOf(1000L shl minOf(failures - 1, 9), MAX_WAIT_MS)
    }

    @Synchronized
    fun succeeded() {
        failures = 0
        until = 0L
    }

    companion object {
        const val MAX_WAIT_MS = 300_000L
    }
}
