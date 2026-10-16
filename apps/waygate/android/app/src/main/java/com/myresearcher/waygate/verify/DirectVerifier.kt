// The phone-side verification flow, mirroring the order and the wording of
// verify()/begin()/finish()/resumeClaim() in pages/Index.ets. Everything runs on a
// single worker thread (SSH, HTTP and the WebView waits all block); the host callbacks
// are expected to marshal onto the UI thread themselves, and the two blocking ones
// (applyProxyOverride, measureExit) must block until their WebView callback fires.
package com.myresearcher.waygate.verify

import com.myresearcher.waygate.core.Api
import com.myresearcher.waygate.core.AuditView
import com.myresearcher.waygate.core.BundledChannel
import com.myresearcher.waygate.core.DirectClaim
import com.myresearcher.waygate.core.DirectTarget
import com.myresearcher.waygate.core.ExitValue
import com.myresearcher.waygate.core.Incident
import com.myresearcher.waygate.core.NodeProfile
import com.myresearcher.waygate.core.NodeProfiles
import com.myresearcher.waygate.core.PrepareValue
import com.myresearcher.waygate.core.ProbeValue
import com.myresearcher.waygate.core.ResultValue
import com.myresearcher.waygate.core.ResumeValue
import com.myresearcher.waygate.core.Vault
import com.myresearcher.waygate.core.VerificationDescriptor
import com.myresearcher.waygate.runtime.Runtime
import com.myresearcher.waygate.ssh.runAction
import org.json.JSONObject
import java.util.concurrent.Executors

private const val NEUTRAL_URL = "https://api.ipify.org"
private const val PROBE_POLL_MS = 2_000L
private const val PROBE_DEADLINE_MS = 125_000L

interface VerificationHost {
    // False once the screen went to the background or the UI is gone.
    fun active(): Boolean
    fun screen(name: String)
    fun message(text: String)
    fun failed(err: Throwable)
    fun nodePrompt(hint: String)
    fun detachWeb()
    fun stopWindow()
    fun applyProxyOverride(port: Int): Boolean
    fun measureExit(loadFirst: Boolean): String?
    fun loadUrl(url: String)
    fun refresh()
}

class DirectVerifier(
    private val host: VerificationHost,
    private val vault: Vault,
    private val profiles: NodeProfiles,
    private val bundled: BundledChannel,
) {
    var descriptor: VerificationDescriptor? = null
        private set
    var claim: DirectClaim? = null
        private set
    var claimCanResume: Boolean = false
        private set
    var claimMessage: String = ""
        private set
    var incidentId: String = ""
        private set
    var stockTitle: String = ""
        private set
    var verifiedExit: Boolean = false
        private set
    var busy: Boolean = false
        private set

    private val worker = Executors.newSingleThreadExecutor { runnable ->
        Thread(runnable, "waygate-verify").apply { isDaemon = true }
    }
    private var generation: Int = 0
    private var measuredOnce: Boolean = false

    fun start(item: Incident) = worker.execute { verify(item) }

    fun submit() = worker.execute { finish() }

    fun resume() = worker.execute { resumeClaim() }

    // Called after the operator filled in the node credentials on the import screen.
    fun continueWith(profile: NodeProfile) {
        val loaded = descriptor ?: return
        worker.execute {
            try {
                profiles.save(profile)
                begin(loaded, profile)
            } catch (err: Exception) {
                host.failed(err)
            }
        }
    }

    // Restores the durable claim left by an earlier run. Only the original probe's
    // result may be read; no new mutation is ever sent from this path.
    fun restore(claim: DirectClaim) {
        this.claim = claim
        claimCanResume = claim.beforeRequestId > 0
        claimMessage = if (claimCanResume) "已提交一次探测，正在等待结果"
        else "上次未收到探测编号，无法核对；请到控制台核对"
        Runtime.incident = claim.incident
        Runtime.nodeId = claim.node
        Runtime.dispatching = true
    }

    // Cancels the flow and drops this phone's resources; the hub is never told.
    fun cancel() {
        generation++
        Runtime.stopForwarding()
        Runtime.releaseSsh()
    }

    private fun verify(item: Incident) {
        val api = Runtime.api ?: return
        if (busy || Runtime.incident.isNotEmpty() || Runtime.dispatching) {
            if (Runtime.dispatching) { host.message("请先核对上次的探测结果") }
            return
        }
        busy = true
        host.message("正在读取该节点的验证信息")
        incidentId = item.id
        stockTitle = item.name + " · " + item.stock
        verifiedExit = false
        measuredOnce = false
        try {
            val loaded = VerificationDescriptor.from(api.get("/v1/incidents/" + item.id + "/verification"))
            descriptor = loaded
            var profile = profiles.load(loaded.nodeId)
            val hint = loaded.sshHostHint ?: ""
            if (profile == null && bundled.matches(hint)) {
                profile = bundledProfileFor(loaded.nodeId, hint)
                profiles.save(profile)
            }
            if (profile == null) {
                host.nodePrompt(hint)
                return
            }
            begin(loaded, profile)
        } catch (err: Exception) {
            releaseAll()
            host.screen("home")
            host.failed(err)
        } finally {
            busy = false
        }
    }

    // Probes first so the pinned host keys from the packaged config are verified before
    // any bundled credential is used.
    private fun bundledProfileFor(nodeId: String, host: String): NodeProfile {
        val node = bundled.forHost(host) ?: throw IllegalStateException("没有内置该节点的通道配置：" + host)
        val key = Runtime.transport.probeHostKey(host, node.port)
        val negotiated = key.type + " " + key.fingerprint
        if (node.hostKeys.isNotEmpty() && !node.hostKeys.contains(negotiated)) {
            throw IllegalStateException("节点主机指纹与内置配置不一致，已拒绝连接（本次协商到 " + negotiated + "）")
        }
        return NodeProfile(nodeId, host, node.port, node.user, node.authType, bundled.secret(node), "",
            key.base64, key.type, key.fingerprint, node.hostKeys)
    }

    private fun begin(loaded: VerificationDescriptor, profile: NodeProfile) {
        Runtime.incident = incidentId
        Runtime.nodeId = loaded.nodeId
        host.screen("transition")
        host.message("正在连接节点并核对任务")
        val current = ++generation
        try {
            host.detachWeb()
            val session = Runtime.transport.connect(profile)
            Runtime.adopt(session)
            alive(current)
            var target = matchTarget(audit(session.id, loaded.workerCommand), loaded.target)
            Runtime.target = target
            alive(current)
            host.message("正在暂停该任务的后续采集")
            runAction(Runtime.transport, session.id, loaded.workerCommand,
                JSONObject().put("action", "prepare").put("target", target.toJson()).toString(), PrepareValue::from)
            alive(current)
            host.message("正在核对节点出口")
            val exitInfo = runAction(Runtime.transport, session.id, loaded.workerCommand,
                JSONObject().put("action", "exit").put("stock", target.stock).put("job", target.job).toString(),
                ExitValue::from)
            val route = target.route
            if (route == null || exitInfo.route == null || exitInfo.route.signature != route.signature) {
                throw IllegalStateException("节点出口已变化，已停止验证")
            }
            target = target.copy(exitIp = exitInfo.exitIp)
            Runtime.target = target
            alive(current)
            host.message("正在建立应用内 SSH 通道")
            val port = Runtime.proxy.start(session.id, route.host, route.port, loaded.sourceDomains)
            if (!host.applyProxyOverride(port)) { throw IllegalStateException("此系统无法应用 WebView 验证代理") }
            alive(current)
            host.screen("verify")
            host.message("正在核对手机与节点的共同出口")
            val ip = host.measureExit(!measuredOnce) ?: ""
            measuredOnce = true
            alive(current)
            if (ip.isEmpty()) { throw IllegalStateException("未能读取手机出口地址，请重试") }
            if (ip != target.exitIp) { throw IllegalStateException("手机出口与节点出口不一致，已关闭通道") }
            Runtime.proxy.markReady()
            verifiedExit = true
            host.message("已确认共同出口，请在下方手动完成验证")
            host.loadUrl(target.url.ifEmpty { NEUTRAL_URL })
        } catch (err: Exception) {
            releaseAll()
            host.screen("home")
            host.failed(err)
        }
    }

    private fun audit(sessionId: Int, command: String): AuditView = runAction(Runtime.transport, sessionId, command,
        JSONObject().put("action", "audit").toString(), AuditView::from)

    private fun matchTarget(view: AuditView, expected: DirectTarget): DirectTarget {
        if (!DirectTarget.same(view.instanceId, expected.instanceId) ||
            !DirectTarget.same(view.revision, expected.revision)) {
            throw IllegalStateException("节点任务配置已变更，请刷新待办")
        }
        for (row in view.targets) {
            if (!expected.sameIdentity(row)) { continue }
            if (row.route == null || row.url.isEmpty()) {
                throw IllegalStateException("节点未返回该任务的代理出口信息，请稍后重试")
            }
            return expected.adopts(view, row)
        }
        throw IllegalStateException("原阻断已变化或解除，请刷新待办")
    }

    private fun finish() {
        val loaded = descriptor ?: return
        val target = Runtime.target ?: return
        if (Runtime.api == null || Runtime.incident.isEmpty() || !verifiedExit || busy) { return }
        busy = true
        host.message("正在复核共同出口")
        try {
            val ip = host.measureExit(false) ?: ""
            if (ip.isEmpty() || ip != target.exitIp) {
                throw IllegalStateException("手机浏览器出口与节点出口不一致，未提交探测")
            }
            // The durable claim precedes the single remote mutation; a restart cannot
            // resend it.
            var written = DirectClaim(Runtime.incident, loaded.nodeId, target.toJson().toString(),
                loaded.workerCommand, 0, System.currentTimeMillis())
            vault.claim(written.toJson().toString())
            claim = written
            Runtime.dispatching = true
            claimCanResume = false
            claimMessage = "已提交一次探测，正在等待结果"
            host.screen("result")
            host.stopWindow()
            Runtime.stopForwarding()
            host.message("正在单次探测，通过后继续本股")
            val queued = runAction(Runtime.transport, Runtime.sessionId(), loaded.workerCommand,
                JSONObject().put("action", "probe").put("target", target.toJson()).toString(), ProbeValue::from)
            written = written.copy(beforeRequestId = queued.beforeRequestId, at = System.currentTimeMillis())
            vault.claim(written.toJson().toString())
            claim = written
            claimCanResume = true
            val outcome = waitProbeResult(loaded.workerCommand, target, queued.beforeRequestId)
            if (outcome != "real_data" && outcome != "detail_unavailable") {
                throw IllegalStateException("探测未通过，任务保持暂停")
            }
            runAction(Runtime.transport, Runtime.sessionId(), loaded.workerCommand,
                JSONObject().put("action", "resume").put("target", target.toJson()).toString(), ResumeValue::from)
            settleClaim("验证通过，已继续 " + target.stock)
            host.refresh()
        } catch (err: Exception) {
            if (Runtime.dispatching) {
                host.screen("result")
                claimMessage = "探测结果尚未确认：" + (err.message ?: "")
                host.message("没有重发请求；可点“继续核对探测结果”或到控制台核对")
                host.stopWindow()
                Runtime.stopForwarding()
                Runtime.releaseSsh()
            } else {
                releaseAll()
                host.screen("home")
                host.failed(err)
            }
        } finally {
            busy = false
        }
    }

    // Read-only follow-up after a restart or disconnect: only the original probe's
    // result is queried, and resume still goes through the worker's own guards.
    private fun resumeClaim() {
        val pending = claim ?: return
        if (Runtime.api == null || busy) { return }
        if (pending.beforeRequestId <= 0) {
            host.message("上次未收到探测编号，无法核对；请到控制台核对")
            return
        }
        val target = DirectTarget.from(JSONObject(pending.target))
        busy = true
        host.message("正在连接节点核对探测结果")
        try {
            val profile = profiles.load(pending.node)
                ?: throw IllegalStateException("该节点凭据已被删除，无法核对；请到控制台核对")
            val session = Runtime.transport.connect(profile)
            Runtime.adopt(session)
            val outcome = waitProbeResult(pending.workerCommand, target, pending.beforeRequestId)
            if (outcome != "real_data" && outcome != "detail_unavailable") {
                throw IllegalStateException("探测未通过，任务保持暂停")
            }
            runAction(Runtime.transport, session.id, pending.workerCommand,
                JSONObject().put("action", "resume").put("target", target.toJson()).toString(), ResumeValue::from)
            settleClaim("验证通过，已继续 " + target.stock)
            host.refresh()
        } catch (err: Exception) {
            claimMessage = "探测结果尚未确认：" + (err.message ?: "")
            host.message("没有重发请求；请到控制台核对")
            Runtime.releaseSsh()
        } finally {
            busy = false
        }
    }

    private fun waitProbeResult(command: String, target: DirectTarget, beforeId: Long): String {
        val deadline = System.currentTimeMillis() + PROBE_DEADLINE_MS
        while (System.currentTimeMillis() < deadline) {
            val result = runAction(Runtime.transport, Runtime.sessionId(), command,
                JSONObject().put("action", "result").put("target", target.toJson())
                    .put("before_request_id", beforeId).toString(), ResultValue::from)
            val row = result.request
            if (result.done && row != null) { return row.outcome }
            Thread.sleep(PROBE_POLL_MS)
        }
        throw IllegalStateException("探测结果尚未确认；没有再次请求，请在控制台核对")
    }

    private fun settleClaim(message: String) {
        Runtime.dispatching = false
        Runtime.incident = ""
        Runtime.target = null
        claim = null
        claimCanResume = false
        claimMessage = ""
        vault.claim("")
        Runtime.releaseSsh()
        host.message(message)
    }

    private fun alive(current: Int) {
        if (current != generation || !host.active()) { throw IllegalStateException("验证已取消") }
    }

    private fun releaseAll() {
        generation++
        host.stopWindow()
        Runtime.releaseSsh()
        Runtime.incident = ""
        Runtime.target = null
    }
}
