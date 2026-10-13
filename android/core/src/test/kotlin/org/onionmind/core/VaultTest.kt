package org.onionmind.core

import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.io.File
import java.security.SecureRandom
import javax.crypto.Cipher
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec
import kotlin.io.path.createTempDirectory
import kotlin.test.Test
import kotlin.test.assertContentEquals
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

/** A stand-in for the Android Keystore: a wrapping key that only lives here. */
class MemoryDeviceWrap : Vault.DeviceKeyWrap {
    var wrapping: ByteArray? = null
    override val label = "a test key store"

    override fun wrap(key: ByteArray): ByteArray {
        val k = wrapping ?: ByteArray(32).also(SecureRandom()::nextBytes).also { wrapping = it }
        val iv = ByteArray(12).also(SecureRandom()::nextBytes)
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(Cipher.ENCRYPT_MODE, SecretKeySpec(k, "AES"), GCMParameterSpec(128, iv))
        return iv + c.doFinal(key)
    }

    override fun unwrap(blob: ByteArray): ByteArray? {
        val k = wrapping ?: return null
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(Cipher.DECRYPT_MODE, SecretKeySpec(k, "AES"), GCMParameterSpec(128, blob.copyOfRange(0, 12)))
        return try { c.doFinal(blob, 12, blob.size - 12) } catch (_: Exception) { null }
    }

    override fun delete() { wrapping = null }
}

class VaultTest {
    private val secret = "a private thing said on the phone"
    private fun dir(): File = createTempDirectory("onionmind-vault").toFile()
    private fun user(text: String) = buildJsonObject { put("role", "user"); put("content", text) }
    private fun noPlaintext(dir: File) = dir.walk().filter { it.isFile }.forEach {
        assertFalse(it.readBytes().toString(Charsets.ISO_8859_1).contains(secret), it.name)
    }

    @Test
    fun `the conversation and the network log are stored encrypted and read back`() {
        val dir = dir(); val device = MemoryDeviceWrap()
        val vault = Vault.open(dir, device)
        ConversationStore.save(dir, listOf(user(secret)), vault)
        val log = EgressLog(File(dir, "egress.jsonl"), vault = vault)
        log.append(EgressLog.TOR, secret.replace(" ", "."), "search")
        noPlaintext(dir)
        assertFalse(File(dir, "egress.jsonl").readText().contains(secret.replace(" ", ".")))
        assertEquals(secret, (ConversationStore.load(dir, vault)[0]["content"] as JsonPrimitive).content)
        assertEquals(1, log.entries().size)
        assertTrue(Vault.isEncrypted(File(dir, "conversation.json").readBytes()))
    }

    @Test
    fun `reopening uses the device key`() {
        val dir = dir(); val device = MemoryDeviceWrap()
        ConversationStore.save(dir, listOf(user(secret)), Vault.open(dir, device))
        val again = Vault.open(dir, device)
        assertEquals(1, ConversationStore.load(dir, again).size)
        assertFalse(again.hasPassword)
    }

    @Test
    fun `an older plaintext conversation still loads, and is encrypted on its next save`() {
        val dir = dir(); val device = MemoryDeviceWrap()
        ConversationStore.save(dir, listOf(user(secret)))
        val vault = Vault.open(dir, device)
        val messages = ConversationStore.load(dir, vault)
        assertEquals(1, messages.size)
        ConversationStore.save(dir, messages, vault)
        noPlaintext(dir)
    }

    @Test
    fun `without the vault encrypted data reads as empty and is never overwritten`() {
        val dir = dir(); val device = MemoryDeviceWrap()
        val vault = Vault.open(dir, device)
        ConversationStore.save(dir, listOf(user(secret)), vault)
        val before = File(dir, "conversation.json").readBytes()
        assertTrue(ConversationStore.load(dir).isEmpty())
        assertFailsWith<IllegalStateException> { ConversationStore.save(dir, listOf(user("downgrade"))) }
        assertContentEquals(before, File(dir, "conversation.json").readBytes())
        EgressLog(File(dir, "egress.jsonl"), vault = vault).append(EgressLog.TOR, "x", "search")
        assertFailsWith<IllegalStateException> { EgressLog(File(dir, "egress.jsonl")).append(EgressLog.TOR, "y", "search") }
    }

    @Test
    fun `a password replaces the device key and gates opening`() {
        val dir = dir(); val device = MemoryDeviceWrap()
        val vault = Vault.open(dir, device)
        ConversationStore.save(dir, listOf(user(secret)), vault)
        vault.setPassword("correct horse")
        assertNull(device.wrapping)                    // nothing on the phone opens it now
        assertTrue(Vault.needsPassword(dir))
        assertFalse(File(dir, Vault.FILE).readText().contains("correct horse"))
        assertFailsWith<Vault.WrongPassword> { Vault.open(dir, device) }
        assertFailsWith<Vault.WrongPassword> { Vault.open(dir, device, "wrong") }
        val opened = Vault.open(dir, device, "correct horse")
        assertEquals(1, ConversationStore.load(dir, opened).size)
        assertTrue(opened.checkPassword("correct horse"))
        assertFalse(opened.checkPassword("nope"))
    }

    @Test
    fun `changing the password needs the current one`() {
        val dir = dir(); val device = MemoryDeviceWrap()
        val vault = Vault.open(dir, device)
        vault.setPassword("one")
        assertFailsWith<Vault.WrongPassword> { vault.setPassword("two", current = "wrong") }
        vault.setPassword("two", current = "one")
        assertFailsWith<Vault.WrongPassword> { Vault.open(dir, device, "one") }
        Vault.open(dir, device, "two")
    }

    @Test
    fun `removing the password hands the key back to the device`() {
        val dir = dir(); val device = MemoryDeviceWrap()
        val vault = Vault.open(dir, device)
        ConversationStore.save(dir, listOf(user(secret)), vault)
        vault.setPassword("pw")
        assertFailsWith<Vault.WrongPassword> { vault.removePassword("wrong") }
        vault.removePassword("pw")
        assertFalse(Vault.needsPassword(dir))
        assertEquals(1, ConversationStore.load(dir, Vault.open(dir, device)).size)
    }

    @Test
    fun `a lost device key is reported, never replaced`() {
        val dir = dir(); val device = MemoryDeviceWrap()
        Vault.open(dir, device)
        device.delete()
        assertFailsWith<Vault.Unavailable> { Vault.open(dir, device) }
        assertTrue(File(dir, Vault.FILE).isFile)
    }

    @Test
    fun `erase removes the data, the header and the key`() {
        val dir = dir(); val device = MemoryDeviceWrap()
        val vault = Vault.open(dir, device)
        ConversationStore.save(dir, listOf(user(secret)), vault)
        Vault.erase(dir, device, listOf(ConversationStore.file(dir), File(dir, "egress.jsonl")))
        assertFalse(File(dir, Vault.FILE).exists())
        assertFalse(ConversationStore.file(dir).exists())
        assertNull(device.wrapping)
    }

    @Test
    fun `a changed file is refused`() {
        val dir = dir(); val device = MemoryDeviceWrap()
        val vault = Vault.open(dir, device)
        val blob = vault.encrypt(secret.toByteArray())
        blob[blob.size - 1] = (blob[blob.size - 1].toInt() xor 1).toByte()
        assertFailsWith<Vault.Unavailable> { vault.decrypt(blob) }
    }

    @Test
    fun `text values round trip and plaintext passes through`() {
        val vault = Vault.open(dir(), MemoryDeviceWrap())
        val stored = vault.encryptString(secret)
        assertTrue(stored.startsWith(Vault.LINE))
        assertFalse(stored.contains(secret))
        assertEquals(secret, vault.decryptString(stored))
        assertEquals("{\"plain\":1}", vault.decryptString("{\"plain\":1}"))
    }

    @Test
    fun `the file format matches the desktop`() {
        // OMENC1\n + 12-byte nonce + AES-GCM, AAD "onionmind/v1": the same bytes
        // onionmind_desktop_core.Vault writes, so the two can never drift apart.
        val vault = Vault.open(dir(), MemoryDeviceWrap())
        val blob = vault.encrypt(byteArrayOf(1, 2, 3))
        assertContentEquals("OMENC1\n".toByteArray(), blob.copyOfRange(0, 7))
        assertEquals(7 + 12 + 3 + 16, blob.size)
    }
}
