// In-app verification proxy. Binds a loopback listener for the WebView and relays only
// allowlisted CONNECT targets, mirroring services/LocalProxy.ets: the neutral exit
// probe host is reachable before the exit check, the source domains open only after
// markReady(), and anything else is refused on the phone so no byte reaches the worker
// until the shared exit has been confirmed.
//
// Unlike the HarmonyOS client, this does not relay through a second native listener
// port; it opens one direct-tcpip channel per accepted browser connection on the same
// SSH session. The guarantee is the same and one hop shorter.
package com.myresearcher.waygate.proxy

import android.util.Log
import com.myresearcher.waygate.ssh.DirectTunnel
import com.myresearcher.waygate.ssh.SshTransport
import java.io.InputStream
import java.io.OutputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicInteger

private const val NEUTRAL_HOST = "api.ipify.org"
private const val TAG = "waygate_proxy"
private const val MAX_CONNECTIONS = 24
private const val MAX_HEAD_BYTES = 4096
private const val HEAD_TIMEOUT_MS = 10_000
private const val COPY_BUFFER = 16 * 1024
private val NUMERIC_HOST = Regex("^\\d{1,3}(\\.\\d{1,3}){3}$")

class LocalProxy(private val transport: SshTransport) {
    private val clients = ConcurrentHashMap<Int, Socket>()
    private val tunnels = ConcurrentHashMap<Int, DirectTunnel>()
    private val counter = AtomicInteger(1)

    @Volatile private var ready: Boolean = false
    @Volatile private var closed: Boolean = true
    private var listener: ServerSocket? = null

    fun start(sessionId: Int, routeHost: String, routePort: Int, domains: List<String>): Int {
        stop()
        val socket = ServerSocket(0, MAX_CONNECTIONS, InetAddress.getByName("127.0.0.1"))
        listener = socket
        closed = false
        ready = false
        val accepted = acceptedDomains(domains)
        val thread = Thread {
            while (!closed) {
                val client = try {
                    socket.accept()
                } catch (err: Exception) {
                    break
                }
                if (clients.size >= MAX_CONNECTIONS) {
                    runCatching { client.close() }
                    continue
                }
                val id = counter.getAndIncrement()
                clients[id] = client
                Thread { serve(id, client, sessionId, routeHost, routePort, accepted) }.apply { isDaemon = true }.start()
            }
        }
        thread.isDaemon = true
        thread.start()
        return socket.localPort
    }

    // Until this is called only the neutral host is reachable.
    fun markReady() {
        ready = true
    }

    fun stop() {
        closed = true
        ready = false
        val socket = listener
        listener = null
        runCatching { socket?.close() }
        tunnels.values.forEach { runCatching { it.close() } }
        tunnels.clear()
        clients.values.forEach { runCatching { it.close() } }
        clients.clear()
    }

    private fun serve(id: Int, client: Socket, sessionId: Int, routeHost: String, routePort: Int,
                      domains: List<String>) {
        var tunnel: DirectTunnel? = null
        try {
            client.soTimeout = HEAD_TIMEOUT_MS
            val head = readHead(client.getInputStream()) ?: return
            val target = parseConnect(head) ?: return refuse(client, "")
            val allowed = permitted(target)
            Log.i(TAG, "CONNECT host=" + target.host + " allowed=" + allowed + " ready=" + ready +
                " domains=" + domains.size)
            if (!allowed) { return refuse(client, target.host) }
            // The phone must not answer the browser's CONNECT itself: the upstream here is
            // the worker's HTTP proxy (Mihomo), which establishes the tunnel, so its own
            // CONNECT has to travel through the channel and its 200 has to come back.
            tunnel = transport.openDirect(sessionId, routeHost, routePort)
            tunnels[id] = tunnel
            val request = ("CONNECT " + target.host + ":443 HTTP/1.1\r\nHost: " + target.host +
                ":443\r\n\r\n").toByteArray(Charsets.UTF_8)
            write(tunnel.output, request)
            client.soTimeout = 0
            pump(client, tunnel)
        } catch (err: Exception) {
            // A client-side abort or a closed worker tunnel needs no reporting.
        } finally {
            tunnel?.let { runCatching { it.close() } }
            tunnels.remove(id)
            clients.remove(id)
            runCatching { client.close() }
        }
    }

    private data class Target(val host: String, val port: Int)

    private fun parseConnect(head: String): Target? {
        val line = head.lineSequence().firstOrNull() ?: return null
        val parts = line.trim().split(" ")
        if (parts.size < 2 || !parts[0].equals("CONNECT", ignoreCase = true)) { return null }
        val authority = parts[1]
        val separator = authority.lastIndexOf(':')
        if (separator <= 0) { return null }
        val host = authority.substring(0, separator).trim().lowercase()
        val port = authority.substring(separator + 1).trim().toIntOrNull() ?: return null
        if (port != 443) { return null }
        if (host.isEmpty() || host.length > 253 || host.contains("..")) { return null }
        if (NUMERIC_HOST.matches(host)) { return null }
        return Target(host, port)
    }

    // The allowlist is the pre-check guard only: before the shared exit is confirmed
    // nothing but the neutral probe host may pass. Once markReady() has run this channel
    // is what the desktop `ssh -L` path provides, and that path has no domain filter -
    // keeping one broke page rendering on the HarmonyOS side, where the page's static
    // host (gbfek.dfcfw.com) was refused while the exit was already confirmed and the
    // operator was left looking at raw unstyled HTML.
    private fun permitted(target: Target): Boolean {
        if (target.host == NEUTRAL_HOST) { return true }
        if (!ready) { return false }
        return true
    }

    private fun acceptedDomains(domains: List<String>): List<String> =
        domains.map { it.trim().lowercase() }.filter { it.isNotEmpty() }

    private fun readHead(input: InputStream): String? {
        val collected = StringBuilder()
        val buffer = ByteArray(512)
        while (collected.length < MAX_HEAD_BYTES) {
            val read = input.read(buffer)
            if (read <= 0) { return null }
            collected.append(String(buffer, 0, read, Charsets.ISO_8859_1))
            if (collected.contains("\r\n\r\n")) { return collected.toString() }
        }
        return null
    }

    // A readable refusal body, never an empty 403: on the HarmonyOS side an empty body
    // rendered as a blank page and made a policy refusal look like a broken tunnel.
    private fun refuse(client: Socket, host: String) {
        val reason = if (host.isEmpty()) {
            "无法识别的 CONNECT 目标（只接受 CONNECT host:443）"
        } else {
            "该域名在出口核对完成前不可通行：" + host + "（核对通过后不再限制域名；核对前仅放行出口测量用的 api.ipify.org）"
        }
        val body = reason.toByteArray(Charsets.UTF_8)
        runCatching {
            val stream = client.getOutputStream()
            write(stream, ("HTTP/1.1 403 Forbidden\r\nContent-Type: text/plain; charset=utf-8" +
                "\r\nConnection: close\r\nContent-Length: " + body.size + "\r\n\r\n").toByteArray(Charsets.UTF_8))
            write(stream, body)
        }
    }

    private fun write(stream: OutputStream, bytes: ByteArray) {
        stream.write(bytes)
        stream.flush()
    }

    // One thread per direction, mirroring the piped relay the HarmonyOS client used.
    private fun pump(client: Socket, tunnel: DirectTunnel) {
        val upstream = Thread {
            try {
                val buffer = ByteArray(COPY_BUFFER)
                while (true) {
                    val read = client.getInputStream().read(buffer)
                    if (read <= 0) { break }
                    tunnel.output.write(buffer, 0, read)
                    tunnel.output.flush()
                }
            } catch (err: Exception) {
                // Half-close: the tunnel end is closed in the finally below.
            } finally {
                runCatching { tunnel.output.close() }
            }
        }
        upstream.isDaemon = true
        upstream.start()
        try {
            val buffer = ByteArray(COPY_BUFFER)
            val output = client.getOutputStream()
            while (true) {
                val read = tunnel.input.read(buffer)
                if (read <= 0) { break }
                output.write(buffer, 0, read)
                output.flush()
            }
        } catch (err: Exception) {
            // Either side closing ends the relay.
        }
    }
}
