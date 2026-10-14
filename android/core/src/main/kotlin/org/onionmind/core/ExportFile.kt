package org.onionmind.core

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.put
import java.security.SecureRandom
import java.util.Base64
import javax.crypto.AEADBadTagException
import javax.crypto.Cipher
import javax.crypto.SecretKeyFactory
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.PBEKeySpec
import javax.crypto.spec.SecretKeySpec

/**
 * The export file, the phone's half of onionmind_desktop_core's export: one
 * password-protected file with the conversations and settings, which opens on
 * either side with the password alone. Layout:
 *   OMEXPORT1\n  <header JSON, one line>\n  <12-byte nonce><AES-256-GCM ciphertext>
 * The magic and header line are the associated data, so the KDF parameters
 * cannot be weakened without breaking the tag. tests/export-sample.onionmind
 * is written by the desktop and read by ExportFileTest.
 */
object ExportFile {
    const val ITERATIONS = 600_000
    private val MAGIC = "OMEXPORT1\n".toByteArray(Charsets.US_ASCII)
    private const val NONCE = 12
    private val random = SecureRandom()

    class Invalid(message: String) : Exception(message)

    fun write(password: String, payload: JsonObject): ByteArray {
        require(password.isNotEmpty()) { "The export password cannot be empty." }
        val salt = ByteArray(16).also(random::nextBytes)
        val nonce = ByteArray(NONCE).also(random::nextBytes)
        val header = buildJsonObject {
            put("format", "onionmind-export"); put("version", 1); put("kdf", "PBKDF2WithHmacSHA256")
            put("iterations", ITERATIONS); put("salt", Base64.getEncoder().encodeToString(salt))
        }
        val preamble = MAGIC + header.toString().toByteArray(Charsets.US_ASCII) + '\n'.code.toByte()
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, SecretKeySpec(key(password, salt, ITERATIONS), "AES"), GCMParameterSpec(128, nonce))
        cipher.updateAAD(preamble)
        return preamble + nonce + cipher.doFinal(payload.toString().toByteArray(Charsets.UTF_8))
    }

    /** The payload; throws [Vault.WrongPassword] for a wrong password or a changed file, [Invalid] otherwise. */
    fun read(blob: ByteArray, password: String): JsonObject {
        if (blob.size < MAGIC.size || !blob.copyOfRange(0, MAGIC.size).contentEquals(MAGIC))
            throw Invalid("This is not an Onionmind export file.")
        var end = MAGIC.size
        while (end < blob.size && blob[end] != '\n'.code.toByte()) end++
        if (end >= blob.size) throw Invalid("The export file is damaged.")
        val header = try {
            Json.parseToJsonElement(blob.copyOfRange(MAGIC.size, end).toString(Charsets.US_ASCII)).jsonObject
        } catch (_: Exception) { throw Invalid("The export file is damaged.") }
        fun text(name: String) = (header[name] as? JsonPrimitive)?.contentOrNull
        if (text("format") != "onionmind-export" || (header["version"] as? JsonPrimitive)?.intOrNull != 1)
            throw Invalid("This export was made by a newer Onionmind; update to import it.")
        val iterations = (header["iterations"] as? JsonPrimitive)?.intOrNull ?: 0
        if (text("kdf") != "PBKDF2WithHmacSHA256" || iterations !in 100_000..10_000_000)
            throw Invalid("The export file names a key derivation this version does not accept.")
        val salt = try { Base64.getDecoder().decode(text("salt") ?: "") } catch (_: Exception) {
            throw Invalid("The export file is damaged.")
        }
        val start = end + 1
        if (blob.size < start + NONCE + 16) throw Invalid("The export file is damaged.")
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, SecretKeySpec(key(password, salt, iterations), "AES"),
            GCMParameterSpec(128, blob.copyOfRange(start, start + NONCE)))
        cipher.updateAAD(blob.copyOfRange(0, start))
        val plain = try { cipher.doFinal(blob, start + NONCE, blob.size - start - NONCE) } catch (_: AEADBadTagException) {
            throw Vault.WrongPassword("That password does not open this export, or the file was changed.")
        }
        return try { Json.parseToJsonElement(plain.toString(Charsets.UTF_8)).jsonObject } catch (_: Exception) {
            throw Invalid("The export file is damaged.")
        }
    }

    private fun key(password: String, salt: ByteArray, iterations: Int): ByteArray {
        val spec = PBEKeySpec(password.toCharArray(), salt, iterations, 256)
        try {
            return SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256").generateSecret(spec).encoded
        } finally {
            spec.clearPassword()
        }
    }
}
