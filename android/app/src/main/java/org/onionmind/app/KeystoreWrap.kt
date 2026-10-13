package org.onionmind.app

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import org.onionmind.core.Vault
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * The vault's device key wrap: an AES-256-GCM key created inside the Android
 * Keystore (the TEE or StrongBox where the phone has one). The key itself
 * never leaves it; this app can only ask it to wrap or unwrap the data key,
 * and it is not part of any backup, so a copied backup cannot be opened.
 */
object KeystoreWrap : Vault.DeviceKeyWrap {
    private const val ALIAS = "onionmind-data-key-wrap"
    private const val PROVIDER = "AndroidKeyStore"
    override val label = "the Android Keystore"

    private fun keyStore() = KeyStore.getInstance(PROVIDER).apply { load(null) }

    private fun existing(): SecretKey? = (keyStore().getEntry(ALIAS, null) as? KeyStore.SecretKeyEntry)?.secretKey

    private fun create(): SecretKey = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, PROVIDER).run {
        init(KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setKeySize(256)
            .build())
        generateKey()
    }

    override fun wrap(key: ByteArray): ByteArray {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        // The keystore picks the IV itself (randomized encryption is enforced).
        cipher.init(Cipher.ENCRYPT_MODE, existing() ?: create())
        return cipher.iv + cipher.doFinal(key)
    }

    override fun unwrap(blob: ByteArray): ByteArray? {
        return try {
            val key = existing() ?: return null
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(128, blob.copyOfRange(0, 12)))
            cipher.doFinal(blob, 12, blob.size - 12)
        } catch (_: Exception) {
            null
        }
    }

    override fun delete() {
        try { keyStore().deleteEntry(ALIAS) } catch (_: Exception) { }
    }
}
