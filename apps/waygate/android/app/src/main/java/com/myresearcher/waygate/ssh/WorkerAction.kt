// Run one existing worker action over SSH exec: one JSON payload on stdin, then EOF,
// and the same {"ok":...,"value":...} contract the desktop command uses. Mirrors
// runAction in services/NodeSsh.ets, including the wording of every failure.
package com.myresearcher.waygate.ssh

import org.json.JSONException
import org.json.JSONObject

fun <T> runAction(transport: SshTransport, sessionId: Int, command: String, payload: String,
                  parse: (JSONObject) -> T): T {
    val result = transport.exec(sessionId, command, payload)
    if (result.exitStatus != 0) {
        val detail = result.stderr.trim()
        throw IllegalStateException("验证命令在该节点执行失败" +
            if (detail.isNotEmpty()) "：" + detail.take(200) else "（退出码 " + result.exitStatus + "）")
    }
    val parsed = try {
        JSONObject(result.stdout.trim())
    } catch (err: JSONException) {
        throw IllegalStateException("节点返回的验证结果无法识别；请在控制台核对")
    }
    if (!parsed.optBoolean("ok")) {
        val message = parsed.optString("error")
        throw IllegalStateException(if (message.isNotEmpty()) message.take(300) else "验证命令未完成；请在控制台核对")
    }
    val value = parsed.optJSONObject("value")
        ?: throw IllegalStateException("节点没有返回验证结果；请在控制台核对")
    return parse(value)
}
