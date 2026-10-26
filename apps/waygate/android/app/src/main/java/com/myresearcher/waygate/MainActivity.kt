// Single-activity shell for the Android port. Screens are toggled like the ArkTS page
// state machine, and this class also implements VerificationHost so the verification
// flow can marshal its WebView work back onto the UI thread. Every host callback may
// arrive from the verification worker thread.
package com.myresearcher.waygate

import android.app.Activity
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.view.WindowManager
import android.webkit.JavascriptInterface
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.RadioButton
import android.widget.TextView
import com.myresearcher.waygate.core.Api
import com.myresearcher.waygate.core.BundledChannel
import com.myresearcher.waygate.core.Credentials
import com.myresearcher.waygate.core.DirectClaim
import com.myresearcher.waygate.core.Incident
import com.myresearcher.waygate.core.Inbox
import com.myresearcher.waygate.core.NodeProfile
import com.myresearcher.waygate.core.NodeProfiles
import com.myresearcher.waygate.core.NodeState
import com.myresearcher.waygate.core.Vault
import com.myresearcher.waygate.core.readableError
import com.myresearcher.waygate.proxy.ProxyOverride
import com.myresearcher.waygate.runtime.Runtime
import com.myresearcher.waygate.verify.DirectVerifier
import com.myresearcher.waygate.verify.VerificationHost
import org.json.JSONObject
import java.util.concurrent.CompletableFuture
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

private const val NEUTRAL_URL = "https://api.ipify.org"
private const val POLL_INTERVAL_MS = 5_000L
private const val UI_WAIT_SECONDS = 12L
private val HUB_PATTERN = Regex("^https://[^/?#@\\s]+(/[^?#\\s]*)?$")

class MainActivity : Activity(), VerificationHost {
    private lateinit var vault: Vault
    private lateinit var profiles: NodeProfiles
    private lateinit var bundled: BundledChannel
    private lateinit var verifier: DirectVerifier

    private lateinit var messageText: TextView
    private lateinit var verifyMessage: TextView
    private lateinit var syncMessage: TextView
    private lateinit var notificationText: TextView
    private lateinit var inboxContainer: LinearLayout
    private lateinit var nodesContainer: LinearLayout
    private lateinit var hubInput: EditText
    private lateinit var tokenInput: EditText
    private lateinit var nodeTitle: TextView
    private lateinit var nodeHint: TextView
    private lateinit var nodePicker: LinearLayout
    private lateinit var nodeHost: EditText
    private lateinit var nodePort: EditText
    private lateinit var nodeUser: EditText
    private lateinit var nodeAuthRow: LinearLayout
    private lateinit var nodeAuthPassword: RadioButton
    private lateinit var nodeSecret: EditText
    private lateinit var nodePassphrase: EditText
    private lateinit var nodeFingerprint: TextView
    private lateinit var channelOutput: TextView
    private lateinit var connectButton: Button
    private lateinit var claimMessage: TextView
    private lateinit var diagnosticsText: TextView
    private lateinit var webView: WebView

    private val screens = LinkedHashMap<String, LinearLayout>()
    private val worker = Executors.newSingleThreadExecutor { runnable ->
        Thread(runnable, "waygate-ui").apply { isDaemon = true }
    }
    private val poller = Handler(Looper.getMainLooper())
    private val polling = Runnable { if (foreground && !busy) { refresh() } }

    private var api: Api? = null
    private var credentials = Credentials("", "", "", false, "")
    private var itemList: List<Incident> = emptyList()
    private var busy = false
    private var foreground = false
    private var channelMode = false
    private var proxyApplied = false
    private var exitWaiter: CompletableFuture<String>? = null
    private var pendingProfile: NodeProfile? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        bindViews()
        vault = Vault(this)
        profiles = NodeProfiles(this)
        bundled = BundledChannel(this)
        verifier = DirectVerifier(this, vault, profiles, bundled)
        setupWebView()
        setupActions()
        initialize()
    }

    override fun onStart() {
        super.onStart()
        foreground = true
        poller.postDelayed(polling, POLL_INTERVAL_MS)
    }

    override fun onStop() {
        foreground = false
        poller.removeCallbacks(polling)
        // Releases only what this phone created; a submitted probe stays readable.
        if (screens["verify"]?.visibility == View.VISIBLE || proxyApplied) {
            worker.execute { stopWindow() }
            show(if (Runtime.dispatching) "result" else "home")
        }
        Runtime.background()
        super.onStop()
    }

    // ---- VerificationHost -----------------------------------------------------

    override fun active(): Boolean = foreground

    override fun screen(name: String) = runOnUiThread { show(name) }

    override fun message(text: String) = runOnUiThread {
        messageText.text = readableError(text)
        verifyMessage.text = readableError(text)
    }

    override fun failed(err: Throwable) = runOnUiThread { error(err) }

    override fun nodePrompt(hint: String) = runOnUiThread {
        channelMode = false
        pendingProfile = null
        nodeTitle.text = verifier.stockTitle
        nodeHint.text = if (hint.isNotEmpty()) "该节点 SSH 主机提示：" + hint else "主控未提供该节点主机提示，请填写实际 SSH 地址"
        nodeHost.setText(hint)
        nodeFingerprint.text = ""
        channelOutput.text = ""
        fillNodePicker(verifyMode = true)
        connectButton.text = "保存并继续"
        message("首次处理该节点，请填写该节点的 SSH 登录凭据（只保存在本机）")
        show("node")
    }

    override fun detachWeb() = transferToUi { webView.stopLoading() }

    override fun stopWindow() {
        Runtime.proxy.stop()
        transferToUi {
            webView.stopLoading()
            webView.loadUrl("about:blank")
            ProxyOverride.clear(this) { }
            proxyApplied = false
            screens["verify"]?.visibility = View.GONE
        }
        exitWaiter?.complete("")
        exitWaiter = null
    }

    override fun applyProxyOverride(port: Int): Boolean {
        val latch = CountDownLatch(1)
        val applied = AtomicBoolean(false)
        runOnUiThread {
            ProxyOverride.apply(this, port) { ok ->
                applied.set(ok)
                proxyApplied = ok
                latch.countDown()
            }
        }
        return latch.await(UI_WAIT_SECONDS, TimeUnit.SECONDS) && applied.get()
    }

    override fun measureExit(loadFirst: Boolean): String? {
        val future = CompletableFuture<String>()
        exitWaiter = future
        runOnUiThread { webView.loadUrl(NEUTRAL_URL) }
        return try {
            future.get(25, TimeUnit.SECONDS)
        } catch (err: Exception) {
            exitWaiter = null
            null
        }
    }

    override fun loadUrl(url: String) = runOnUiThread { webView.loadUrl(url) }

    override fun refresh() = runOnUiThread { refresh() }

    // ---- screens --------------------------------------------------------------

    private fun bindViews() {
        messageText = findViewById(R.id.message_text)
        verifyMessage = findViewById(R.id.verify_message)
        syncMessage = findViewById(R.id.sync_message)
        notificationText = findViewById(R.id.notification_text)
        inboxContainer = findViewById(R.id.inbox_container)
        nodesContainer = findViewById(R.id.nodes_container)
        hubInput = findViewById(R.id.hub_input)
        tokenInput = findViewById(R.id.token_input)
        nodeTitle = findViewById(R.id.node_title)
        nodeHint = findViewById(R.id.node_hint)
        nodePicker = findViewById(R.id.node_picker)
        nodeHost = findViewById(R.id.node_host)
        nodePort = findViewById(R.id.node_port)
        nodeUser = findViewById(R.id.node_user)
        nodeAuthRow = findViewById(R.id.node_auth_row)
        nodeAuthPassword = findViewById(R.id.node_auth_password)
        nodeSecret = findViewById(R.id.node_secret)
        nodePassphrase = findViewById(R.id.node_passphrase)
        nodeFingerprint = findViewById(R.id.node_fingerprint)
        channelOutput = findViewById(R.id.channel_output)
        connectButton = findViewById(R.id.connect_button)
        claimMessage = findViewById(R.id.claim_message)
        diagnosticsText = findViewById(R.id.diagnostics_text)
        webView = findViewById(R.id.web_view)
        for (name in listOf("screen_loading", "screen_settings", "screen_home", "screen_node",
            "screen_transition", "screen_result", "screen_diagnostics", "screen_verify")) {
            val id = resources.getIdentifier(name, "id", packageName)
            screens[name.removePrefix("screen_")] = findViewById(id)
        }
    }

    private fun show(name: String) {
        for ((key, view) in screens) {
            view.visibility = if (key == name) View.VISIBLE else View.GONE
        }
        // The header stays reachable except while a browser is on screen.
        findViewById<Button>(R.id.settings_button).visibility =
            if (name == "verify") View.GONE else View.VISIBLE
        // A captcha keeps the operator watching without touching for minutes, and the OEM
        // smart-stay heuristic is not a guarantee, so the verification window holds its own
        // screen-on. Every other screen clears it, matching the ArkTS port.
        if (name == "verify") {
            window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        } else {
            window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        }
        if (name == "result") {
            claimMessage.text = verifier.claimMessage
            findViewById<Button>(R.id.resume_button).isEnabled = verifier.claimCanResume
        }
    }

    private fun setupWebView() {
        webView.settings.javaScriptEnabled = true
        webView.settings.domStorageEnabled = true
        webView.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView?, url: String?) {
                val target = url ?: return
                if (!target.startsWith(NEUTRAL_URL)) { return }
                val waiter = exitWaiter ?: return
                exitWaiter = null
                view?.evaluateJavascript("JSON.stringify(document.body.innerText.trim())") { raw ->
                    waiter.complete(unwrap(raw))
                }
            }
        }
    }

    // ArkWeb could hand back the JS string either raw or JSON-encoded once more; the
    // same tolerance is kept here.
    private fun unwrap(raw: String?): String {
        var value = raw ?: return ""
        if (value.startsWith("\"") && value.endsWith("\"") && value.length >= 2) {
            value = value.substring(1, value.length - 1)
        }
        return value.trim()
    }

    private fun setupActions() {
        findViewById<Button>(R.id.settings_button).setOnClickListener {
            hubInput.setText(credentials.hub)
            tokenInput.setText("")
            show("settings")
        }
        findViewById<Button>(R.id.save_button).setOnClickListener { save() }
        findViewById<Button>(R.id.channel_button).setOnClickListener { openChannel() }
        findViewById<Button>(R.id.diagnostics_button).setOnClickListener { openDiagnostics() }
        findViewById<Button>(R.id.diagnostics_back).setOnClickListener { show("home") }
        findViewById<Button>(R.id.probe_button).setOnClickListener { readFingerprint() }
        connectButton.setOnClickListener { if (channelMode) channelConnect() else saveNode() }
        findViewById<Button>(R.id.node_back).setOnClickListener { show("home") }
        findViewById<Button>(R.id.finish_button).setOnClickListener { verifier.submit() }
        findViewById<Button>(R.id.verify_cancel).setOnClickListener { abandon() }
        findViewById<Button>(R.id.resume_button).setOnClickListener { verifier.resume() }
        findViewById<Button>(R.id.result_back).setOnClickListener { show("home"); refresh() }
        nodeHost.setOnFocusChangeListener { _, _ -> nodeFingerprint.text = "" }
        nodePort.setText("22")
        nodeUser.setText("root")
    }

    private fun initialize() {
        worker.execute {
            try {
                val loaded = vault.load()
                credentials = loaded
                runOnUiThread { hubInput.setText(loaded.hub) }
                if (loaded.hub.isNotEmpty() && loaded.token.isNotEmpty()) {
                    val client = Api(loaded)
                    api = client
                    Runtime.api = client
                    runOnUiThread { show("home") }
                    val pending = loaded.pendingClaim
                    if (pending.isNotEmpty()) {
                        val claim = DirectClaim.from(JSONObject(pending))
                        verifier.restore(claim)
                    }
                    refresh()
                } else {
                    runOnUiThread { show("settings") }
                }
            } catch (err: Exception) {
                runOnUiThread { error(err); show(if (api == null) "settings" else "home") }
            }
        }
    }

    private fun save() {
        val address = hubInput.text.toString().trim().trimEnd('/')
        if (!HUB_PATTERN.matches(address)) { message("请填写主控 HTTPS 地址，例如 https://你的域名/waygate"); return }
        val token = tokenInput.text.toString().trim()
        if (token.length < 16 || token.length > 1023) { message("请填写主控的 console.token"); return }
        setBusy(true)
        message("正在连接主控并校验令牌…")
        worker.execute {
            try {
                val next = Credentials(address, token, credentials.deviceId, false, "")
                val client = Api(next)
                client.get("/v1/inbox")
                if (api != null && credentials.push) {
                    runCatching { api?.request("/v1/devices/" + credentials.deviceId, "DELETE", "") }
                }
                vault.save(next)
                vault.claim("")
                credentials = next
                api = client
                Runtime.api = client
                runOnUiThread { show("home"); message(""); syncMessage.text = "" }
                refresh()
            } catch (err: Exception) {
                runOnUiThread { error(err) }
            } finally {
                runOnUiThread { setBusy(false) }
            }
        }
    }

    private fun refresh() {
        val client = api ?: return
        worker.execute {
            try {
                val feed = Inbox.from(client.get("/v1/inbox"))
                runOnUiThread {
                    itemList = feed.items
                    renderInbox(feed.items)
                    renderNodes(feed.nodes)
                    syncMessage.text = if (feed.error.isNotEmpty()) readableError(feed.error) else ""
                    notificationText.text = when {
                        !feed.pushEnabled -> "主控推送尚未配置，待办仍会保存"
                        feed.pushErrors > 0 -> "有通知发送失败，请检查主控推送配置"
                        else -> "通知尚未开启"
                    }
                }
            } catch (err: Exception) {
                runOnUiThread { syncMessage.text = readableError(err.message ?: "主控刷新失败") }
            }
        }
    }

    private fun renderInbox(items: List<Incident>) {
        inboxContainer.removeAllViews()
        for (item in items) {
            val row = layoutInflater.inflate(R.layout.item_incident, inboxContainer, false)
            row.findViewById<TextView>(R.id.item_title).text = item.title.ifEmpty { item.name + " · " + item.stock }
            row.findViewById<TextView>(R.id.item_subtitle).text = buildString {
                append(item.node).append(" · ").append(item.stock)
                if (!item.online) { append(" · 节点离线") }
                if (!item.canVerify) { append(" · 暂不可验证") }
            }
            val action = row.findViewById<Button>(R.id.item_action)
            action.isEnabled = item.canVerify
            action.setOnClickListener { verifier.start(item) }
            inboxContainer.addView(row)
        }
    }

    private fun renderNodes(nodes: List<NodeState>) {
        nodesContainer.removeAllViews()
        for (node in nodes) {
            val text = TextView(this)
            text.text = buildString {
                append(node.name.ifEmpty { node.id }).append(if (node.online) " · 在线" else " · 离线")
                append(" · ").append(node.state)
                for (stock in node.stocks) { append("\n  ").append(stock.stock).append(' ').append(stock.state) }
            }
            text.setTextColor(getColor(R.color.waygate_muted))
            text.textSize = 12f
            nodesContainer.addView(text)
        }
    }

    // ---- node screen ----------------------------------------------------------

    private fun openChannel() {
        channelMode = true
        pendingProfile = null
        nodeTitle.text = getString(R.string.title_node)
        fillNodePicker(verifyMode = false)
        val first = bundled.all().firstOrNull()
        if (first != null) {
            selectNode(first)
        } else {
            nodeHint.text = if (bundled.failure.isNotEmpty()) bundled.failure
            else "直接建立到该节点的 SSH 通道，不经过主控，也不使用待办信息。"
        }
        connectButton.text = getString(R.string.action_connect)
        show("node")
    }

    private fun fillNodePicker(verifyMode: Boolean) {
        nodePicker.removeAllViews()
        if (bundled.all().isEmpty()) { return }
        nodeHint.text = "内置通道：配置随本构建打包，无需在手机上填写"
        for (node in bundled.all()) {
            val button = Button(this)
            button.text = node.id
            button.setOnClickListener { selectNode(node) }
            nodePicker.addView(button)
        }
        if (verifyMode) { nodeHint.text = nodeHint.text }
    }

    private fun selectNode(node: com.myresearcher.waygate.core.BuiltinNode) {
        nodeHost.setText(node.host)
        nodePort.setText(node.port.toString())
        nodeUser.setText(node.user)
        nodeFingerprint.text = ""
        channelOutput.text = ""
        val bundledSecret = bundled.secret(node)
        val built = bundledSecret.isNotEmpty()
        nodeAuthRow.visibility = if (built) View.GONE else View.VISIBLE
        nodeSecret.visibility = if (built) View.GONE else View.VISIBLE
        nodePassphrase.visibility = if (built) View.GONE else View.VISIBLE
        nodeHint.text = "内置通道：" + node.label + "（配置随本构建打包，无需在手机上填写）"
        if (built) {
            channelOutput.text = "使用本构建内置的凭据，无需在手机上输入；主机指纹已按内置配置固定校验。"
        }
    }

    private fun currentProfile(host: String, port: Int, user: String, fingerprint: String): NodeProfile {
        val node = bundled.forHost(host)
        val secret = node?.let { bundled.secret(it) } ?: nodeSecret.text.toString().trim()
        if (secret.isEmpty()) { throw IllegalStateException("请填写该节点的私钥或密码") }
        val auth = if (node != null) node.authType else if (nodeAuthPassword.isChecked) "password" else "key"
        val pinned = if (node != null && node.hostKeys.isNotEmpty()) node.hostKeys else listOf(fingerprint)
        return NodeProfile(node?.id ?: ("node-" + host), host, port, user, auth, secret,
            nodePassphrase.text.toString().trim(), "", "", "", pinned)
    }

    private fun readFingerprint() {
        val host = nodeHost.text.toString().trim()
        val port = nodePort.text.toString().trim().toIntOrNull() ?: 22
        if (host.isEmpty()) { message("请填写节点 SSH 地址"); return }
        setBusy(true)
        message("正在读取节点主机指纹")
        worker.execute {
            try {
                val key = Runtime.transport.probeHostKey(host, port)
                val shown = key.type + " " + key.fingerprint
                runOnUiThread {
                    nodeFingerprint.text = "主机指纹：" + shown
                    val node = bundled.forHost(host)
                    if (node != null && node.hostKeys.isNotEmpty() && !node.hostKeys.contains(shown)) {
                        message("节点主机指纹与内置配置不一致，已拒绝连接（本次协商到 " + shown + "）")
                    } else {
                        message("请核对指纹后再建立通道")
                    }
                }
            } catch (err: Exception) {
                runOnUiThread { error(err) }
            } finally {
                runOnUiThread { setBusy(false) }
            }
        }
    }

    private fun channelConnect() {
        val host = nodeHost.text.toString().trim()
        val port = nodePort.text.toString().trim().toIntOrNull() ?: 22
        val user = nodeUser.text.toString().trim().ifEmpty { "root" }
        if (host.isEmpty()) { message("请填写节点 SSH 地址"); return }
        setBusy(true)
        message("正在建立 SSH 通道")
        channelOutput.text = ""
        worker.execute {
            try {
                val key = Runtime.transport.probeHostKey(host, port)
                val shown = key.type + " " + key.fingerprint
                runOnUiThread { nodeFingerprint.text = "主机指纹（已与内置配置比对）\n" + shown }
                val node = bundled.forHost(host)
                if (node != null && node.hostKeys.isNotEmpty() && !node.hostKeys.contains(shown)) {
                    throw IllegalStateException("节点主机指纹与内置配置不一致，已拒绝连接（本次协商到 " + shown + "）")
                }
                val profile = currentProfile(host, port, user, shown).copy(
                    knownHostBase64 = key.base64, knownHostType = key.type, fingerprint = key.fingerprint)
                val session = Runtime.transport.connect(profile)
                try {
                    val result = Runtime.transport.exec(session.id, CHANNEL_COMMAND, "")
                    val text = buildString {
                        append("通道已建立，主机指纹已核对\n退出码：").append(result.exitStatus)
                        append("\n--- stdout ---\n").append(result.stdout.trim())
                        if (result.stderr.trim().isNotEmpty()) { append("\n--- stderr ---\n").append(result.stderr.trim()) }
                    }
                    runOnUiThread { channelOutput.text = text; message("SSH 通道可用") }
                } finally {
                    Runtime.transport.close(session.id)
                }
            } catch (err: Exception) {
                runOnUiThread { error(err) }
            } finally {
                runOnUiThread { setBusy(false) }
            }
        }
    }

    private fun saveNode() {
        val host = nodeHost.text.toString().trim()
        val port = nodePort.text.toString().trim().toIntOrNull() ?: 22
        val user = nodeUser.text.toString().trim().ifEmpty { "root" }
        val current = verifier.descriptor ?: return
        if (host.isEmpty()) { message("请填写节点 SSH 地址"); return }
        setBusy(true)
        worker.execute {
            try {
                val key = Runtime.transport.probeHostKey(host, port)
                val shown = key.type + " " + key.fingerprint
                runOnUiThread { nodeFingerprint.text = "主机指纹：" + shown }
                val node = bundled.forHost(host)
                if (node != null && node.hostKeys.isNotEmpty() && !node.hostKeys.contains(shown)) {
                    throw IllegalStateException("节点主机指纹与内置配置不一致，已拒绝连接（本次协商到 " + shown + "）")
                }
                val profile = currentProfile(host, port, user, shown).copy(
                    nodeId = current.nodeId, knownHostBase64 = key.base64, knownHostType = key.type,
                    fingerprint = key.fingerprint)
                profiles.save(profile)
                verifier.continueWith(profile)
            } catch (err: Exception) {
                runOnUiThread { error(err) }
            } finally {
                runOnUiThread { setBusy(false) }
            }
        }
    }

    // ---- diagnostics and housekeeping ----------------------------------------

    private fun openDiagnostics() {
        val client = api
        diagnosticsText.text = buildString {
            append("当前状态：").append(if (syncMessage.text.isEmpty()) "尚未同步" else syncMessage.text)
            append("\n主控：").append(if (client != null) client.credentials.hub else hubInput.text.toString())
            append("\n\n最近一次同步故障：\n").append(syncMessage.text.ifEmpty { "无" })
            append("\n\n最近一次接口失败：\n").append(client?.lastFailureDetails?.ifEmpty { "无" } ?: "无")
        }
        show("diagnostics")
    }

    private fun abandon() {
        verifier.cancel()
        worker.execute { stopWindow() }
        show("home")
        refresh()
    }

    private fun error(err: Throwable) {
        messageText.text = readableError(err.message ?: "操作未完成，请重试连接")
    }

    private fun setBusy(value: Boolean) {
        busy = value
    }

    // Runs a UI action on the main thread and waits for it, mirroring the awaited
    // ArkWeb teardown calls.
    private fun transferToUi(action: () -> Unit) {
        val latch = CountDownLatch(1)
        runOnUiThread { try { action() } finally { latch.countDown() } }
        latch.await(UI_WAIT_SECONDS, TimeUnit.SECONDS)
    }

    private companion object {
        const val CHANNEL_COMMAND = "id; hostname; echo SSH_CONNECTION=\$SSH_CONNECTION"
    }
}
