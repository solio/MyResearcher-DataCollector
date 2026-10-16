// Host key fingerprints in the same shape ssh-keygen prints, so the values pinned in
// assets/channel.json can be compared character for character: "SHA256:" followed by
// the unpadded base64 of SHA-256 over the SSH wire-format public key blob.
package com.myresearcher.waygate.ssh

import net.schmizz.sshj.common.Buffer
import net.schmizz.sshj.common.KeyType
import java.security.MessageDigest
import java.security.PublicKey
import java.util.Base64

object Fingerprints {

    fun of(key: PublicKey): String {
        val buffer = Buffer.PlainBuffer()
        KeyType.fromKey(key).putPubKeyIntoBuffer(key, buffer)
        val digest = MessageDigest.getInstance("SHA-256").digest(buffer.compactData)
        return "SHA256:" + Base64.getEncoder().withoutPadding().encodeToString(digest)
    }

    // Extra-curricular key types sshd may offer (for example ssh-dss) have no
    // KeyType in sshj, so they are reported as "unknown" and cannot be pinned.
    fun typeOf(key: PublicKey): String = try {
        KeyType.fromKey(key).toString()
    } catch (err: Exception) {
        "unknown"
    }

    fun describe(key: PublicKey): String = typeOf(key) + " " + of(key)

    // The raw blob kept for compatibility with the HarmonyOS profile shape.
    fun base64(key: PublicKey): String {
        val buffer = Buffer.PlainBuffer()
        KeyType.fromKey(key).putPubKeyIntoBuffer(key, buffer)
        return Base64.getEncoder().encodeToString(buffer.compactData)
    }
}
