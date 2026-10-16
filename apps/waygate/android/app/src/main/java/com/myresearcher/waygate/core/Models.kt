// Data shapes mirrored field-for-field from the HarmonyOS client (services/Models.ets)
// so both phone apps speak identical hub and worker contracts. Parsing is done with
// org.json and is deliberately lenient: the hub may send string or number for the
// target identity fields, exactly as the ArkTS side tolerated.
package com.myresearcher.waygate.core

import org.json.JSONArray
import org.json.JSONObject

// ---- hub feed -----------------------------------------------------------------

data class Incident(
    val id: String,
    val node: String,
    val name: String,
    val stock: String,
    val kind: String,
    val title: String,
    val created: Long,
    val seen: Long,
    val snoozed: Long,
    val online: Boolean,
    val canVerify: Boolean,
) {
    companion object {
        fun from(o: JSONObject) = Incident(o.optString("id"), o.optString("node"), o.optString("name"),
            o.optString("stock"), o.optString("kind"), o.optString("title"), o.optLong("created"),
            o.optLong("seen"), o.optLong("snoozed"), o.optBoolean("online"), o.optBoolean("can_verify"))
    }
}

data class StockState(val stock: String, val state: String, val window: String, val posts: String, val pending: String) {
    companion object {
        fun from(o: JSONObject) = StockState(o.optString("stock"), o.optString("state"), o.optString("window"),
            o.optString("posts"), o.optString("pending"))
    }
}

data class NodeState(val id: String, val name: String, val online: Boolean, val state: String, val stocks: List<StockState>) {
    companion object {
        fun from(o: JSONObject) = NodeState(o.optString("id"), o.optString("name"), o.optBoolean("online"),
            o.optString("state"), o.optJSONArray("stocks").objects(StockState::from))
    }
}

data class Inbox(
    val items: List<Incident>,
    val nodes: List<NodeState>,
    val lastPoll: Long,
    val error: String,
    val pushEnabled: Boolean,
    val pushErrors: Int,
) {
    companion object {
        fun from(o: JSONObject) = Inbox(o.optJSONArray("items").objects(Incident::from),
            o.optJSONArray("nodes").objects(NodeState::from), o.optLong("last_poll"), o.optString("error"),
            o.optBoolean("push_enabled"), o.optInt("push_errors"))
    }
}

data class SessionResult(val message: String, val exitIp: String, val expiresAt: Long, val requestId: Long, val outcome: String) {
    companion object {
        fun from(o: JSONObject) = SessionResult(o.optString("message"), o.optString("exit_ip"), o.optLong("expires_at"),
            o.optLong("request_id"), o.optString("outcome"))
    }
}

data class Session(val id: String, val phase: String, val result: SessionResult) {
    companion object {
        fun from(o: JSONObject) = Session(o.optString("id"), o.optString("phase"),
            SessionResult.from(o.optJSONObject("result") ?: JSONObject()))
    }
}

data class Credentials(
    val hub: String,
    val token: String,
    val deviceId: String,
    val push: Boolean,
    val pendingClaim: String,
)

// ---- phone-side direct SSH verification ---------------------------------------

data class RouteInfo(val host: String, val port: Int, val endpoint: String, val mode: String, val signature: String) {
    companion object {
        fun from(o: JSONObject) = RouteInfo(o.optString("host"), o.optInt("port"), o.optString("endpoint"),
            o.optString("mode"), o.optString("signature"))
    }

    fun toJson(): JSONObject = JSONObject()
        .put("host", host).put("port", port).put("endpoint", endpoint).put("mode", mode).put("signature", signature)
}

// One blocked task. The descriptor only carries identity; url/route come from audit.
// instance_id/revision/job/stock/task/request_id may arrive as either string or number,
// so they are normalised to text and compared on the text form.
data class DirectTarget(
    val instanceId: String,
    val revision: String,
    val job: String,
    val stock: String,
    val task: String,
    val requestId: String,
    val url: String,
    val route: RouteInfo?,
    val exitIp: String,
    val detached: Boolean,
) {
    companion object {
        fun from(o: JSONObject) = DirectTarget(o.optString("instance_id"), o.optString("revision"),
            o.optString("job"), o.optString("stock"), o.optString("task"), o.optString("request_id"),
            o.optString("url"), o.optJSONObject("route")?.let(RouteInfo::from), o.optString("exit_ip"),
            o.optBoolean("detached"))

        fun same(left: String, right: String) = left == right
    }

    fun sameIdentity(row: DirectTarget) = job == row.job && stock == row.stock && task == row.task && requestId == row.requestId

    fun adopts(view: AuditView, row: DirectTarget) = copy(instanceId = view.instanceId, revision = view.revision,
        job = row.job, stock = row.stock, task = row.task, requestId = row.requestId, url = row.url, route = row.route)

    fun toJson(): JSONObject = JSONObject()
        .put("instance_id", instanceId).put("revision", revision).put("job", job).put("stock", stock)
        .put("task", task).put("request_id", requestId)
        .apply { if (url.isNotEmpty()) put("url", url) }
        .apply { route?.let { put("route", it.toJson()) } }
        .apply { if (exitIp.isNotEmpty()) put("exit_ip", exitIp) }
        .apply { if (detached) put("detached", true) }
}

data class VerificationDescriptor(
    val protocol: String,
    val nodeId: String,
    val sshHostHint: String?,
    val target: DirectTarget,
    val workerCommand: String,
    val sourceDomains: List<String>,
    val sessionTtlSeconds: Long,
) {
    companion object {
        fun from(o: JSONObject) = VerificationDescriptor(o.optString("protocol"), o.optString("node_id"),
            if (o.isNull("ssh_host_hint")) null else o.optString("ssh_host_hint").takeIf { it.isNotEmpty() },
            DirectTarget.from(o.optJSONObject("target") ?: JSONObject()), o.optString("worker_command"),
            o.optJSONArray("source_domains").strings(), o.optLong("session_ttl_seconds"))
    }
}

data class AuditStock(val job: String, val stock: String, val state: String) {
    companion object {
        fun from(o: JSONObject) = AuditStock(o.optString("job"), o.optString("stock"), o.optString("state"))
    }
}

data class AuditView(
    val state: String,
    val instanceId: String,
    val revision: String,
    val targets: List<DirectTarget>,
    val stocks: List<AuditStock>,
    val inflight: Boolean,
    val probePending: Boolean,
) {
    companion object {
        fun from(o: JSONObject) = AuditView(o.optString("state"), o.optString("instance_id"), o.optString("revision"),
            o.optJSONArray("targets").objects(DirectTarget::from), o.optJSONArray("stocks").objects(AuditStock::from),
            o.optBoolean("inflight"), o.optBoolean("probe_pending"))
    }
}

data class PrepareValue(val pausedStocks: List<String>) {
    companion object {
        fun from(o: JSONObject) = PrepareValue(o.optJSONArray("paused_stocks").strings())
    }
}

data class ExitValue(val route: RouteInfo?, val exitIp: String) {
    companion object {
        fun from(o: JSONObject) = ExitValue(o.optJSONObject("route")?.let(RouteInfo::from), o.optString("exit_ip"))
    }
}

data class ProbeValue(val beforeRequestId: Long) {
    companion object {
        fun from(o: JSONObject) = ProbeValue(o.optLong("before_request_id"))
    }
}

data class ProbeRow(val id: Long, val outcome: String)

data class ResultValue(val done: Boolean, val request: ProbeRow?) {
    companion object {
        fun from(o: JSONObject) = ResultValue(o.optBoolean("done"), o.optJSONObject("request")?.let {
            ProbeRow(it.optLong("id"), it.optString("outcome"))
        })
    }
}

data class ResumeValue(val started: Boolean) {
    companion object {
        fun from(o: JSONObject) = ResumeValue(o.optBoolean("started"))
    }
}

// The worker always answers {"ok":true,"value":...} or {"ok":false,"error":"..."}.
data class Envelope(val ok: Boolean, val value: JSONObject?, val error: String)

// Durable record written before the one-shot probe leaves the phone.
data class DirectClaim(
    val incident: String,
    val node: String,
    val target: String,
    val workerCommand: String,
    val beforeRequestId: Long,
    val at: Long,
) {
    fun toJson(): JSONObject = JSONObject().put("incident", incident).put("node", node).put("target", target)
        .put("worker_command", workerCommand).put("before_request_id", beforeRequestId).put("at", at)

    companion object {
        fun from(o: JSONObject) = DirectClaim(o.optString("incident"), o.optString("node"), o.optString("target"),
            o.optString("worker_command"), o.optLong("before_request_id"), o.optLong("at"))
    }
}

// SSH login material for one node. Stored only in this phone's secure store.
//
// pinned holds every "<type> SHA256:<b64>" the node is allowed to present. The
// HarmonyOS client only compared the raw negotiated key bytes recorded at import
// time, which rejected a node when a later connection negotiated one of its other
// pinned key types; carrying the accepted set keeps the same guarantee without that
// failure mode. knownHostBase64 is kept for contract parity with that client.
data class NodeProfile(
    val nodeId: String,
    val host: String,
    val port: Int,
    val user: String,
    val authType: String,
    val secret: String,
    val passphrase: String,
    val knownHostBase64: String,
    val knownHostType: String,
    val fingerprint: String,
    val pinned: List<String>,
) {
    fun accepted(): List<String> = pinned.ifEmpty { listOf(knownHostType + " " + fingerprint) }

    fun toJson(): JSONObject = JSONObject()
        .put("nodeId", nodeId).put("host", host).put("port", port).put("user", user).put("authType", authType)
        .put("secret", secret).put("passphrase", passphrase).put("knownHostBase64", knownHostBase64)
        .put("knownHostType", knownHostType).put("fingerprint", fingerprint)
        .put("pinned", JSONArray(pinned))

    companion object {
        fun from(o: JSONObject) = NodeProfile(o.optString("nodeId"), o.optString("host"), o.optInt("port", 22),
            o.optString("user", "root"), o.optString("authType", "key"), o.optString("secret"),
            o.optString("passphrase"), o.optString("knownHostBase64"), o.optString("knownHostType"),
            o.optString("fingerprint"), o.optJSONArray("pinned").strings())
    }
}

// Packaged channel configuration: endpoint, pinned host keys and the name of the
// credential file next to this config. The credential itself is git-ignored and is
// bundled only when present in the local build.
data class BuiltinNode(
    val id: String,
    val label: String,
    val host: String,
    val port: Int,
    val user: String,
    val authType: String,
    val hostKeys: List<String>,
    val secretFile: String,
) {
    companion object {
        fun from(o: JSONObject) = BuiltinNode(o.optString("id"), o.optString("label"), o.optString("host"),
            o.optInt("port", 22), o.optString("user", "root"), o.optString("authType", "key"),
            o.optJSONArray("hostKeys").strings(), o.optString("secretFile"))
    }
}

data class BuiltinConfig(val nodes: List<BuiltinNode>) {
    companion object {
        fun from(o: JSONObject) = BuiltinConfig(o.optJSONArray("nodes").objects(BuiltinNode::from))
    }
}

// ---- org.json helpers ---------------------------------------------------------

fun <T> JSONArray?.objects(transform: (JSONObject) -> T): List<T> = buildList {
    val array = this@objects ?: return@buildList
    for (index in 0 until array.length()) { array.optJSONObject(index)?.let { add(transform(it)) } }
}

fun JSONArray?.strings(): List<String> = buildList {
    val array = this@strings ?: return@buildList
    for (index in 0 until array.length()) { array.optString(index, "").takeIf { it.isNotEmpty() }?.let { add(it) } }
}
