// Android has no equivalent of the HarmonyOS Asset Store, so secrets are sealed with
// an AES-GCM key that never leaves the Android Keystore. Failures keep the same
// "unlock and retry" / "cannot store securely" wording the ArkTS client used.
package com.myresearcher.waygate.core

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

object SecureStore {
    private const val KEYSTORE = "AndroidKeyStore"
    private const val ALIAS = "waygate-secrets-v1"
    private const val TRANSFORM = "AES/GCM/NoPadding"
    private const val TAG_BITS = 128
    private const val PREFERENCES = "waygate-sealed"

    fun put(context: Context, name: String, value: String) {
        try {
            val cipher = Cipher.getInstance(TRANSFORM)
            cipher.init(Cipher.ENCRYPT_MODE, key())
            val sealed = cipher.iv + cipher.doFinal(value.toByteArray(Charsets.UTF_8))
            prefs(context).edit().putString(name, Base64.encodeToString(sealed, Base64.NO_WRAP)).apply()
        } catch (err: Exception) {
            throw IllegalStateException("无法安全保存主控凭据")
        }
    }

    fun get(context: Context, name: String): String? {
        val stored = prefs(context).getString(name, null) ?: return null
        try {
            val sealed = Base64.decode(stored, Base64.NO_WRAP)
            if (sealed.size <= 12) { return null }
            val cipher = Cipher.getInstance(TRANSFORM)
            cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(TAG_BITS, sealed, 0, 12))
            return String(cipher.doFinal(sealed, 12, sealed.size - 12), Charsets.UTF_8)
        } catch (err: Exception) {
            throw IllegalStateException("系统凭据存储不可用，请解锁后重试")
        }
    }

    fun delete(context: Context, name: String) {
        prefs(context).edit().remove(name).apply()
    }

    private fun prefs(context: Context) =
        context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)

    private fun key(): SecretKey {
        val store = KeyStore.getInstance(KEYSTORE).apply { load(null) }
        (store.getEntry(ALIAS, null) as? KeyStore.SecretKeyEntry)?.let { return it.secretKey }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KEYSTORE)
        generator.init(
            KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .setRandomizedEncryptionRequired(true)
                .build()
        )
        return generator.generateKey()
    }
}
