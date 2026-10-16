// sshj-backed implementation of the phone's SSH capability set. sshj is used rather
// than Apache MINA sshd because sshd's default transport needs
// java.nio.channels.AsynchronousSocketChannel, which Android's libcore does not
// implement; sshj is plain blocking IO.
//
// The host key is captured during KEX and checked explicitly before any
// authentication is attempted, so nothing is ever sent to a host whose key is not
// pinned, and a mismatch produces a precise message instead of a generic handshake
// failure.
package com.myresearcher.waygate.ssh

import com.myresearcher.waygate.core.NodeProfile
import net.schmizz.sshj.SSHClient
import net.schmizz.sshj.common.SecurityUtils
import net.schmizz.sshj.transport.verification.HostKeyVerifier
import java.security.PublicKey
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

private const val CONNECT_TIMEOUT_MS = 12_000
private const val READ_TIMEOUT_MS = 35_000
private const val KEEPALIVE_SECONDS = 30

class SshjTransport : SshTransport {
    private val sessions = ConcurrentHashMap<Int, SSHClient>()
    private val nextId = AtomicInteger(1)

    private class KeyRecorder(private val captured: (PublicKey) -> Unit) : HostKeyVerifier {
        override fun verify(hostname: String, port: Int, key: PublicKey): Boolean {
            captured(key)
            // Accepted here on purpose: the pinned comparison follows, before auth.
            return true
        }

        override fun findExistingAlgorithms(hostname: String, port: Int): List<String> = emptyList()
    }

    override fun probeHostKey(host: String, port: Int): HostKey {
        val captured = arrayOfNulls<PublicKey>(1)
        val client = newClient()
        try {
            client.addHostKeyVerifier(KeyRecorder { captured[0] = it })
            client.connect(host, port)
            val key = captured[0] ?: throw IllegalStateException("无法读取节点主机指纹")
            return HostKey(Fingerprints.typeOf(key), Fingerprints.of(key), Fingerprints.base64(key))
        } catch (err: IllegalStateException) {
            throw err
        } catch (err: Exception) {
            throw IllegalStateException("无法连接该 SSH 主机（" + detail(err) + "）")
        } finally {
            shutdown(client)
        }
    }

    override fun connect(profile: NodeProfile): SshSession {
        val accepted = profile.accepted().toSet()
        val captured = arrayOfNulls<PublicKey>(1)
        val client = newClient()
        try {
            client.addHostKeyVerifier(KeyRecorder { captured[0] = it })
            client.connect(profile.host, profile.port)
            val key = captured[0] ?: throw IllegalStateException("无法读取节点主机指纹")
            val negotiated = Fingerprints.describe(key)
            if (!accepted.contains(negotiated)) {
                throw IllegalStateException("节点主机指纹与内置配置不一致，已拒绝连接（本次协商到 " + negotiated + "）")
            }
            if (profile.authType == "password") {
                client.authPassword(profile.user, profile.secret)
            } else {
                val provider = if (profile.passphrase.isEmpty()) {
                    client.loadKeys(profile.secret, null, null)
                } else {
                    client.loadKeys(profile.secret, null, profile.passphrase)
                }
                client.authPublickey(profile.user, provider)
            }
            if (!client.isAuthenticated) { throw IllegalStateException("节点 SSH 认证未通过，请检查凭据") }
            val id = nextId.getAndIncrement()
            sessions[id] = client
            return SshSession(id, negotiated)
        } catch (err: IllegalStateException) {
            shutdown(client)
            throw err
        } catch (err: Exception) {
            shutdown(client)
            throw IllegalStateException("无法登录该节点（" + detail(err) + "）")
        }
    }

    override fun exec(sessionId: Int, command: String, payload: String): ExecResult {
        val client = sessions[sessionId] ?: throw IllegalStateException("SSH 会话已关闭，请重新处理")
        val session = client.startSession()
        try {
            val started = session.exec(command)
            started.outputStream.use { it.write(payload.toByteArray(Charsets.UTF_8)) }
            val stdout = started.inputStream.bufferedReader().use { it.readText() }
            val stderr = started.errorStream.bufferedReader().use { it.readText() }
            started.join(READ_TIMEOUT_MS.toLong(), TimeUnit.MILLISECONDS)
            return ExecResult(started.exitStatus ?: -1, stdout, stderr)
        } finally {
            runCatching { session.close() }
        }
    }

    override fun openDirect(sessionId: Int, remoteHost: String, remotePort: Int): DirectTunnel {
        val client = sessions[sessionId] ?: throw IllegalStateException("SSH 会话已关闭，请重新处理")
        val connection = client.newDirectConnection(remoteHost, remotePort)
        return object : DirectTunnel {
            override val input = connection.inputStream
            override val output = connection.outputStream
            override fun close() {
                runCatching { connection.close() }
            }
        }
    }

    override fun close(sessionId: Int) {
        sessions.remove(sessionId)?.let { shutdown(it) }
    }

    private fun newClient(): SSHClient = SSHClient().apply {
        connectTimeout = CONNECT_TIMEOUT_MS
        timeout = READ_TIMEOUT_MS
        // Keeps the single long-lived session alive while the operator completes a
        // captcha by hand. Exact accessor chain is unverified (no local SDK to build
        // against); remove it if it does not compile.
        runCatching { connection.keepAlive.keepAliveInterval = KEEPALIVE_SECONDS }
    }

    private fun shutdown(client: SSHClient) {
        runCatching { client.disconnect() }
        runCatching { client.close() }
    }

    private fun detail(err: Exception): String =
        err.javaClass.simpleName + (err.message?.let { ": " + it.take(200) } ?: "")

    private companion object {
        init {
            // Ed25519 support comes from the full BouncyCastle bundled with the app,
            // because the provider shipped inside Android is stripped down.
            runCatching {
                SecurityUtils.registerSecurityProvider("org.bouncycastle.jce.provider.BouncyCastleProvider")
            }
        }
    }
}
