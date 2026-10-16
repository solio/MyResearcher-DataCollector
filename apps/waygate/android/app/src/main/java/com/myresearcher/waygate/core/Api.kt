// Hub REST client mirroring services/Api.ets: the same URL join, headers, 12s/35s
// timeouts, error classification, token redaction and truncation, and the same
// structured failure record that the diagnostics screen shows. Call it off the
// main thread; every method blocks on network IO.
package com.myresearcher.waygate.core

import org.json.JSONException
import org.json.JSONObject
import java.io.IOException
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URL
import java.nio.charset.StandardCharsets
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

private const val CONNECT_TIMEOUT_MS = 12_000
private const val READ_TIMEOUT_MS = 35_000
private const val MAX_BODY_BYTES = 1 shl 20

private val JSON_FAILURE_HINTS =
    Regex("Unterminated string|JSONDecodeError|Unexpected end of JSON|Expecting .*delimiter", RegexOption.IGNORE_CASE)

fun readableError(message: String): String =
    if (JSON_FAILURE_HINTS.containsMatchIn(message)) "主控状态数据解析失败；详情见“诊断”" else message

class Api(val credentials: Credentials) {
    var lastFailureDetails: String = ""

    fun get(path: String): JSONObject = request(path, "GET", "")

    fun post(path: String, body: String = "{}"): JSONObject = request(path, "POST", body)

    fun request(path: String, method: String, body: String): JSONObject {
        var status = 0
        var characters = 0
        var stage = "连接主控"
        try {
            val connection = (URL(credentials.hub + path).openConnection() as HttpURLConnection).apply {
                requestMethod = method
                connectTimeout = CONNECT_TIMEOUT_MS
                readTimeout = READ_TIMEOUT_MS
                useCaches = false
                instanceFollowRedirects = false
                setRequestProperty("Authorization", "Bearer " + credentials.token)
                setRequestProperty("Content-Type", "application/json")
            }
            try {
                if (method != "GET") {
                    connection.doOutput = true
                    connection.outputStream.use { it.write(body.toByteArray(StandardCharsets.UTF_8)) }
                }
                status = connection.responseCode
                val text = readBody(connection, status)
                characters = text.length
                stage = "HTTP 响应"
                if (status < 200 || status >= 300) {
                    throw IllegalStateException(failureMessage(text, status))
                }
                stage = "解析 JSON"
                return JSONObject(text)
            } finally {
                connection.disconnect()
            }
        } catch (err: JSONException) {
            recordFailure(path, method, stage, status, characters, "")
            throw IllegalStateException("主控返回的数据不完整或格式错误；详情见“诊断”")
        } catch (err: Exception) {
            recordFailure(path, method, stage, status, characters, describe(err))
            if (stage == "连接主控") {
                throw IllegalStateException("无法连接主控，请检查网络；详情见“诊断”")
            }
            throw IllegalStateException(err.message ?: "主控请求未完成；详情见“诊断”")
        }
    }

    private fun failureMessage(text: String, status: Int): String {
        var message = "主控请求失败：HTTP " + status
        try {
            val error = JSONObject(text).optString("error")
            if (error.isNotEmpty()) { message = readableError(error) }
        } catch (_: JSONException) {
            // A body that is not JSON keeps the generic HTTP message.
        }
        val redacted = if (credentials.token.isNotEmpty()) message.replace(credentials.token, "[已隐藏]") else message
        return redacted.take(400)
    }

    private fun readBody(connection: HttpURLConnection, status: Int): String {
        val stream: InputStream? = if (status in 200..299) connection.inputStream else connection.errorStream
        if (stream == null) { return "" }
        return stream.use { input ->
            val buffer = ByteArray(16 * 1024)
            val collected = ArrayList<Byte>(1024)
            var total = 0
            while (total < MAX_BODY_BYTES) {
                val read = input.read(buffer)
                if (read <= 0) { break }
                val keep = minOf(read, MAX_BODY_BYTES - total)
                for (index in 0 until keep) { collected.add(buffer[index]) }
                total += keep
            }
            String(collected.toByteArray(), StandardCharsets.UTF_8)
        }
    }

    private fun recordFailure(path: String, method: String, stage: String, status: Int, characters: Int, network: String) {
        lastFailureDetails = buildString {
            append("记录时间：").append(SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.getDefault()).format(Date()))
            append("\n接口：").append(method).append(' ').append(path.substringBefore('?'))
            append("\n阶段：").append(stage)
            append("\nHTTP：").append(if (status > 0) status.toString() else "未收到响应")
            append("\n响应长度：").append(characters).append(" 个字符")
            if (network.isNotEmpty()) { append("\n网络错误：").append(network.take(200)) }
        }
    }

    private fun describe(err: Exception): String = when (err) {
        is IOException -> err.javaClass.simpleName + (err.message?.let { ": " + it } ?: "")
        else -> ""
    }
}
