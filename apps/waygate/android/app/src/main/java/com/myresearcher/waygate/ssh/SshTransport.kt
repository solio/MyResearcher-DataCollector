// The SSH capability set the phone needs, mirroring the HarmonyOS native bridge
// (probeHostKey / connect / exec / forward / close). Everything here blocks; call it
// from a worker thread.
package com.myresearcher.waygate.ssh

import com.myresearcher.waygate.core.NodeProfile
import java.io.InputStream
import java.io.OutputStream

data class HostKey(val type: String, val fingerprint: String, val base64: String)

data class ExecResult(val exitStatus: Int, val stdout: String, val stderr: String)

data class SshSession(val id: Int, val fingerprint: String)

// One direct-tcpip channel. The proxy owns the byte pumping so the domain allowlist
// is applied on the phone before any byte reaches the worker.
interface DirectTunnel {
    val input: InputStream
    val output: OutputStream
    fun close()
}

interface SshTransport {
    // TCP connect + KEX only: no authentication, no worker interaction.
    fun probeHostKey(host: String, port: Int): HostKey

    // Verifies the negotiated host key against profile.accepted(), then authenticates.
    fun connect(profile: NodeProfile): SshSession

    // One JSON payload on stdin followed by EOF; returns the raw channel result.
    fun exec(sessionId: Int, command: String, payload: String): ExecResult

    fun openDirect(sessionId: Int, remoteHost: String, remotePort: Int): DirectTunnel

    fun close(sessionId: Int)
}
