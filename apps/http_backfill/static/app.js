"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const display = (id, value) => { $(id).textContent = value == null || value === "" ? "—" : String(value); };
  const states = { idle: "待配置", running: "采集中", paused: "已暂停", blocked: "已拦截", error: "需要检查", completed: "任务结束" };
  const proxyModes = { direct: "直连", http: "HTTP 代理", mayi: "动态 IP", qingguo: "青果动态 IP" };
  const dynamicProxyMode = (mode) => ["mayi", "qingguo"].includes(mode);
  const outcomes = { real_data: "数据已核实", reserved: "请求中 / 尚未确认", redirect: "重定向，等待下一次请求", detail_unavailable: "详情不可用", list_ok: "列表已核实", detail_ok: "正文已核实", success: "已核实", ok: "已核实", removed: "源已移除", deleted: "源已删除", access_block: "访问拦截", blocked: "访问拦截", challenge: "验证码响应", rate_limited: "限流响应", parse_error: "解析异常", schema_error: "结构异常", transport_error: "传输异常", network_timeout: "请求超时", tls_error: "TLS 连接失败", network_connect: "网络连接失败", network_io: "网络传输失败", error: "异常", pending: "待处理", unknown: "结果未确认" };
  let authenticated = false;
  let status = null;
  let online = false;
  let busy = false;
  let polling = false;
  let timer = null;
  let tickTimer = null;
  let loadedConfig = false;
  let formDirty = false;
  let lastPoll = null;
  let requestItems = [];
  let latestRequestItems = [];
  const activityPages = Object.fromEntries(["requests", "events"].map((kind) => [kind, { limit: kind === "requests" ? 30 : 20, latest: true, index: 0, cursors: [null], snapshot: null, page: null, loading: false, generation: 0 }]));
  let statusGeneration = 0;
  let pendingDeletion = null;
  let formJobId = null;
  let formConfigSignature = "";
  let selectedNode = "local";
  let nodeEpoch = 0;
  let pollSerial = 0;
  let fleetTimer = null;
  let fleetLoading = false;
  let fleetData = null;
  let editingNode = null;
  let removingNode = null;
  let pendingSelection = null;
  let retryNodeEpoch = null;
  let proxyState = null;
  let proxyDirty = false;
  let proxySupported = null;
  let proxyLoading = false;
  let proxyReadSerial = 0;
  const downloadBusy = new Set();
  const taskProxyDrafts = new Map();
  const stockEvidenceOpen = new Set();
  let mihomoDownloading = false;

  function first(object, keys, fallback = null) {
    for (const key of keys) if (object && object[key] !== undefined && object[key] !== null) return object[key];
    return fallback;
  }
  function objectValue(value) { return value && typeof value === "object" && !Array.isArray(value) ? value : {}; }
  function textValue(value) {
    if (value == null) return "";
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
  }
  function epoch(value) {
    if (value == null || value === "") return null;
    if (typeof value === "object") return epoch(first(value, ["at", "timestamp", "time", "completed_at", "finished_at", "started_at"]));
    if (typeof value === "number") return value > 1e12 ? value : value * 1000;
    if (/^\d+(\.\d+)?$/.test(value)) return epoch(Number(value));
    // All source wall times without an explicit timezone use the source's Shanghai contract.
    const explicit = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value);
    const normalized = /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}/.test(value) && !explicit ? value.replace(" ", "T") + "+08:00" : value;
    const time = Date.parse(normalized);
    return Number.isFinite(time) ? time : null;
  }
  function time(value, includeDate = true) {
    const ms = epoch(value);
    if (ms == null) return "—";
    const options = { timeZone: "Asia/Shanghai", hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" };
    if (includeDate) Object.assign(options, { year: "numeric", month: "2-digit", day: "2-digit" });
    return new Intl.DateTimeFormat("zh-CN", options).format(ms).replaceAll("/", "-");
  }
  function dateOnly(value) {
    const ms = epoch(value);
    if (ms == null) return value ? textValue(value) : "—";
    return new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).format(ms).replaceAll("/", "-");
  }
  function duration(seconds) {
    if (!Number.isFinite(Number(seconds))) return "—";
    seconds = Math.max(0, Math.floor(Number(seconds)));
    if (seconds < 60) return `${seconds} 秒`;
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor(seconds % 86400 / 3600);
    const minutes = Math.floor(seconds % 3600 / 60);
    const remainder = seconds % 60;
    if (days) return `${days} 天 ${hours} 小时`;
    if (hours) return `${hours} 小时 ${minutes} 分`;
    return `${minutes} 分 ${remainder} 秒`;
  }
  function number(value) {
    if (value == null || value === "" || !Number.isFinite(Number(value))) return "—";
    return new Intl.NumberFormat("zh-CN").format(Number(value));
  }
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = textValue(text);
    return node;
  }
  function notice(message, error = false) {
    $("notice").hidden = !message;
    $("notice").classList.toggle("error", error);
    $("notice").textContent = message || "";
  }
  function authMessage(message, error = false) {
    display("auth-message", message);
    $("auth-message").classList.toggle("error", error);
  }
  function showAuth(message) {
    authenticated = false;
    online = false;
    stopTimers();
    $("auth-panel").hidden = false;
    $("console").hidden = true;
    $("logout").hidden = true;
    $("node-token").value = "";
    proxyReadSerial += 1; proxyLoading = false;
    clearProxySecrets();
    clearTaskProxyDrafts();
    authMessage(message || "输入密钥后进入控制台。");
  }
  function showConsole() {
    authenticated = true;
    $("auth-panel").hidden = true;
    $("console").hidden = false;
    $("logout").hidden = false;
    authMessage("");
    startTimers();
  }
  async function api(path, method = "GET", payload, node = selectedNode) {
    const options = { method, credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" } };
    if (payload !== undefined) { options.headers["Content-Type"] = "application/json"; options.body = JSON.stringify(payload); }
    const routed = node !== "local" && /^(status|jobs|control|requests|events|posts|proxy|stocks|mihomo)(?:[/?]|$)/.test(path) ? `nodes/${encodeURIComponent(node)}/${path}` : path;
    const response = await fetch(new URL(`api/${routed}`, document.baseURI), options);
    const contentType = response.headers.get("content-type") || "";
    const data = contentType.includes("application/json") ? await response.json() : null;
    if (!response.ok) {
      if (response.status === 401 && authenticated) showAuth("中央控制台会话已过期，请重新输入访问密钥。各实例的采集状态不受页面退出影响。");
      const ambiguous = data?.ambiguous === true;
      const message = data && data.error ? textValue(data.error) : `本地接口返回 HTTP ${response.status}`;
      const err = new Error(message + (ambiguous ? "。该实例可能已执行操作，结果尚未确认；请先刷新状态再决定，系统不会自动重发。" : ""));
      err.status = response.status;
      err.ambiguous = ambiguous;
      throw err;
    }
    if (data === null) throw new Error("本地接口返回了非 JSON 响应，请检查服务或反向代理配置。");
    return data;
  }
  function nodeName(id = selectedNode) {
    const node = fleetData?.nodes?.find((entry) => String(first(entry, ["id", "alias"])) === id);
    return node?.name || (id === "local" ? "本机采集实例" : id);
  }
  function downloadURL(scope, format, node = selectedNode) {
    const path = scope === "local" && node !== "local" ? `api/nodes/${encodeURIComponent(node)}/download/posts` : "api/download/posts";
    return new URL(`${path}?scope=${scope}&format=${format}`, document.baseURI);
  }
  function downloadKey(scope, format, node = selectedNode) {
    return `${scope}:${scope === "fleet" ? "local" : node}:${format}`;
  }
  function downloadNodeId(node) { return String(node).replace(/[^A-Za-z0-9._-]/g, "_"); }
  function updateDownloadLinks() {
    for (const scope of ["local", "fleet"]) for (const format of ["csv", "jsonl"]) {
      const link = $(`download-${scope}-${format}`);
      link.setAttribute("href", downloadURL(scope, format).href);
      link.setAttribute("download", "");
      link.setAttribute("aria-disabled", String(!authenticated || downloadBusy.has(downloadKey(scope, format))));
    }
    const description = `当前导出节点：${nodeName()}（${selectedNode}）。下载该节点全部已采集帖子，涵盖已留存的所有任务；与主控的“导出合并帖子”分开。导出不启动采集或同步。`;
    display("download-node-description", `导出：${nodeName()}`);
    $("download-node-description").title = description;
    for (const format of ["csv", "jsonl"]) {
      const label = `导出 ${nodeName()}（${selectedNode}）全部已采集帖子 ${format.toUpperCase()}`;
      $(`download-local-${format}`).title = description;
      $(`download-local-${format}`).setAttribute("aria-label", label);
    }
  }
  async function refreshFleet() {
    if (!authenticated || fleetLoading) return;
    fleetLoading = true;
    try {
      const data = await api("fleet");
      if (!authenticated) return;
      if (!data || !Array.isArray(data.nodes)) throw new Error("多实例概览格式无效");
      fleetData = data; renderFleet(data);
      $("fleet-error").hidden = true;
    } catch (error) {
      if (authenticated) { $("fleet-error").hidden = false; display("fleet-error", `中央概览暂时无法更新：${error.message}。已取得的其他实例状态保留。`); }
    } finally { fleetLoading = false; controls(); }
  }
  function renderFleet(data) {
    const nodes = [...data.nodes];
    if (!nodes.some((entry) => entry.local || entry.id === "local")) nodes.unshift({ id: "local", name: "本机采集实例", local: true, connection: "unknown" });
    const selector = $("selected-node"); selector.replaceChildren();
    const list = $("fleet-nodes"); list.replaceChildren();
    for (const node of nodes) {
      const id = String(first(node, ["id", "alias"], "local"));
      const option = el("option", "", `${node.name || id}${node.local || id === "local" ? " · 本机" : " · " + id}`); option.value = id; selector.append(option);
      const card = el("article", `node-card${id === selectedNode ? " selected" : ""}`);
      const head = el("div", "node-head");
      head.append(el("strong", "", node.name || id), el("span", `tag${node.connection === "offline" ? " warning" : node.connection === "online" ? " success" : ""}`, { online: "连接正常", offline: "连接异常", unknown: "尚未连接" }[node.connection] || "等待状态")); card.append(head);
      card.append(el("p", "node-address", node.local || id === "local" ? "中央控制台所在本机" : node.base_url || "地址未返回"));
      const state = objectValue(node.status), counts = objectValue(first(state, ["aggregate", "counters", "stats"], {}));
      const nodeStateLabel = state.state === "running" && state.network_retry ? "等待网络重试" : states[state.state] || "状态尚未取得";
      card.append(el("p", "node-status", `${nodeStateLabel}${state.reason ? " · " + textValue(state.reason) : ""}`));
      const summary = el("div", "post-summary"); summary.append(el("strong", "", `已采集帖子 ${number(counts.unique_posts)}`));
      const subsets = el("div", "post-subset-counts"); subsets.append(el("span", "subset-prefix", "其中"));
      for (const [label, value] of [["已补详情", counts.body_complete], ["待补详情", counts.pending], ["未触发补详情", counts.list_only]]) subsets.append(el("span", "", `${label} ${number(value)}`));
      summary.append(subsets); card.append(summary, el("p", "node-sync", `${counts.source_attempts == null ? "请求台账（旧版）" : "实际来源请求"} ${number(first(counts, ["source_attempts", "attempts"]))} · 标题与详情属于同一条帖子`));
      const audit = objectValue(state.rate_audit);
      card.append(el("p", "node-sync", audit.confirmed_requests == null ? "源请求间隔审计：未可核验，需该实例实际台账。" : `近期网络尝试 ${number(audit.confirmed_requests)} · 已知间隔违规 ${number(audit.violations?.count)} · 仅此审计样本，帖子数不代表请求数`));
      const config = state.job?.config || state.config;
      if (config) card.append(el("p", "node-task", `${Array.isArray(config.stocks) ? config.stocks.join("，") : "—"} · ${config.from_date || "—"} 至 ${config.to_date || "—"}`));
      if (state.active_halt || state.storage_halt) card.append(el("p", "node-error", state.storage_halt ? "本地写入暂停，需修复采集数据库写入。" : "保留来源阻断，需在该实例明确安排一次探测。"));
      if (node.last_error) card.append(el("p", "node-error", `主控到节点的连接错误：${textValue(node.last_error)}。请检查主控到采集机的网络、端口和安全组；这不等于来源采集失败。`));
      const sync = objectValue(node.sync), labels = { idle: "等待同步", syncing: "正在同步", error: "同步异常", ready: "已同步" };
      card.append(el("p", "node-sync", `${labels[sync.state] || "同步状态尚未取得"} · 游标 ${number(first(sync, ["cursor", "last_cursor", "last_seq"], data.merge?.cursors?.[node.instance_id]))} · 最近状态 ${time(node.last_seen_at)}${sync.last_synced_at ? " · 最近同步 " + time(sync.last_synced_at) : ""}`));
      if (sync.error) card.append(el("p", "node-error", `同步错误：${textValue(sync.error)}`));
      const actions = el("div", "node-actions");
      const select = el("button", "button secondary small", id === selectedNode ? "正在查看" : "查看与控制"); select.type = "button"; select.dataset.fleetAction = "select"; select.dataset.current = id === selectedNode ? "1" : "0";
      select.addEventListener("click", () => requestSelection(id)); actions.append(select);
      if (!node.local && id !== "local") {
        const edit = el("button", "button quiet small", "编辑登记"); edit.type = "button"; edit.dataset.fleetAction = "edit"; edit.addEventListener("click", () => openNodeForm(node));
        const remove = el("button", "button danger-quiet small", "移除登记"); remove.type = "button"; remove.dataset.fleetAction = "remove"; remove.addEventListener("click", () => confirmNodeRemoval(node)); actions.append(edit, remove);
      }
      card.append(actions); list.append(card);
    }
    if (!nodes.some((entry) => String(first(entry, ["id", "alias"])) === selectedNode)) { const option = el("option", "", `${selectedNode} · 已移除登记`); option.value = selectedNode; selector.append(option); }
    selector.value = selectedNode;
    display("selected-node-note", `下方任务、配置、控制、记录与当前节点导出属于 ${nodeName()}。切换只改变查看对象，不会开始或暂停采集。`);
    const merge = objectValue(data.merge), stats = objectValue(first(merge, ["aggregate", "counts", "summary"], merge));
    display("merge-sync-note", data.auto_sync === false ? "自动同步已关闭，可手动立即同步已采记录。同步只传输数据和证据，不请求股吧。" : "服务端每 60 秒同步已采记录。同步只传输数据和证据，不请求股吧。");
    display("merge-posts", number(first(stats, ["posts", "unique_posts", "post_count"])));
    display("merge-bodies", number(first(stats, ["body_complete", "bodies", "detail_bodies", "body_count"])));
    display("merge-conflicts", number(first(stats, ["conflicts", "conflict_count"])));
    display("merge-status", `${{ idle: "等待同步", syncing: "正在同步已采记录", ready: "已同步", error: "合并异常" }[merge.state || merge.status] || (data.auto_sync === false ? "自动同步已关闭" : "增量同步由服务端后台运行")} · 来源观察 ${number(merge.observations)} · 实例 ${number(merge.instances)}${merge.pending_instances?.length ? " · 待完成传输 " + number(merge.pending_instances.length) + " 个实例" : ""}${merge.last_synced_at ? " · 最近同步 " + time(merge.last_synced_at) : ""}`);
    display("merge-path", first(merge, ["db_path", "collector_db_path", "path"], "合并库路径尚未返回"));
    const errors = [];
    const error = first(merge, ["last_error", "error"]); if (error) errors.push(textValue(error));
    for (const [instance, detail] of Object.entries(objectValue(merge.recovery_errors))) errors.push(`本地合并恢复异常 · ${instance} · 游标 ${number(detail?.cursor)}：${textValue(detail?.error || detail)}。已保留原游标，其他实例可继续同步。`);
    $("merge-error").hidden = !errors.length; display("merge-error", errors.join("\n"));
    controls();
  }
  function requestSelection(id) {
    $("selected-node").value = selectedNode;
    if (busy || id === selectedNode) return;
    if (formDirty || proxyDirty || [...taskProxyDrafts.values()].some((draft) => draft.dirty)) { pendingSelection = id; $("switch-node-dialog").showModal(); return; }
    void switchNode(id);
  }
  async function switchNode(id, force = false) {
    if (busy || (!force && id === selectedNode)) return false;
    selectedNode = id; nodeEpoch += 1; statusGeneration += 1; pollSerial += 1; polling = false;
    updateDownloadLinks();
    status = null; online = false; lastPoll = null; requestItems = []; latestRequestItems = [];
    pendingDeletion = null; retryNodeEpoch = null;
    for (const dialog of ["retry-dialog", "delete-dialog"]) if ($(dialog).open) { $(dialog).returnValue = "cancel"; $(dialog).close?.(); }
    clearForm();
    clearProxyForm();
    clearTaskProxyDrafts();
    for (const kind of ["requests", "events"]) {
      const previous = activityPages[kind]; activityPages[kind] = { ...previous, latest: true, index: 0, cursors: [null], snapshot: null, page: null, loading: false, generation: previous.generation + 1 };
      $(kind === "requests" ? "request-list" : "event-list").replaceChildren(el("div", "empty-state", "正在读取所选实例的记录…")); activityError(kind); display(kind === "requests" ? "request-count" : "event-count", "—");
    }
    $("history-list").replaceChildren(el("div", "empty-state", "正在读取所选实例的任务历史…")); display("history-count", "—");
    $("coverage-list").replaceChildren(el("div", "empty-state", "正在读取所选实例的覆盖状态…")); display("coverage-count", "等待状态");
    for (const field of ["metric-attempts", "metric-pages", "metric-posts", "metric-list-only", "metric-bodies", "metric-pending", "run-duration", "current-target", "last-success"]) display(field, "—");
    for (const field of ["metric-failures", "metric-calibration", "metric-list-total", "metric-removed", "metric-body-breakdown", "observed-rate", "next-request", "next-request-time"]) display(field, "等待所选实例状态");
    $("block-panel").hidden = true; $("block-history").hidden = true; $("block-history").open = false; display("block-history-evidence", "");
    $("storage-panel").hidden = true; $("storage-panel").open = false; $("storage-error").hidden = true; display("recovery-reason", "等待所选实例的校准状态。"); display("recovery-phase", "等待状态"); $("recovery-facts").replaceChildren();
    renderRateAudit({});
    display("run-title", `正在读取 ${nodeName()} 的状态`); display("run-reason", "切换实例不会发出任何来源请求或采集控制命令。"); display("job-window", "等待配置"); display("run-start", "等待状态"); display("state-label", "等待状态");
    notice(""); setConnection(false); display("updated-at", "—");
    if (fleetData) renderFleet(fleetData); else $("selected-node").value = id;
    await poll(); return true;
  }
  function nodeHost(value) {
    let host = value.trim();
    if (/^\[[^\]]+\]$/.test(host)) { if (!host.includes(":")) return null; host = host.slice(1, -1); }
    if (host.includes(":")) {
      if (!/^[0-9a-fA-F:.]+$/.test(host)) return null;
      try { new URL(`http://[${host}]/`); return host; } catch { return null; }
    }
    const parts = host.split(".");
    return parts.length === 4 && parts.every((part) => /^(0|[1-9][0-9]{0,2})$/.test(part) && Number(part) <= 255) ? host : null;
  }
  function nodeConnectionMode(mode = $("node-connect-mode").value) {
    const direct = mode === "direct";
    $("node-connect-mode").value = direct ? "direct" : "url";
    $("node-direct-fields").hidden = !direct; $("node-direct-fields").disabled = !direct;
    $("node-url-fields").hidden = direct; $("node-url-fields").disabled = direct;
    $("node-host").required = direct; $("node-port").required = direct; $("node-url").required = !direct;
  }
  function openNodeForm(node = null) {
    if (busy) return;
    editingNode = node ? String(first(node, ["id", "alias"])) : null;
    display("node-dialog-title", node ? "编辑实例登记" : "登记远端实例"); display("save-node", node ? "保存登记修改" : "登记实例");
    $("node-id").value = editingNode || ""; $("node-id").disabled = !!node; $("node-name").value = node?.name || ""; $("node-url").value = node?.base_url || ""; $("node-token").value = ""; $("node-token").required = !node;
    $("node-host").value = ""; $("node-port").value = "8790"; $("node-scheme").value = "http";
    let mode = node ? "url" : "direct";
    if (node?.connection_mode === "direct" && nodeHost(node.host || "") && Number.isInteger(node.port)) {
      mode = "direct"; $("node-host").value = nodeHost(node.host); $("node-port").value = String(node.port); $("node-scheme").value = node.scheme || "http";
    } else if (node?.base_url && node.connection_mode !== "url") {
      try {
        const url = new URL(node.base_url), host = nodeHost(url.hostname);
        if (host && url.port && url.pathname === "/" && !url.search && !url.hash && !url.username && !url.password) {
          mode = "direct"; $("node-host").value = host; $("node-port").value = url.port; $("node-scheme").value = url.protocol.slice(0, -1);
        }
      } catch { /* Keep an unrecognized existing URL visible for correction. */ }
    }
    nodeConnectionMode(mode);
    display("node-token-note", node ? "留空保留现有密钥。输入新密钥时替换服务端配置；浏览器不保存。" : "仅写入当前服务端的私密配置，浏览器不保存，接口不回传。");
    $("node-form-error").hidden = true; $("node-dialog").showModal();
  }
  function confirmNodeRemoval(node) {
    if (busy) return;
    removingNode = String(first(node, ["id", "alias"]));
    display("remove-node-description", `移除 ${node.name || removingNode} 的登记，停止中央控制台对它的后续同步。${removingNode === selectedNode ? "当前视图将返回本机，未保存的页面修改会丢弃。" : ""}`);
    $("remove-node-dialog").showModal();
  }
  async function fleetMutation(path, payload, method, message, submittedToken = "") {
    if (busy || !authenticated) return false;
    busy = true; controls();
    try {
      await api(path, method, payload);
      if (authenticated) { notice(message); return true; }
    } catch (error) {
      const safeError = submittedToken ? error.message.split(submittedToken).join("[已隐藏密钥]") : error.message;
      if (authenticated) { notice(safeError, true); if ($("node-dialog").open) { $("node-form-error").hidden = false; display("node-form-error", safeError); } }
    } finally { $("node-token").value = ""; busy = false; controls(); if (authenticated) await refreshFleet(); }
    return false;
  }
  function jobConfig() {
    if (!status) return null;
    const job = status.job || status.current_job || status.config;
    return job ? objectValue(job.config || job) : null;
  }
  function probePending() {
    return !!first(status, ["retry_pending", "probe_pending", "retry_scheduled"], false);
  }
  function independentStocks(data = status) { return data?.runtime_scope === "stock"; }
  function stockEntries(data = status) {
    const entries = first(data, ["coverage", "stock_coverage", "per_stock"], []);
    return Array.isArray(entries) ? entries : Object.entries(objectValue(entries)).map(([stock, item]) => ({ stock, ...objectValue(item) }));
  }
  function stockRuntimes(data = status) { return stockEntries(data).map((item) => item.runtime).filter(Boolean); }
  function runtimeProbe(runtime) { return Boolean(runtime?.probe || runtime?.probe_pending || runtime?.retry_pending); }
  function stockInFlight(runtime, data = status) {
    return runtime?.request_inflight === true || Boolean(data?.request_inflight && String(data.current?.stock) === String(runtime?.stock) && (runtime?.job == null || data.current?.job == null || String(data.current.job) === String(runtime.job)));
  }
  function runtimeFor(data, stock, job) {
    const candidates = [...stockRuntimes(data), ...(Array.isArray(data?.detached_stock_runtimes) ? data.detached_stock_runtimes : [])];
    return candidates.find((runtime) => String(runtime.stock) === String(stock) && (job == null || String(runtime.job) === String(job)));
  }
  function taskKey(stock, job) { return `${selectedNode}:${job == null ? "current" : job}:${stock}`; }
  function clearTaskProxyDrafts() {
    document.querySelectorAll(".task-proxy-form input[name='username'], .task-proxy-form input[name='password']").forEach((input) => { input.value = ""; });
    taskProxyDrafts.clear(); stockEvidenceOpen.clear();
  }
  function stockButton(action, stock, runtime, detached = false) {
    const labels = { start: "采集", pause: "暂停", retry: "探测" };
    const button = el("button", `button ${action === "start" ? "primary" : "secondary"} small stock-control`, labels[action]);
    button.type = "button"; button.dataset.stockAction = action; button.dataset.stock = String(stock);
    if (runtime?.job != null) button.dataset.stockJob = String(runtime.job);
    if (detached) button.dataset.detached = "1";
    button.setAttribute("aria-label", `${labels[action]}股票 ${stock}${detached ? " 保留的原目标" : ""}`);
    button.addEventListener("click", () => { void controlStock(String(stock), action, runtime?.job); });
    return button;
  }
  function updateStockControls() {
    const supported = independentStocks();
    const locked = busy || !online || !authenticated;
    document.querySelectorAll("[data-stock-action]").forEach((button) => {
      const runtime = runtimeFor(status, button.dataset.stock, button.dataset.stockJob);
      const active = runtime?.state === "running", probe = runtimeProbe(runtime), inFlight = stockInFlight(runtime);
      const detached = button.dataset.detached === "1";
      const action = button.dataset.stockAction;
      button.disabled = Boolean(locked || !supported || !runtime || status?.storage_halt || status?.active_halt || (action === "start" ? detached || active || probe || inFlight || runtime.active_halt || !["paused", "idle"].includes(runtime.state) : action === "pause" ? !(active || probe || runtime.proxy_auto_suspended === false || inFlight) : active || probe || inFlight || !["paused", "blocked", "error"].includes(runtime.state) || !first(runtime, ["current", "current_target", "target"])));
      if (!supported) button.title = "该节点尚不支持逐股控制，请更新节点；不会回退执行全局控制。";
      else if (runtime?.active_halt && action === "start") button.title = "本股原目标阻断仍保留，请先探测成功，再手动采集。";
      else button.title = action === "retry" ? "只请求本股保留的当前目标一次，遵守全局间隔和本股冷却；成功后仍暂停。" : action === "pause" ? "只暂停本股的后续请求，当前请求结束后保留响应。" : "只采集本股，其他股票状态不变。";
    });
    document.querySelectorAll("[data-task-proxy-fields]").forEach((fields) => { fields.disabled = locked || !supported || Boolean(status?.storage_halt); });
    if ($("download-mihomo")) $("download-mihomo").disabled = locked || !supported || mihomoDownloading;
  }
  async function controlStock(stock, action, job) {
    if (!independentStocks()) { notice("所选节点需更新以支持逐股控制；没有发送全局命令。", true); return false; }
    const payload = { action }; if (job != null) payload.job_id = job;
    const messages = { start: `${stock} 已提交采集指令，其他股票状态不变。`, pause: `${stock} 已暂停后续采集，已有响应与队列保留。`, retry: `${stock} 已安排一次原目标探测，遵守全局间隔与本股冷却；成功后仍暂停。` };
    return post(`stocks/${encodeURIComponent(stock)}/control`, payload, messages[action]);
  }
  function stockTargetText(target) {
    if (!target || typeof target !== "object") return target ? textValue(target) : "等待下一项";
    return target.kind === "detail" ? `正文 ${target.post_id || "—"}` : `${target.purpose === "seek" ? "日期定位" : target.purpose === "recovery" ? "校准列表" : "前进列表"}${target.page == null ? "" : " · 源页码 " + number(target.page)}`;
  }
  function appendStockRuntime(row, runtime, detached = false) {
    if (!runtime) return;
    const facts = el("div", "stock-runtime-facts");
    if (runtime.reason) facts.append(el("p", "stock-runtime-reason", runtime.reason));
    const retry = objectValue(runtime.network_retry), target = first(runtime, ["current", "current_target", "target"]);
    const parts = [stockInFlight(runtime) ? "本股请求中" : runtimeProbe(runtime) ? "单次探测已安排" : states[runtime.state] || runtime.state];
    if (target) parts.push(stockTargetText(target));
    const due = first(runtime, ["next_request_at", "next_due_at", "next_due"], retry.retry_at);
    if (due) parts.push(`${runtimeProbe(runtime) ? "探测" : retry.kind ? "重试" : "最早请求"} ${time(due)}`);
    if (retry.kind) parts.push(`${outcomes[retry.kind] || "网络异常"} · 连续 ${number(retry.attempt)} 次`);
    facts.append(el("p", "field-note", parts.filter(Boolean).join(" · ")));
    if (detached) facts.append(el("p", "coverage-gap", `原任务 #${runtime.job} 已移除或归档。仅保留原阻断目标的单次探测，不恢复旧采集队列。`));
    if (runtime.active_halt) {
      const details = el("details", "stock-evidence"), key = taskKey(runtime.stock, runtime.job);
      details.open = stockEvidenceOpen.has(key);
      details.append(el("summary", "", `本股阻断 · ${outcomes[runtime.active_halt] || runtime.active_halt} · 查看证据`), el("pre", "", JSON.stringify(runtime.block_evidence || { reason: runtime.reason, kind: runtime.active_halt }, null, 2)));
      details.addEventListener("toggle", () => { if (details.isConnected) { if (details.open) stockEvidenceOpen.add(key); else stockEvidenceOpen.delete(key); } });
      facts.append(details);
    }
    row.append(facts);
  }
  function appendTaskProxy(row, item, stock, runtime, existingPanels = new Map()) {
    const proxy = objectValue(item.task_proxy), settings = objectValue(proxy.settings), key = taskKey(stock, runtime?.job);
    if (existingPanels.has(key)) { row.append(existingPanels.get(key)); return; }
    let draft = taskProxyDrafts.get(key);
    if (!draft) { draft = { open: false, dirty: false }; taskProxyDrafts.set(key, draft); }
    if (!draft.dirty) Object.assign(draft, { mode: settings.mode || "inherit", endpoint: settings.endpoint || "", outbound: settings.outbound || "", listen_address: settings.mode === "mihomo" ? settings.listen_address || proxy.suggested_listen_address || "127.0.0.1" : proxy.suggested_listen_address || settings.listen_address || "127.0.0.1", username: "", password: "", clear_auth: false });
    const details = el("details", "task-proxy-panel"); details.open = draft.open; details.dataset.taskProxyKey = key;
    const modeLabels = { inherit: "继承节点", direct: "直连", http: "HTTP 代理", mihomo: "Mihomo 独立入口" };
    details.append(el("summary", "task-proxy-summary", `本股出口 · ${modeLabels[settings.mode] || "继承节点"}${settings.outbound ? " · " + settings.outbound : ""}`));
    details.addEventListener("toggle", () => { if (details.isConnected) draft.open = details.open; });
    const lease = objectValue(proxy.lease), facts = [];
    if (lease.endpoint || settings.endpoint) facts.push(lease.endpoint || settings.endpoint);
    if (lease.recent_success_at) facts.push(`该出口最近来源成功 ${time(lease.recent_success_at)}`);
    else facts.push("尚未由来源请求核实");
    if (lease.ip) facts.push(`供应商报告 IP ${lease.ip}`);
    if (lease.expires_at) facts.push(`到期 ${time(lease.expires_at)}`);
    if (proxy.fallback) facts.push("当前已回退直连");
    details.append(el("p", "field-note", facts.join(" · ")));
    if (proxy.last_error) details.append(el("p", "coverage-gap", proxy.last_error));
    const form = el("form", "task-proxy-form"), fields = el("fieldset", ""); fields.dataset.taskProxyFields = "1";
    const input = (name, label, type = "text", placeholder = "") => {
      const id = `task-proxy-${String(stock)}-${String(runtime?.job ?? "current").replace(/[^A-Za-z0-9_-]/g, "_")}-${name}`;
      const wrap = el("div", "task-proxy-field"), caption = el("label", "", label); caption.htmlFor = id;
      const control = el("input", ""); control.id = id; control.name = name; control.type = type; control.autocomplete = type === "password" ? "new-password" : "off"; control.spellcheck = false;
      if (type === "checkbox") control.checked = draft[name] === true; else control.value = draft[name] || "";
      control.placeholder = placeholder; wrap.append(caption, control); return { wrap, control };
    };
    const modeLabel = el("label", "", "本股出站方式"), mode = el("select"); mode.id = `task-proxy-${stock}-${runtime?.job ?? "current"}-mode`; mode.name = "mode"; modeLabel.htmlFor = mode.id;
    for (const [value, label] of Object.entries(modeLabels)) { const option = el("option", "", label); option.value = value; mode.append(option); } mode.value = draft.mode;
    fields.append(modeLabel, mode);
    const suggestedEndpoint = proxy.suggested_endpoint || `http://host.docker.internal:${proxy.suggested_port || 17890}`;
    const address = input("endpoint", "本股 HTTP 代理端点", "url", suggestedEndpoint);
    const endpointHelp = el("p", "field-note", "填写采集节点可访问的 HTTP/mixed 地址。Mac Docker 用 host.docker.internal；原生程序可用 127.0.0.1。Mihomo 为每股使用不同端口，避开日常 7897 端口。");
    const httpFields = el("div", "task-http-fields"); httpFields.append(address.wrap, endpointHelp); fields.append(httpFields);
    const outbound = input("outbound", "Mihomo 节点名", "text", "填写 Mihomo 配置里的具体节点完整名称"), listen = input("listen_address", "监听地址", "text", "127.0.0.1 或 0.0.0.0");
    const mihomoFields = el("div", "task-mihomo-fields"); mihomoFields.append(outbound.wrap, listen.wrap, el("p", "field-note", "保存后下载下方私密分流配置。Clash Verge 在“订阅 → 全局扩展脚本”粘贴并保存；已有自定义脚本时须合并 main 中的采集监听逻辑，保留原逻辑。每个入口绑定具体节点，不切换日常 GLOBAL；节点名不同不证明公网 IP 不同。Docker 访问宿主时监听需接受容器连接，认证由程序生成。")); fields.append(mihomoFields);
    const authFields = el("div", "task-auth-fields proxy-grid");
    const user = input("username", "代理用户名", "text", settings.has_auth ? "已保存，留空保留" : "可选"), password = input("password", "代理密码", "password", settings.has_auth ? "已保存，留空保留" : "可选");
    const clear = input("clear_auth", "明确移除旧 HTTP 认证", "checkbox"); clear.wrap.classList.add("task-auth-clear"); authFields.append(user.wrap, password.wrap, clear.wrap); fields.append(authFields);
    const note = el("p", "field-note", runtime?.detached ? "这是已移除或归档的原任务出口，仅供原失败目标的单次探测。保存不会恢复旧采集队列，不清除原阻断或冷却；其他股票状态不变。" : "保存前只暂停本股并等待本股请求结束；保存后仍暂停。其他股票继续按节点全局间隔采集。出口配置不清除本股原阻断或冷却。");
    const actions = el("div", "task-proxy-actions"), save = el("button", "button secondary small", "保存本股出口"), reset = el("button", "button quiet small", "撤销修改"); save.type = "submit"; reset.type = "button"; reset.hidden = !draft.dirty;
    actions.append(save, reset); fields.append(actions); form.append(fields, note);
    const error = el("p", "page-error"); error.hidden = true; error.setAttribute("role", "status"); form.append(error);
    const showMode = () => { httpFields.hidden = !["http", "mihomo"].includes(mode.value); mihomoFields.hidden = mode.value !== "mihomo"; authFields.hidden = mode.value !== "http"; user.control.disabled = password.control.disabled = clear.control.checked; };
    showMode();
    form.addEventListener("input", () => { for (const field of [mode, address.control, outbound.control, listen.control, user.control, password.control, clear.control]) draft[field.name] = field.type === "checkbox" ? field.checked : field.value; draft.dirty = true; reset.hidden = false; error.hidden = true; showMode(); });
    mode.addEventListener("change", () => {
      if (mode.value === "mihomo") {
        if (!address.control.value.trim()) address.control.value = suggestedEndpoint;
        if (!listen.control.value.trim()) listen.control.value = proxy.suggested_listen_address || "127.0.0.1";
      }
      Object.assign(draft, { mode: mode.value, endpoint: address.control.value, listen_address: listen.control.value, dirty: true });
      reset.hidden = false; showMode();
    });
    reset.addEventListener("click", () => { taskProxyDrafts.delete(key); renderCoverage(status, jobConfig()); controls(); });
    form.addEventListener("submit", async (event) => {
      event.preventDefault(); if (fields.disabled) return;
      try {
        const payload = { mode: mode.value };
        if (["http", "mihomo"].includes(payload.mode)) {
          let parsed; try { parsed = new URL(address.control.value.trim()); } catch { throw new Error("请填写该采集节点可访问的 HTTP 代理地址（http://）。"); }
          if (parsed.protocol !== "http:" || parsed.username || parsed.password || parsed.search || parsed.hash || !["", "/"].includes(parsed.pathname)) throw new Error("代理地址应使用 http://，不带路径、查询或认证。用户名和密码请单独填写。");
          payload.endpoint = address.control.value.trim();
        }
        if (payload.mode === "mihomo") {
          payload.outbound = outbound.control.value.trim(); payload.listen_address = listen.control.value.trim();
          if (!payload.outbound) throw new Error("请填写 Mihomo 配置中的具体节点名称。");
        } else if (payload.mode === "http") { if (user.control.value) payload.username = user.control.value; if (password.control.value) payload.password = password.control.value; if (clear.control.checked) payload.clear_auth = true; }
        const saved = await saveStockProxy(String(stock), runtime?.job, payload);
        draft.username = draft.password = ""; user.control.value = password.control.value = "";
        if (saved) { draft.dirty = false; draft.open = true; }
        renderCoverage(status, jobConfig()); controls();
      } catch (err) { error.textContent = safeProxyMessage(err.message, [user.control.value, password.control.value]); error.hidden = false; }
    });
    details.append(form); row.append(details);
  }
  async function pauseStockAndWait(stock, job, node, selection) {
    let snapshot = await api("status", "GET", undefined, node);
    const verify = () => {
      if (selection !== nodeEpoch) throw new Error("查看实例已变化，尚未修改出口。");
      if (!independentStocks(snapshot)) throw new Error("该节点尚不支持逐股控制，请更新节点；没有发送全局暂停。");
      const runtime = runtimeFor(snapshot, stock, job); if (!runtime) throw new Error("股票任务已变化，请刷新后重新配置出口。"); return runtime;
    };
    let runtime = verify(); applyStatus(snapshot);
    if (runtime.state === "running" || runtimeProbe(runtime) || stockInFlight(runtime, snapshot) || runtime.proxy_auto_suspended === false) {
      snapshot = await api(`stocks/${encodeURIComponent(stock)}/control`, "POST", { action: "pause", ...(job == null ? {} : { job_id: job }) }, node);
      runtime = verify(); applyStatus(snapshot);
    }
    const deadline = Date.now() + 45000;
    while (runtime.state === "running" || runtimeProbe(runtime) || stockInFlight(runtime, snapshot)) {
      if (Date.now() >= deadline) throw new Error("本股已提交暂停，当前请求尚未结束。出口尚未修改，请等待后重试。");
      await new Promise((resolve) => setTimeout(resolve, 750)); snapshot = await api("status", "GET", undefined, node); runtime = verify(); applyStatus(snapshot);
    }
  }
  async function saveStockProxy(stock, job, payload) {
    if (busy || !authenticated || !online || !independentStocks()) return false;
    const node = selectedNode, selection = nodeEpoch, detached = runtimeFor(status, stock, job)?.detached === true; busy = true; statusGeneration += 1; controls(); let saved = false;
    try {
      notice(`正在暂停 ${stock} 并保存本股出口；其他股票的采集状态不变。`);
      await pauseStockAndWait(stock, job, node, selection);
      const result = await api(`stocks/${encodeURIComponent(stock)}/proxy/config`, "POST", { ...payload, ...(job == null ? {} : { job_id: job }) }, node);
      if (selection !== nodeEpoch) throw new Error("查看实例已变化，请重新读取原节点状态。");
      applyStatus(result); saved = true; notice(`${stock} ${detached ? "原任务的探测" : "的"}出口已保存，仍保持暂停。${payload.mode === "mihomo" ? "请下载分流配置，在 Clash Verge 的全局扩展脚本中合并并保存，再对本股单次探测；配置生成不表示监听已可用。" : detached ? "已有阻断与冷却保留，只能单次探测原失败目标，不恢复旧采集队列。" : "已有阻断与冷却保留，检查后对本股探测或采集。"}`);
    } catch (error) { notice(safeProxyMessage(error.message, [payload.username, payload.password]), true); }
    finally { busy = false; controls(); if (authenticated) await poll(); }
    return saved;
  }
  async function downloadMihomo() {
    if (busy || mihomoDownloading || !independentStocks()) return;
    const node = selectedNode, selection = nodeEpoch; mihomoDownloading = true; controls();
    try {
      const result = await api("mihomo/config", "GET", undefined, node);
      if (selection !== nodeEpoch || !authenticated) return;
      const hasScript = typeof result.script === "string" && Boolean(result.script.trim());
      const contents = hasScript ? result.script : result.yaml;
      if (typeof contents !== "string" || !contents.trim()) throw new Error("该节点没有返回有效的 Mihomo 分流配置。");
      const url = URL.createObjectURL(new Blob([contents], { type: hasScript ? "text/javascript;charset=utf-8" : "application/yaml;charset=utf-8" }));
      const anchor = el("a"); anchor.href = url; anchor.download = String(hasScript ? result.script_filename || "collector-mihomo-extension.js" : result.filename || "collector-mihomo-listeners.yaml").replace(/[^A-Za-z0-9._-]/g, "_"); document.body.append(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
      notice(`${nodeName(node)} 的私密 Mihomo ${hasScript ? "扩展脚本" : "监听 YAML"}已下载，含入口认证。${hasScript ? "请在 Clash Verge“订阅 → 全局扩展脚本”粘贴并保存；已有脚本时合并采集逻辑，保留原逻辑。" : "该节点只提供 YAML，请将 listeners 条目合并到代理配置并加载；更新节点可下载 Clash Verge 扩展脚本。"}加载后逐股探测，没有切换日常代理或发送股吧请求。`);
    } catch (error) { if (selection === nodeEpoch && authenticated) notice(error.status === 404 ? "所选节点需更新以支持 Mihomo 配置下载；没有从主控本机生成替代配置。" : error.message, true); }
    finally { mihomoDownloading = false; controls(); }
  }
  function clearProxySecrets() {
    for (const id of ["proxy-username", "proxy-password", "proxy-api-url"]) $(id).value = "";
  }
  function proxyError(message = "") {
    $("proxy-form-error").hidden = !message; display("proxy-form-error", message);
  }
  function proxyVisibility() {
    const mode = $("proxy-mode").value;
    const dynamic = dynamicProxyMode(mode);
    for (const [id, visible] of [["proxy-http-fields", mode === "http"], ["proxy-mayi-fields", dynamic], ["proxy-auth-fields", mode !== "direct" || first(proxyState, ["has_auth"], proxyState?.settings?.has_auth) === true], ["proxy-rotation-fields", dynamic], ["proxy-auto-fields", dynamic]]) {
      $(id).hidden = !visible; $(id).disabled = !visible;
    }
    display("proxy-api-label", mode === "qingguo" ? "青果国内短效 IP 提取 API（私密）" : "动态 IP 提取 API（私密）");
    display("proxy-api-help", mode === "qingguo" ? "仅支持青果国内短效代理的新接口：share.proxy.qg.net/get 或 share.proxy.qg.net/aggregate/get。填写后台生成的完整 API URL，num=1，默认返回 JSON，无需蚂蚁的 type/mode 参数。程序读取实际到期时间；海外代理、长效代理和旧版 /allocate 暂不支持，完整 API URL 不回显。" : "使用蚂蚁生成的单 IP、HTTP、JSON 提取接口（num=1、type=2、mode=1），返回 IP、端口及到期时间。这里配置动态提取来源，不是填写一个永久静态 IP；完整 API URL 不回显。");
    display("proxy-auth-note", mode === "qingguo" ? "青果代理认证的 Authkey / Authpwd 分别填入用户名 / 密码；使用节点出口 IP 白名单时可两项留空，若有旧认证须勾选明确移除。提取 API URL 中的 pwd 不作为代理密码。认证字段不回显，留空会保留旧值。" : "用户名和密码不回显。输入为空会保留旧值；修改密钥后输入框会清空。");
    const savedMode = first(proxyState?.settings, ["mode"], proxyState?.mode);
    const apiConfigured = first(proxyState, ["has_api_url"], proxyState?.settings?.has_api_url) === true;
    $("proxy-api-url").placeholder = apiConfigured && (!dynamic || mode === savedMode) ? "已配置，留空保留" : mode === "qingguo" ? "填写青果官方生成的提取 API URL" : "填写蚂蚁生成的提取 API URL";
    const recovering = dynamic && $("proxy-auto-recover").checked;
    $("proxy-recovery-limits").hidden = !recovering;
    $("proxy-recovery-cooldown").disabled = !recovering;
    $("proxy-recovery-max").disabled = !recovering;
    $("proxy-username").disabled = $("proxy-clear-auth").checked;
    $("proxy-password").disabled = $("proxy-clear-auth").checked;
    $("proxy-api-url").disabled = $("proxy-clear-api").checked;
    $("proxy-api-clear-row").hidden = !dynamic && first(proxyState, ["has_api_url"], proxyState?.settings?.has_api_url) !== true;
  }
  function loadProxyForm(data) {
    if (proxyDirty) return;
    const settings = objectValue(data.settings);
    $("proxy-mode").value = first(settings, ["mode"], data.mode || "direct");
    for (const [id, field] of [["proxy-endpoint", "endpoint"], ["proxy-rotate-seconds", "rotate_seconds"], ["proxy-rotate-requests", "rotate_requests"], ["proxy-daily-limit", "daily_limit"], ["proxy-recovery-cooldown", "recovery_cooldown_seconds"], ["proxy-recovery-max", "recovery_max_attempts"]]) $(id).value = settings[field] == null ? "" : String(settings[field]);
    $("proxy-auto-recover").checked = settings.auto_recover === true;
    $("proxy-clear-auth").checked = false; $("proxy-clear-api").checked = false;
    clearProxySecrets(); proxyVisibility();
  }
  function clearProxyForm() {
    proxyState = null; proxyDirty = false; proxySupported = null; proxyLoading = false; proxyReadSerial += 1;
    $("proxy-panel").open = false;
    for (const id of ["proxy-endpoint", "proxy-rotate-seconds", "proxy-rotate-requests", "proxy-daily-limit", "proxy-recovery-cooldown", "proxy-recovery-max"]) $(id).value = "";
    for (const id of ["proxy-auto-recover", "proxy-clear-auth", "proxy-clear-api"]) $(id).checked = false;
    $("proxy-mode").value = "direct"; clearProxySecrets(); proxyVisibility(); proxyError();
    display("proxy-summary-state", "尚未读取"); display("proxy-config-state", "等待脱敏配置");
    display("proxy-node-note", `作用节点：${nodeName()}（${selectedNode}）。手机与主控的管理连接不使用此代理。`);
    display("proxy-capability-note", "展开后读取该节点的代理能力；旧节点需要升级。");
    $("proxy-facts").replaceChildren(); display("proxy-lease-note", "尚无候选出站状态。"); display("proxy-recovery-note", ""); $("proxy-status-error").hidden = true;
  }
  function safeProxyMessage(message, secrets = []) {
    let safe = textValue(message);
    for (const secret of secrets.filter(Boolean).sort((a, b) => b.length - a.length)) safe = safe.split(secret).join("[已隐藏]");
    return safe;
  }
  function renderProxy(data) {
    if (!data || !Object.hasOwn(proxyModes, first(data.settings, ["mode"], data.mode))) return;
    proxyState = data; proxySupported = true;
    const settings = objectValue(data.settings), mode = first(settings, ["mode"], data.mode);
    const fallback = dynamicProxyMode(mode) && data.effective_mode === "direct" && data.fallback ? objectValue(data.fallback) : null;
    display("proxy-summary-state", fallback ? `${proxyModes[mode]} · 已回退直连` : proxyModes[mode]);
    display("proxy-config-state", proxyDirty ? "页面修改尚未保存" : "已读取保存配置");
    display("proxy-node-note", `作用节点：${nodeName()}（${selectedNode}）。这里只改变该节点的来源请求出站，手机与主控的管理连接不使用此代理。`);
    display("proxy-capability-note", "保存只修改该节点的代理配置，不会清除已有阻断、冷却或响应证据。已保存的用户名、密码和完整提取 API 不回显。");
    const auth = first(data, ["has_auth"], settings.has_auth) === true, apiConfigured = first(data, ["has_api_url"], settings.has_api_url) === true;
    for (const id of ["proxy-username", "proxy-password"]) $(id).placeholder = auth ? "已配置，留空保留" : "未配置，可留空";
    $("proxy-api-url").placeholder = apiConfigured ? "已配置，留空保留" : "填写供应商生成的提取 API URL";
    loadProxyForm(data);
    const daily = objectValue(data.daily_extractions), lease = objectValue(data.lease), facts = $("proxy-facts"); facts.replaceChildren();
    for (const [label, value] of [["今日提取", number(daily.count)], ["每日上限", number(first(daily, ["limit"], settings.daily_limit))], ["今日剩余", number(daily.remaining)], ["连续恢复", number(data.recovery_attempts)]]) facts.append(el("span", "", `${label} ${value}`));
    if (daily.date) facts.append(el("span", "", `额度日期 ${daily.date}`));
    if (fallback) {
      const reasons = { provider_budget: "每日提取额度已用完", provider_balance: "供应商余额不足", provider_unavailable: "提取服务暂不可用", provider_no_ip: "提取接口未返回 IP", provider_duplicate: "返回的 IP 仍在隔离期", provider_expiry: "返回的 IP 有效期不足", provider_auth: "提取接口认证失败", provider_schema: "提取响应格式无法识别" };
      const parts = [`当前使用 ${nodeName()} 的原直连出口，不走已配置的 HTTP 代理`, `回退原因：${reasons[fallback.reason] || "未取得可用动态 IP"}`];
      const message = safeProxyMessage(fallback.message, [$("proxy-username").value, $("proxy-password").value, $("proxy-api-url").value]);
      if (message) parts.push(message);
      if (fallback.since) parts.push(`开始回退 ${time(fallback.since)}`);
      if (fallback.manual_retry === true) parts.push(fallback.reason === "provider_balance" ? "自动提取已暂停；充值后保存配置或手动更换出站后再试" : "自动提取已暂停；检查 API 配置后保存，或手动更换出站后再试");
      else if (fallback.retry_at) parts.push(`下次最早提取 ${time(fallback.retry_at)}，仅在有采集请求时尝试`);
      if (fallback.blocked === true) parts.push("来源已阻断直连，采集已暂停；不会自动用直连重试，检查证据后可安排单次探测");
      display("proxy-lease-note", parts.join(" · "));
    } else if (data.lease) {
      const parts = [`${mode === "qingguo" ? "代理连接端点" : "候选出站"}：${lease.endpoint || "尚未返回"}`, `租约剩余 ${lease.remaining_seconds == null ? "—" : duration(lease.remaining_seconds)}`, `候选来源尝试 ${number(lease.requests)}`];
      if (mode === "qingguo" && lease.ip) parts.splice(1, 0, `供应商报告出口 IP ${lease.ip}`);
      const reasons = { explicit_endpoint: "使用已配置端点", initial_extraction: "首次提取", lease_expiring: "租约即将到期", elapsed_rotation: "达到使用时间", attempt_count_rotation: "达到尝试次数", manual_rotation: "手动更换" };
      if (lease.selection_reason) parts.push(`选择原因：${reasons[lease.selection_reason] || textValue(lease.selection_reason)}`);
      parts.push(lease.recent_success_at ? `最近来源核实 ${time(lease.recent_success_at)}（${outcomes[lease.recent_success_outcome] || "已核实响应"}）` : "尚未取得此候选的来源成功证据");
      if (lease.expires_at) parts.push(`到期 ${time(lease.expires_at)}`);
      display("proxy-lease-note", parts.join(" · "));
    } else display("proxy-lease-note", mode === "direct" ? "当前为直连，没有代理候选。" : "尚无候选租约。更换操作只安排下一次出站，来源结果仍须实际请求核实。");
    const suspended = first(data, ["auto_suspended"], status?.proxy?.auto_suspended) === true;
    display("proxy-recovery-note", fallback ? "回退直连保留原采集间隔与来源冷却；遇到验证码或限流仍会暂停，不会自动重复直连请求。恢复动态 IP 后仍须由实际来源响应核实可用性。" : !dynamicProxyMode(mode) || settings.auto_recover !== true ? "自动恢复未启用；普通重试仅安排一次探测。" : `自动恢复已配置${suspended ? "，目前已挂起；点击开始或继续后才允许运行" : "，受每日额度、连续次数和冷却约束"}。冷却 ${duration(settings.recovery_cooldown_seconds)}，最多连续 ${number(settings.recovery_max_attempts)} 次${data.next_recovery_at ? "；下次最早 " + time(data.next_recovery_at) : ""}。保存配置后保持暂停，不会自动请求来源。`);
    const error = safeProxyMessage(data.last_error, [$("proxy-username").value, $("proxy-password").value, $("proxy-api-url").value]);
    $("proxy-status-error").hidden = !error; display("proxy-status-error", error);
  }
  async function readProxy() {
    if (!authenticated || busy || proxyLoading) return;
    const target = selectedNode, selection = nodeEpoch, generation = statusGeneration, serial = ++proxyReadSerial;
    proxyLoading = true; controls();
    try {
      const data = await api("proxy", "GET", undefined, target);
      if (!authenticated || selection !== nodeEpoch || generation !== statusGeneration || serial !== proxyReadSerial) return;
      const proxy = data?.proxy || data;
      if (!Object.hasOwn(proxyModes, first(proxy?.settings, ["mode"], proxy?.mode))) throw new Error("该节点返回的代理配置格式无法识别，需要更新节点。");
      renderProxy(proxy); proxyError();
    } catch (error) {
      if (!authenticated || selection !== nodeEpoch || generation !== statusGeneration || serial !== proxyReadSerial) return;
      if ([400, 404, 405].includes(error.status)) {
        proxySupported = false; display("proxy-summary-state", "节点需更新");
        display("proxy-capability-note", "所选节点尚未提供代理配置接口，请更新该节点和主控。不会回退到主控本机修改代理。");
      }
      proxyError(safeProxyMessage(error.message, [$("proxy-username").value, $("proxy-password").value, $("proxy-api-url").value]));
    } finally { if (selection === nodeEpoch && serial === proxyReadSerial) { proxyLoading = false; controls(); } }
  }
  function controls() {
    const state = status ? status.state : "idle";
    const job = jobConfig();
    const locked = busy || !online || !authenticated;
    const scoped = independentStocks(), runtimes = stockRuntimes();
    const pending = probePending() || (scoped && runtimes.some(runtimeProbe));
    $("start").disabled = locked || !job || status?.storage_halt || status?.active_halt || (scoped ? !runtimes.some((runtime) => ["paused", "idle"].includes(runtime.state) && !runtime.active_halt && !runtimeProbe(runtime) && !stockInFlight(runtime)) : pending || status?.request_inflight || !["paused", "idle"].includes(state));
    const autoWaiting = ["blocked", "error"].includes(state) && status?.proxy?.settings?.auto_recover === true && status?.proxy?.auto_suspended === false && status?.proxy?.fallback?.blocked !== true;
    $("pause").disabled = locked || !(state === "running" || pending || autoWaiting || (scoped && runtimes.some((runtime) => runtime.state === "running" || runtime.proxy_auto_suspended === false)));
    $("retry").disabled = locked || (status?.storage_halt ? state === "running" : scoped ? Boolean(status?.active_halt) || ![...runtimes, ...(status?.detached_stock_runtimes || [])].some((runtime) => ["paused", "blocked", "error"].includes(runtime.state) && !runtimeProbe(runtime) && !stockInFlight(runtime) && first(runtime, ["current", "current_target", "target"])) : pending || status?.request_inflight || ((!job && !status?.active_halt) || !first(status, ["current_target", "current", "target"]) || !["paused", "blocked", "error"].includes(state)));
    display("start", scoped ? "批量采集" : "开始采集"); display("pause", scoped ? "批量暂停" : "暂停");
    display("retry", status?.storage_halt ? "重试数据库写入" : scoped && !status?.active_halt ? "批量探测" : "单次探测重试");
    const awaitingStop = state === "running" || pending || status?.request_inflight || (scoped && runtimes.some((runtime) => runtime.state === "running"));
    $("config-fields").disabled = locked || awaitingStop;
    $("save-config").disabled = $("config-fields").disabled || (!job && !!status?.active_halt);
    display("save-config", job ? "保存当前任务修改" : "创建任务");
    $("config-management").hidden = !job;
    $("pause-edit").hidden = !job || !awaitingStop;
    $("pause-edit").disabled = locked;
    display("pause-edit", state === "running" || pending ? "暂停后编辑" : "等待请求结束后编辑");
    $("delete-job").hidden = !job;
    $("delete-job").disabled = locked;
    $("reset-config").hidden = !job || !formDirty;
    $("reset-config").disabled = locked || awaitingStop;
    document.querySelectorAll("[data-remove-stock]").forEach((button) => { button.disabled = locked || !job; });
    $("refresh").disabled = busy || polling;
    $("logout").disabled = busy;
    $("selected-node").disabled = busy;
    $("add-node").disabled = busy || !authenticated;
    $("sync-fleet").disabled = busy || !authenticated || !fleetData;
    $("save-node").disabled = busy;
    $("proxy-fields").disabled = locked || proxySupported !== true;
    $("proxy-refresh").disabled = busy || !authenticated || proxyLoading;
    $("proxy-save").disabled = locked || proxySupported !== true || proxyLoading;
    display("proxy-save", awaitingStop ? "暂停并保存代理配置" : "保存代理配置");
    $("proxy-reset").hidden = !proxyDirty; $("proxy-reset").disabled = busy || !authenticated;
    $("proxy-rotate").disabled = locked || proxySupported !== true || proxyDirty || pending || !!status?.storage_halt || (["blocked", "error"].includes(state) && status?.request_inflight);
    const proxyMode = first(proxyState?.settings, ["mode"], proxyState?.mode);
    const blockedProxy = ["blocked", "error"].includes(state);
    display("proxy-rotate", dynamicProxyMode(proxyMode) ? (blockedProxy ? "更换 IP 并单次探测" : "更换下一次出口") : (blockedProxy ? "重选出站并单次探测" : "重选下一次出站"));
    proxyVisibility();
    updateStockControls();
    updateDownloadLinks();
    document.querySelectorAll("[data-fleet-action]").forEach((button) => { button.disabled = busy || !authenticated || button.dataset.current === "1"; });
    updatePager("requests"); updatePager("events");
    if (status?.storage_halt) display("action-note", "采集数据库写入异常，源采集已暂停。重试仅修复本地写入，成功后仍暂停。");
    else if (scoped && status?.active_halt) display("action-note", "旧阻断记录无法定位到原股票任务，节点保持保护暂停。需要恢复原任务与请求证据，不能猜测目标探测或直接开始。");
    else if (scoped) display("action-note", "各股控制在下方股票卡片右上角。批量采集只启动可继续的股票，保留其他股票的阻断；暂停作用于全部股票。多个探测目标请按卡片分别探测。");
    else if (pending) display("action-note", "已安排单次探测；成功后保持暂停。");
    else if (state === "running" && status?.network_retry) display("action-note", "网络异常会按退避时间自动重试；点击暂停可停止后续请求。");
    else if (state === "running") display("action-note", "关闭页面后服务端仍继续采集。暂停将在当前请求结束后生效。");
    else if (autoWaiting) display("action-note", "动态 IP 自动恢复正在等待冷却与额度。点击暂停可挂起自动恢复；单次探测只做一次。");
    else if (["blocked", "error"].includes(state)) display("action-note", "已暂停后续请求。检查证据后，可手动安排一次探测。");
    else if (state === "completed") display("action-note", "请核对覆盖。可编辑当前窗口，或删除任务后新建。");
    else if (job) display("action-note", "点击开始后，按持久化的全局请求间隔调度。");
    else display("action-note", "保存配置不会发起源请求。");
    if (busy) display("config-message", "正在提交操作或等待当前请求结束…");
    else if (awaitingStop) display("config-message", "先暂停并等待正在执行的请求结束，再编辑配置。");
    else if (!job && status?.active_halt) display("config-message", "实例阻断仍保留。先对原目标单次探测成功后，才能创建新任务。");
    else if (job && status?.active_halt) display("config-message", "可以修改配置；保存后仍保留阻断状态，不会自动开始。");
    else display("config-message", job ? "保存修改后保持暂停；已有数据和原始响应保留。" : "创建后保持暂停，手动启动。");
  }
  function setConnection(ok) {
    online = ok;
    $("connection-dot").className = `connection-dot ${ok ? "online" : "offline"}`;
    display("connection-text", ok ? "实例已连接" : "所选实例未连接");
    if (lastPoll) display("updated-at", `${ok ? "更新于" : "最近更新"} ${time(lastPoll, false)}`);
    controls();
  }
  function defaultDates(years = 1) {
    const parts = Object.fromEntries(new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(new Date()).filter((part) => part.type !== "literal").map((part) => [part.type, part.value]));
    const end = `${parts.year}-${parts.month}-${parts.day}`;
    const year = Number(parts.year) - years;
    const day = Math.min(Number(parts.day), new Date(Date.UTC(year, Number(parts.month), 0)).getUTCDate());
    $("from-date").value = `${year}-${parts.month}-${String(day).padStart(2, "0")}`;
    $("to-date").value = end;
  }
  function loadForm(config) {
    if (!config || formDirty) return;
    $("stocks").value = Array.isArray(config.stocks) ? config.stocks.join("，") : "";
    if (config.from_date) $("from-date").value = config.from_date;
    if (config.to_date) $("to-date").value = config.to_date;
    $("interval").value = config.interval_seconds ?? 60;
    $("client").value = config.client || "curl";
    loadedConfig = true;
    formJobId = first(status?.job, ["id"], first(status, ["job_id"]));
    formConfigSignature = JSON.stringify(config);
    display("config-state", "已保存");
    display("config-message", "保存后保持暂停，手动启动。");
  }
  function clearForm() {
    formDirty = false; loadedConfig = false; formJobId = null; formConfigSignature = "";
    $("stocks").value = ""; $("interval").value = "60"; $("client").value = "curl";
    defaultDates(1); display("config-state", "未配置");
  }
  function counter(name, aliases = []) {
    const stats = objectValue(first(status, ["aggregate", "counters", "metrics", "stats", "counts"], {}));
    return first(stats, [name, ...aliases], first(status, [name, ...aliases]));
  }
  function renderTiming() {
    if (!status) return;
    const state = status.state;
    const due = first(status, ["next_request_at", "next_due_at", "next_allowed_at"]);
    const dueMs = epoch(due);
    const active = state === "running" || probePending();
    const serverNow = epoch(status.server_time) == null ? Date.now() : epoch(status.server_time) + (lastPoll ? Math.max(0, Date.now() - epoch(lastPoll)) : 0);
    if (active && dueMs != null) {
      const seconds = Math.ceil((dueMs - serverNow) / 1000);
      display("next-request", seconds > 0 ? `${duration(seconds)} 后` : "等待工作进程");
      display("next-request-time", time(due));
    } else {
      display("next-request", state === "running" ? "调度中" : "未安排");
      display("next-request-time", dueMs && !["completed", "idle"].includes(state) ? `最早可请求 ${time(due)}` : "由服务端统一调度");
    }
    const run = objectValue(status.run);
    const start = first(status, ["run_started_at", "started_at"], first(run, ["started_at", "start_at"], first(status.job, ["started_at"])));
    let seconds = first(status, ["continuous_run_seconds", "run_duration_seconds", "runtime_seconds", "observed_run_seconds", "duration_seconds"], first(run, ["duration_seconds", "runtime_seconds"]));
    if (seconds == null && epoch(start) != null) {
      const end = first(status, ["run_ended_at", "paused_at"], first(run, ["ended_at"]));
      seconds = ((state === "running" ? Date.now() : epoch(end) || epoch(status.updated_at) || epoch(start)) - epoch(start)) / 1000;
    } else if (seconds != null && state === "running" && lastPoll) seconds = Number(seconds) + Math.max(0, (Date.now() - epoch(lastPoll)) / 1000);
    display("run-duration-label", status.continuous_run_seconds != null || status.run_started_at ? "本次连续运行" : "任务历时（含暂停）");
    display("run-duration", !start || seconds == null ? "—" : duration(seconds));
    display("run-start", !start ? "尚未启动" : status.job_age_seconds != null ? `任务历时 ${duration(status.job_age_seconds)}（含暂停）` : `开始于 ${time(start)}`);
    display("retry-due", dueMs ? `当前最早可请求时间：${time(due)}（Asia/Shanghai）。` : "具体发送时间由服务端的持久化间隔和冷却期决定。");
  }
  function renderStatus(data) {
    status = data;
    const config = jobConfig();
    const state = data.state || "idle";
    const pending = probePending();
    const networkRetry = objectValue(data.network_retry);
    const hasNetworkRetry = Object.keys(networkRetry).length > 0;
    const retryWaiting = state === "running" && hasNetworkRetry && !pending;
    display("state-label", pending ? "单次探测已安排" : retryWaiting ? "等待网络重试" : states[state] || state);
    $("state-badge").dataset.state = state;
    const titles = { idle: "配置窗口，开始长期观察", running: "正在按间隔采集", paused: config ? "任务已暂停，进度已保留" : "配置窗口，开始长期观察", blocked: "观测到访问拦截，已暂停", error: "观测到异常，已暂停", completed: "采集已结束，请核对覆盖" };
    display("run-title", pending ? "等待单次探测，不会自动继续" : retryWaiting ? "网络暂时失败，等待自动重试" : titles[state] || "等待检查任务状态");
    const genericReasons = { idle: "尚未设置股票和日期窗口。保存配置后，手动启动采集。", paused: config ? "恢复采集后继续使用已保存的队列和请求间隔。" : "尚未设置股票和日期窗口。保存配置后，手动启动采集。", running: "列表和正文串行获取；暂时网络异常按间隔退避重试，验证码或身份核实等保护性拦截会暂停。", blocked: "保留响应和失败位置，等待人工检查。", error: "保留响应和异常原因，等待人工检查。", completed: "本任务已停止，请逐股核对请求范围、源数据终点和未解决的缺口。" };
    display("run-reason", textValue(data.reason) || genericReasons[state]);
    if (independentStocks(data)) {
      const runtimes = stockRuntimes(data), active = runtimes.filter((runtime) => runtime.state === "running").length, blocked = runtimes.filter((runtime) => runtime.active_halt || ["blocked", "error"].includes(runtime.state)).length, probes = runtimes.filter(runtimeProbe).length;
      display("state-label", `${states[state] || state}${runtimes.length ? ` · 采集 ${active} / 阻断 ${blocked}${probes ? " / 探测 " + probes : ""}` : ""}`);
      if (!data.storage_halt && !data.active_halt) {
        display("run-title", active ? "各股独立调度，按节点间隔采集" : probes ? "等待各股单次探测" : blocked ? "部分股票等待处理，逐股查看状态" : titles[state] || "逐股查看采集状态");
        display("run-reason", textValue(data.reason) || "每只股票独立保存状态与出口。一股阻断或网络退避不暂停其他股票，节点仍共用一个请求间隔。");
      }
    }
    $("network-retry-note").hidden = !hasNetworkRetry;
    if (hasNetworkRetry) {
      const retryAt = first(networkRetry, ["retry_at"], first(data, ["next_request_at", "next_due_at", "next_allowed_at"]));
      const retryError = first(networkRetry, ["error", "reason"], "网络请求未成功，原目标仍保留。");
      display("network-retry-note", `${outcomes[networkRetry.kind] || "网络异常"} · 连续失败 ${number(networkRetry.attempt)} 次 · ${retryWaiting ? "下次重试 " + time(retryAt) : pending ? "仅执行已安排的单次探测" : "已暂停，点击继续后才会重试"} · ${textValue(retryError)}`);
    }
    if (config) {
      const effectiveTo = first(data.job, ["effective_to_shanghai", "effective_to"]);
      display("job-window", `${config.from_date || "—"} 至 ${config.to_date || "—"}${effectiveTo ? " · 本次截止 " + time(effectiveTo) : ""}`);
      const jobId = first(data.job, ["id"], first(data, ["job_id"]));
      if (!formDirty && (!loadedConfig || formJobId !== jobId || formConfigSignature !== JSON.stringify(config))) loadForm(config);
      if (!formDirty) display("config-state", "已保存");
    } else {
      display("job-window", "未配置日期窗口");
      if (loadedConfig && !formDirty) clearForm();
      if (!formDirty) display("config-state", "未配置");
    }
    const target = first(data, ["current_target", "current", "target"]);
    if (target && typeof target === "object") {
      const kind = first(target, ["kind", "type"]);
      const stock = first(target, ["stock", "stock_code", "bar_code"], "");
      const page = first(target, ["page", "page_number"]);
      const id = first(target, ["post_id", "source_item_id", "id"]);
      const label = kind === "detail" || id != null ? `${stock ? stock + " · " : ""}正文 ${id || ""}` : `${stock || "列表"}${target.purpose === "seek" ? " · 日期定位列表" : target.purpose === "recovery" ? " · 校准列表" : ""}${page == null ? "" : " · 源页码 " + page}`;
      display("current-target", label.trim());
    } else display("current-target", target || (config ? "等待下一项" : "—"));
    const success = first(data, ["last_success_at", "last_success", "last_source_success"]);
    display("last-success", success ? time(success) : "—");
    const sourceAttempts = counter("source_attempts", ["network_attempts"]);
    display("metric-attempt-label", sourceAttempts == null ? "请求台账（旧版）" : "实际来源请求");
    display("metric-attempts", number(sourceAttempts == null ? counter("attempts", ["requests", "request_count"]) : sourceAttempts));
    display("metric-pages", number(counter("list_pages", ["list_pages_success", "pages"])));
    const forwardPages = counter("list_pages", ["list_pages_success", "pages"]);
    const calibrationPages = counter("calibration_pages");
    display("metric-calibration", `校准成功 ${number(calibrationPages)} / 尝试 ${number(counter("calibration_requests"))}`);
    display("metric-list-total", `列表成功合计 ${number(forwardPages == null || calibrationPages == null ? null : Number(forwardPages) + Number(calibrationPages))} 次`);
    const coverageRecords = first(data, ["coverage", "stock_coverage", "per_stock"], []);
    const currentWindowCounts = (Array.isArray(coverageRecords) ? coverageRecords : Object.values(objectValue(coverageRecords))).some((entry) => entry && Object.hasOwn(entry, "post_time_range"));
    display("metric-post-label", currentWindowCounts ? "已发现窗口内帖子" : config ? "帖子记录（旧节点口径）" : "已采集帖子");
    display("metric-post-note", currentWindowCounts ? "含定位采样与顺序采集 · 不代表连续覆盖" : config ? "需更新节点以核对当前窗口统计" : "每条保留列表记录与标题");
    display("metric-posts", number(counter("unique_posts", ["posts", "posts_count"])));
    display("metric-list-only", number(counter("list_only", ["list_only_posts"])));
    display("metric-bodies", number(counter("body_complete", ["bodies_complete", "detail_success", "complete_posts"])));
    display("metric-pending", number(counter("pending", ["pending_details", "body_pending"])));
    display("metric-failures", `失败 / 异常 ${number(counter("failures", ["failed", "errors"]))}`);
    display("metric-removed", `详情不可访问 ${number(counter("removed", ["deleted"]))}`);
    display("metric-body-breakdown", `非空 ${number(counter("body_nonempty", ["nonempty_body", "nonempty_bodies", "nonempty", "body_non_empty"]))} · 有效空正文 ${number(counter("body_empty", ["source_empty_body", "empty_bodies", "empty"]))}`);
    renderBlock(data);
    renderCoverage(data, config);
    renderRecovery(data);
    renderStorage(data);
    renderRateAudit(data);
    if (data.proxy) renderProxy(data.proxy);
    renderTiming();
    controls();
  }
  function renderRateAudit(data) {
    const audit = objectValue(data.rate_audit), scope = objectValue(audit.scope), unknown = objectValue(audit.unknown);
    const facts = $("rate-facts"); facts.replaceChildren();
    if (audit.confirmed_requests == null || !audit.scope) {
      display("rate-state", "未可核验"); $("rate-state").className = "tag";
      display("rate-scope", "该实例未返回实际网络请求审计；仅凭帖子数量或配置不能判断间隔是否遵守。");
      display("rate-uncertainty", "需升级节点或对该实例的真实台账执行只读审计。");
      display("observed-rate", "网络尝试速率未可核验，不能由帖子数推算");
      return;
    }
    const violations = audit.violations?.count, checked = audit.checked_pairs;
    const unknownKeys = ["attempt_rows", "timing_rows", "unfinished_rows", "pairs", "overlaps", "clock_anomalies"];
    const fullAudit = ["fail", "unknown", "pass"].includes(audit.verdict) && unknownKeys.every((key) => Number.isInteger(unknown[key]) && unknown[key] >= 0);
    const knownTimings = fullAudit && unknownKeys.every((key) => unknown[key] === 0);
    const failed = audit.verdict === "fail" || Number(violations) > 0;
    const passed = audit.verdict === "pass" && knownTimings && violations === 0 && Number(checked) > 0;
    display("rate-state", failed ? "发现已知违规" : !fullAudit ? "未可核验" : passed ? `所示样本未观察到 <${number(audit.interval_seconds)} 秒` : "存在未知 / 样本不足，尚不能确认");
    $("rate-state").className = `tag${failed ? " warning" : ""}`;
    display("rate-scope", `${scope.mode === "all" ? "全量台账" : "近期台账"} · 请求 ID ${number(scope.first_request_id)} 至 ${number(scope.last_request_id)}${scope.limit != null ? " · 最多 " + number(scope.limit) + " 行" : ""}${scope.truncated === true ? " · 有更早记录未纳入" : ""} · 网络尝试开始 ${time(audit.first_started_at)} 至 ${time(audit.last_started_at)}`);
    const config = data.job?.config || data.config;
    const begin = epoch(audit.first_started_at), end = epoch(audit.last_started_at);
    display("observed-rate", passed && Number(audit.confirmed_requests) > 1 && begin != null && end > begin ? `已确认网络尝试样本平均 ${((Number(audit.confirmed_requests) - 1) * 60000 / (end - begin)).toFixed(2)} 次 / 分钟 · 仅限所示样本` : `仅确认网络尝试 ${number(audit.confirmed_requests)} 次 · 未知项不能计为总速率`);
    const seconds = (value) => value == null || !Number.isFinite(Number(value)) ? "—" : `${Number(value).toFixed(2)} 秒`;
    const policy = objectValue(audit.config_policy);
    for (const [label, value] of [["当前配置间隔", config?.interval_seconds == null ? "—" : `${number(config.interval_seconds)} 秒`], ["审计下限", audit.interval_seconds == null ? "—" : `${number(audit.interval_seconds)} 秒`], ["确认网络尝试", number(audit.confirmed_requests)], ["可核对相邻请求", number(checked)], ["最小完成→下次开始", seconds(audit.min_finish_to_start_seconds)], ["已知低于下限", number(violations)], ["历史配置策略违规", number(policy.violations?.count)]]) facts.append(el("span", "", `${label} ${value}`));
    const classification = objectValue(audit.classification);
    for (const [key, label] of [["list_forward", "前进列表请求"], ["list_seek", "日期定位请求"], ["list_recovery", "校准列表请求"], ["detail", "详情请求"]]) {
      if (classification[key] != null) facts.append(el("span", "", `${label} ${number(classification[key])}`));
    }
    display("rate-uncertainty", `网络尝试标记未知 ${number(unknown.attempt_rows)} 行 · 时间未知 ${number(unknown.timing_rows)} 行 · 未完成 ${number(unknown.unfinished_rows)} 行 · 间隔未知 ${number(unknown.pairs)} 对 · 重叠 ${number(unknown.overlaps)} 对 · 时钟异常 ${number(unknown.clock_anomalies)} 项。以上未知项不能视为遵守间隔。历史配置策略未知 ${number(policy.unknown_pairs)} 对${policy.history_truncated ? "，配置历史也有未纳入部分" : ""}；审计按所示秒数核对，历史配置策略单独列出。`);
  }
  async function downloadPosts(scope, format) {
    const targetNode = scope === "fleet" ? "local" : selectedNode, targetName = nodeName(targetNode), selection = nodeEpoch;
    const key = downloadKey(scope, format, targetNode), url = downloadURL(scope, format, targetNode);
    if (downloadBusy.has(key)) return;
    if (!authenticated) { showAuth("导出需要有效的中央控制台会话，请重新登录。"); return; }
    downloadBusy.add(key); controls();
    const label = scope === "fleet" ? "主控中央合并" : `节点 ${targetName}（${targetNode}）`;
    const selectionNote = () => scope === "local" && selection !== nodeEpoch ? ` 本次导出属于点击时的${label}；当前查看 ${nodeName()}（${selectedNode}）。` : "";
    notice(`正在生成${label}的全部已采帖子导出文件，不会启动采集或同步。`);
    try {
      const response = await fetch(url, { credentials: "same-origin", cache: "no-store", headers: { Accept: format === "csv" ? "text/csv" : "application/x-ndjson" } });
      if (!response.ok) {
        let message = `导出失败，HTTP ${response.status}。`;
        if ((response.headers.get("content-type") || "").includes("application/json")) { const data = await response.json(); if (data?.error) message = textValue(data.error); }
        if (response.status === 404) message += scope === "fleet" ? " 请检查主控是否已更新到支持合并帖子导出的版本。" : ` 请检查所选节点 ${targetName}（${targetNode}）的下载接口是否已升级；不会改为导出主控本机。`;
        if (response.status === 401) showAuth("中央控制台会话已过期，请重新登录后导出。");
        throw new Error(message);
      }
      const disposition = response.headers.get("content-disposition") || "";
      if (!/^attachment(?:;|$)/i.test(disposition)) throw new Error("导出接口未返回文件附件，未保存错误响应。请检查服务或代理配置。");
      let filename = disposition.match(/filename="([^"]+)"/i)?.[1] || `collector-${scope}-posts.${format}`;
      filename = filename.split(/[\\/]/).at(-1).replace(/[\u0000-\u001f]/g, "_");
      if (scope === "local") {
        const prefix = `collector-node-${downloadNodeId(targetNode)}-`;
        if (!filename.startsWith(prefix)) filename = prefix + filename.replace(/^collector-local-/, "");
      }
      const blob = await response.blob(), objectURL = URL.createObjectURL(blob), link = document.createElement("a");
      link.href = objectURL; link.download = filename; document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(objectURL), 60000);
      const count = response.headers.get("x-collector-post-count"), snapshot = response.headers.get("x-export-snapshot-at");
      notice(`${label}帖子文件已交给浏览器下载${count == null ? "" : " · " + number(count) + " 条"}${snapshot ? " · 快照 " + time(snapshot) : ""}。${selectionNote()} 文件只包含已采记录，不代表覆盖完整。`);
    } catch (error) { if (authenticated) notice(`未下载${label}帖子：${error.message}${selectionNote()}`, true); else authMessage("会话已过期，请重新登录后导出。", true); }
    finally { downloadBusy.delete(key); controls(); }
  }
  function renderBlock(data) {
    const firstBlock = first(data, ["first_block_evidence", "first_block"]);
    const evidence = first(data, ["blocking_evidence", "block_evidence", "block"], firstBlock);
    const blocked = Boolean(data.active_halt) || (!independentStocks(data) && ["blocked", "error"].includes(data.state) && !data.storage_halt);
    const evidenceKind = first(objectValue(evidence), ["kind", "outcome", "type"]);
    const staleEvidence = Boolean(data.active_halt && evidenceKind && evidenceKind !== data.active_halt);
    const currentEvidence = staleEvidence ? null : evidence;
    const history = $("block-history"), hideHistory = independentStocks(data) || !evidence || (blocked && !staleEvidence);
    if (history.hidden || hideHistory) history.open = false;
    history.hidden = hideHistory;
    display("block-history-evidence", hideHistory ? "" : JSON.stringify(firstBlock ? { first_block: firstBlock, latest_block: evidence } : evidence, null, 2));
    $("block-panel").hidden = !blocked;
    if (!blocked) return;
    const info = objectValue(currentEvidence);
    const reason = first(info, ["reason", "error", "message"], data.reason || (data.state === "error" ? "当前异常尚未处理。" : "当前来源阻断尚未解除。"));
    const kind = first(info, ["outcome", "kind", "type"], data.active_halt || "待检查");
    display("block-title", independentStocks(data) ? "节点保护暂停，需检查原始证据" : data.state === "error" ? "采集已暂停，需检查异常" : "当前来源阻断尚未解除");
    display("block-kind", outcomes[kind] || kind);
    display("block-reason", reason);
    const facts = [
      ["阻断记录时间", time(first(info, ["at", "timestamp", "started_at", "started", "blocked_at", "time"]))],
      ["请求序号 / ID", first(info, ["attempt", "attempt_no", "request_id", "id", "sequence"], "—")],
      ["HTTP 状态", first(info, ["http_status", "status_code", "status"], "未知 / 无响应")],
    ];
    const list = $("block-facts"); list.replaceChildren();
    facts.forEach(([label, value]) => { const fact = el("div", "evidence-fact"); fact.append(el("span", "", label), el("strong", "", value)); list.append(fact); });
    display("block-evidence", JSON.stringify(currentEvidence || { state: data.state, active_halt: data.active_halt, reason: data.reason }, null, 2));
  }
  function renderCoverage(data, config) {
    let items = first(data, ["coverage", "stock_coverage", "per_stock"], []);
    if (!Array.isArray(items) && items && typeof items === "object") items = Object.entries(items).map(([stock, value]) => ({ stock, ...objectValue(value) }));
    if (!Array.isArray(items)) items = [];
    const list = $("coverage-list");
    const focused = document.activeElement;
    const existingPanels = new Map([...list.querySelectorAll("[data-task-proxy-key]")].filter((panel) => taskProxyDrafts.get(panel.dataset.taskProxyKey)?.dirty || panel.contains(focused)).map((panel) => [panel.dataset.taskProxyKey, panel]));
    const detachedRuntimes = Array.isArray(data.detached_stock_runtimes) ? data.detached_stock_runtimes : [];
    const activeProxyKeys = new Set([...items.filter((item) => item.runtime).map((item) => taskKey(first(item, ["stock", "stock_code", "bar_code", "code"]), item.runtime.job)), ...detachedRuntimes.map((runtime) => taskKey(runtime.stock, runtime.job))]);
    for (const key of taskProxyDrafts.keys()) if (!activeProxyKeys.has(key)) taskProxyDrafts.delete(key);
    list.replaceChildren();
    if (!items.length && config && Array.isArray(config.stocks)) items = config.stocks.map((stock) => ({ stock, status: "pending" }));
    display("coverage-count", items.length ? `${items.length} 只股票 · 覆盖未确认` : "未配置");
    display("stock-controls-note", independentStocks(data) ? "各股独立采集、暂停、探测和出口；一个股票被拦截不会暂停其他股票。节点仍串行请求，共用全局请求间隔。" : "所选节点尚不支持逐股状态与控制，请更新该节点和主控。卡片按钮不会回退为全局命令。");
    if (!items.length) list.append(el("div", "empty-state", "保存股票和日期窗口后，这里会显示覆盖进度。"));
    for (const item of items) {
      const stock = first(item, ["stock", "stock_code", "bar_code", "code"], "未知代码");
      const seek = windowSeekInfo(data, stock, item);
      const runtime = independentStocks(data) ? item.runtime : null;
      const state = runtime?.state || first(item, ["status", "state", "stop_reason"], Number(item.pages || item.list_pages) > 0 ? data.state === "running" ? "running" : "paused" : "pending");
      const complete = item.date_boundary_reached && item.details_complete && !item.gaps?.length;
      const gaps = first(item, ["gaps", "gap", "coverage_gap"]);
      const labels = { pending: "待观察", running: "进行中", active: "进行中", paused: "待继续", complete: "已发现项完成", completed: "已发现项完成", date_boundary_confirmed: "已到窗口边界", boundary_reached: "已到窗口边界", source_exhausted: "源数据到尾", gap: "存在缺口", blocked: "已拦截", exhausted: "源数据到尾" };
      const row = el("article", "coverage-item");
      const header = el("div", "coverage-item-header");
      const hasGap = Array.isArray(gaps) ? gaps.length > 0 : !!gaps;
      const actions = el("div", "coverage-item-actions");
      const runtimeLabel = runtime ? runtimeProbe(runtime) ? "探测已安排" : runtime.state === "running" && runtime.network_retry ? "等待网络重试" : states[runtime.state] || runtime.state : complete ? "已发现项完成" : hasGap ? "存在缺口" : item.date_boundary_reached ? "已到窗口边界" : labels[state] || state;
      actions.append(el("span", `tag${runtime?.active_halt || state === "error" ? " warning" : state === "completed" || (!runtime && complete) ? " success" : ""}`, runtimeLabel));
      if (/^\d{6}$/.test(String(stock))) for (const action of ["start", "pause", "retry"]) actions.append(stockButton(action, stock, runtime));
      if (config && config.stocks?.includes(String(stock)) && /^\d{6}$/.test(String(stock))) {
        const remove = el("button", "stock-remove", "移除");
        remove.type = "button"; remove.dataset.removeStock = String(stock);
        remove.setAttribute("aria-label", `移除股票 ${stock} 的后续采集`);
        remove.addEventListener("click", () => confirmDeletion(String(stock)));
        actions.append(remove);
      }
      header.append(el("strong", "", stock), actions);
      row.append(header);
      appendStockRuntime(row, runtime);
      const requested = objectValue(item.requested_window);
      const requestedFrom = requested.from_date || config?.from_date;
      const requestedTo = requested.to_date || config?.to_date;
      if (requestedFrom || requestedTo) row.append(el("p", "field-note", `目标窗口：${requestedFrom || "—"} 至 ${requestedTo || "—"}`));
      const windowScoped = Object.hasOwn(item, "post_time_range");
      const postRange = objectValue(item.post_time_range);
      row.append(el("div", "coverage-range", windowScoped ? postRange.earliest || postRange.latest ? `窗口内帖子：${dateOnly(postRange.earliest)} 至 ${dateOnly(postRange.latest)}` : "尚未取得窗口内帖子。" : "旧节点尚未提供窗口内帖子时间范围。"));
      const navigation = objectValue(item.navigation_time_range);
      const navigationEarliest = first(navigation, ["earliest"], windowScoped ? null : first(item, ["earliest_publish_time", "earliest_published_at", "earliest", "min_published_at", "oldest"]));
      const navigationLatest = first(navigation, ["latest"], windowScoped ? null : first(item, ["latest_publish_time", "latest_published_at", "latest", "max_published_at", "newest"]));
      if (navigationEarliest || navigationLatest) row.append(el("p", "stock-recovery", `${windowScoped ? "定位／列表观察" : "列表观察（旧节点口径）"}：${dateOnly(navigationEarliest)} 至 ${dateOnly(navigationLatest)} · 不代表采集覆盖`));
      const forward = objectValue(item.forward_time_range);
      if (forward.earliest || forward.latest) row.append(el("p", "stock-recovery", `顺序列表页观察：${dateOnly(forward.earliest)} 至 ${dateOnly(forward.latest)} · 页面可能跨越目标窗口`));
      if (seek) row.append(el("p", "stock-recovery", windowSeekDescription(seek, state)));
      const counters = el("div", "coverage-counts");
      const pages = first(item, ["list_pages", "pages", "pages_completed"]);
      const details = objectValue(item.details);
      const bodies = first(item, ["body_complete", "bodies_complete", "complete_posts"], first(details, ["complete"]));
      const required = first(details, ["required"], first(item, ["detail_required"]));
      const listOnly = first(details, ["list_only"], first(item, ["list_only"]));
      const posts = first(details, ["observed"], first(item, ["unique_posts", "posts", "total_posts"], required == null || listOnly == null ? null : Number(required) + Number(listOnly)));
      const pending = first(item, ["pending", "pending_details"], first(details, ["pending"]));
      const summary = el("div", "post-summary"); summary.append(el("strong", "", `${windowScoped ? "已发现窗口内帖子" : "帖子记录（旧节点口径）"} ${number(posts)}`));
      const subsets = el("div", "post-subset-counts"); subsets.append(el("span", "subset-prefix", "其中"));
      for (const [label, value] of [["已补详情", bodies], ["待补详情", pending], ["未触发补详情", listOnly]]) subsets.append(el("span", "", `${label} ${number(value)}`));
      summary.append(subsets, el("p", "post-record-note", windowScoped ? "含定位采样与顺序采集发现的窗口内帖子，不代表连续覆盖。每条帖子保留标题，详情补到同一条帖子。" : "旧节点统计尚未核对当前窗口。每条帖子保留标题，详情补到同一条帖子。")); row.append(summary);
      if (Number(item.excluded_posts) > 0) row.append(el("p", "field-note activity-detail", `另有 ${number(item.excluded_posts)} 条历史记录因不在当前窗口或发布时间无法核实，已从本任务统计排除。记录仍保留，节点导出范围不变。`));
      const counts = [["前进列表", pages], ["原始列表观察行", first(item, ["rows"])]];
      counts.forEach(([label, value]) => counters.append(el("span", "", `${label} ${number(value)}`)));
      row.append(counters);
      if (required != null && bodies != null && Number(required) > 0) {
        const bar = el("div", "coverage-progress");
        bar.setAttribute("role", "progressbar"); bar.setAttribute("aria-label", `${stock} 已补详情占已触发补详情帖子的比例`);
        const percent = Math.min(100, Math.max(0, Number(bodies) / Number(required) * 100));
        bar.setAttribute("aria-valuemin", "0"); bar.setAttribute("aria-valuemax", "100"); bar.setAttribute("aria-valuenow", percent.toFixed(0));
        const fill = el("span"); fill.style.width = `${percent}%`; bar.append(fill); row.append(bar);
      }
      const describeGap = (gap) => {
        if (!gap || typeof gap !== "object") return textValue(gap);
        if (gap.kind === "details_unavailable") return `${number(gap.count)} 篇已列出的详情不可用，仍有正文缺口。`;
        if (gap.kind === "source_exhausted_before_boundary") return `源数据已到尾，尚未到请求起点 ${gap.requested_from || "—"}；最早观察 ${dateOnly(gap.earliest_observed)}。`;
        return textValue(first(gap, ["message", "reason"], gap));
      };
      const gapText = Array.isArray(gaps) ? gaps.filter(Boolean).map(describeGap).join("；") : describeGap(gaps);
      const reason = first(item, ["reason", "message"]);
      if (gapText || reason || ["exhausted", "source_exhausted"].includes(state)) row.append(el("p", "coverage-gap", gapText || reason || "源数据已到尾。目标窗口是否有未覆盖历史，请核对保留证据。"));
      if (item.recovery) row.append(el("div", "stock-recovery", recoveryDescription(item.recovery)));
      if (runtime) appendTaskProxy(row, item, stock, runtime, existingPanels);
      list.append(row);
    }
    for (const runtime of detachedRuntimes) {
      const row = el("article", "coverage-item detached-stock-item"), header = el("div", "coverage-item-header"), actions = el("div", "coverage-item-actions");
      actions.append(el("span", "tag warning", runtimeProbe(runtime) ? "原目标探测已安排" : "保留原阻断"), stockButton("pause", runtime.stock, runtime, true), stockButton("retry", runtime.stock, runtime, true));
      header.append(el("strong", "", `${runtime.stock} · 原任务 #${runtime.job}`), actions); row.append(header); appendStockRuntime(row, runtime, true);
      appendTaskProxy(row, { task_proxy: runtime.task_proxy }, runtime.stock, runtime, existingPanels); list.append(row);
    }
    updateStockControls();
    if (focused?.isConnected && document.activeElement !== focused) focused.focus({ preventScroll: true });
  }
  function windowSeekInfo(data, stock, item) {
    if (item.window_seek && typeof item.window_seek === "object") return item.window_seek;
    const entries = data.window_seek;
    if (Array.isArray(entries)) return entries.find((entry) => String(entry?.stock) === String(stock)) || null;
    if (entries && typeof entries === "object") return entries[String(stock)] || (String(entries.stock) === String(stock) ? entries : null);
    return null;
  }
  function windowSeekDescription(info, state) {
    const complete = info.phase === "complete";
    const failed = info.phase === "error";
    const label = failed ? "结束日期定位异常，已暂停" : complete ? "结束日期定位已完成" : state === "running" ? "正在定位结束日期" : "结束日期定位待继续";
    const facts = [label + (info.target_time ? " " + dateOnly(info.target_time) : ""), `探测 ${number(info.probes)} 次`];
    if (complete && info.start_page != null) facts.push(`顺序采集入口：源页码 ${number(info.start_page)}`);
    else if (info.current_page != null) facts.push(`当前源页码 ${number(info.current_page)}`);
    const reasons = { initial: "新任务日期定位", historic_prefix_upgrade: "旧任务升级为日期定位", seek_error_recovery: "定位异常后重新检查", entry_shifted: "入口时间范围变化，重新定位", target_at_or_after_head: "首页已进入目标日期范围", target_in_observed_page: "已找到包含结束日期的页面", adjacent_observed_bounds: "已找到结束日期的相邻页面区间" };
    const reason = complete ? info.completion_reason : info.reason;
    if (failed && info.error) facts.push(textValue(info.error));
    else if (reason) facts.push(reasons[reason] || textValue(reason));
    return facts.join(" · ");
  }
  const recoveryPhases = { pending: "等待校准", scheduled: "等待校准", verify_frontier: "检查末次前进页", seek: "定位 ID 与时间区间", anchor: "检查原始源页码", probe: "检查原始源页码", backtrack: "向前校准", scan: "回扫局部区间", scanning: "回扫局部区间", verify: "核对发现项", reconciling: "核对发现项", complete: "本轮校准结束", completed: "本轮校准结束", done: "本轮校准结束", paused: "校准已暂停", blocked: "校准被拦截", error: "校准异常", idle: "尚未校准" };
  const recoveryReasons = { process_restart: "进程重启后检查列表位置", manual_resume: "暂停恢复后检查列表位置", config_updated: "配置修改后重新核对列表位置", config_changed: "配置修改后重新核对列表位置", details_completed_recheck: "详情取得后检查列表位置", list_delay_recheck: "距上次列表已达校准间隔", periodic_recheck: "定期检查列表位置", forward_no_progress: "前进列表没有新增 ID，重新定位", source_count_decrease: "来源计数下降，核对局部区间", forward_time_shift: "前进页时间发生偏移，核对局部区间", nonstandard_page_recheck: "页面锚点不能直接确认，核对局部区间", terminal_recheck: "到达日期或来源尾页边界，核对局部区间", source_tail_recheck: "核对来源尾页", date_boundary_confirmed: "已到请求日期边界，核对局部区间", source_exhausted: "来源列表到尾，核对局部区间" };
  function recoveryProof(info) {
    if (info.time_fallback || info.proof_level === "time_boundary_with_gap") return "旧 ID 不可见，仅按时间回扫，缺口保留。";
    if (info.time_order_verified === false || info.proof_level === "id_interval_time_order_unverified") return "发布时间次序尚未核实，局部覆盖不能确认。";
    if (info.proof_level === "two_matching_anchor_interval_observations") return "已观察的 ID 与时间局部区间两轮一致；整体覆盖仍未确认。";
    if (info.proof_level === "last_forward_page_stable") return "末次前进页的 ID 与发布时间一致、来源计数未下降；仅确认导航锚点稳定，窗口覆盖仍未确认。";
    return "";
  }
  function recoveryUsage(info) {
    const facts = [];
    if (info.validated_requests != null) facts.push(`有效校准响应 ${number(info.validated_requests)}${info.max_requests == null ? "" : " / " + number(info.max_requests)}`);
    if (info.completed_passes != null) facts.push(`已完成扫描 ${number(info.completed_passes)}${info.max_passes == null ? " 轮" : " / " + number(info.max_passes) + " 轮"}`);
    return facts;
  }
  function recoveryStrategy(info) {
    return { stable_frontier: "末页单次检查", two_pass: "区间两轮校准" }[info.strategy] || "";
  }
  function recoveryDescription(recovery) {
    const info = objectValue(recovery);
    const phase = first(info, ["phase"], "pending");
    const facts = [recoveryPhases[phase] || phase, recoveryStrategy(info), info.anchor_page == null ? "" : `原始源页码 ${number(info.anchor_page)}`, info.current_page == null ? "" : `当前源页码 ${number(info.current_page)}`, info.passes == null ? "" : `当前校准轮次 ${number(info.passes)}`, ...recoveryUsage(info), info.new_posts == null ? "" : `新增发现 ${number(info.new_posts)} 帖`].filter(Boolean);
    const trigger = first(info, ["trigger_reason", "reason"]);
    if (trigger) facts.push("触发原因：" + (recoveryReasons[trigger] || textValue(trigger)));
    if (info.fallback_reason) facts.push("转为区间校准：" + textValue(info.fallback_reason));
    if (Number(info.resume_count) > 0) facts.push(`本轮按保存断点恢复 ${number(info.resume_count)} 次`);
    if (recoveryProof(info)) facts.push(recoveryProof(info));
    return facts.join(" · ");
  }
  function renderRecovery(data) {
    let info = first(data, ["recovery", "current_recovery"]);
    if (Array.isArray(info)) info = info.find((entry) => entry.stock === data.current?.stock) || info[0];
    if (info && typeof info === "object" && !info.phase && !Object.hasOwn(info, "anchor_page")) {
      const values = Object.entries(info).filter(([, value]) => value && typeof value === "object");
      info = info[data.current?.stock] || values[0]?.[1];
    }
    $("recovery-panel").hidden = false;
    const facts = $("recovery-facts"); facts.replaceChildren();
    if (!info || typeof info !== "object") {
      display("recovery-phase", "尚未校准");
      display("recovery-reason", "尚无分页恢复校准记录。");
      return;
    }
    const phase = first(info, ["phase"], "pending");
    display("recovery-phase", recoveryPhases[phase] || phase);
    const strategy = recoveryStrategy(info);
    if (strategy) facts.append(el("span", "", `校准方式 ${strategy}`));
    const values = [["股票", first(info, ["stock", "stock_code"], data.current?.stock)], ["原始源页码", info.anchor_page], ["当前源页码", info.current_page], ["当前校准轮次", info.passes], ["偏移观察", info.drift_count], ["新增发现帖", info.new_posts]];
    values.forEach(([label, value]) => { if (value != null) facts.append(el("span", "", `${label} ${label === "股票" ? textValue(value) : number(value)}`)); });
    recoveryUsage(info).forEach((value) => facts.append(el("span", "", value)));
    const trigger = first(info, ["trigger_reason", "reason"]);
    display("recovery-reason", [trigger ? "触发原因：" + (recoveryReasons[trigger] || textValue(trigger)) : "校准进度由服务端保存。暂停、编辑和刷新不会自动开始采集。", info.fallback_reason ? "转为区间校准：" + textValue(info.fallback_reason) : "", recoveryProof(info)].filter(Boolean).join(" · "));
  }
  function renderJobs(data) {
    const items = unpack(data, "jobs");
    const list = $("history-list"); list.replaceChildren();
    const archived = items.filter((job) => job.status === "archived" || job.archived_at || job.archived).length;
    display("history-count", `${items.length} 个任务 · ${archived} 已归档`);
    if (!items.length) { list.append(el("div", "empty-state", "尚无任务记录。")); return; }
    for (const job of items) {
      const config = objectValue(job.config);
      const archive = job.status === "archived" || job.archived_at || job.archived;
      const current = !archive && (job.current === true || job.status === "active" || String(job.id) === String(status?.job?.id));
      const item = el("article", "history-item");
      const head = el("div", "history-item-head");
      head.append(el("strong", "", `任务 #${job.id == null ? "—" : textValue(job.id)}${job.revision == null ? "" : " · 配置版本 " + number(job.revision)}`), el("span", `tag${current ? " success" : ""}`, archive ? "已归档" : current ? "当前任务" : "已保留"));
      item.append(head);
      const stocks = Array.isArray(config.stocks) ? config.stocks.join("，") : "—";
      item.append(el("p", "", `${stocks} · ${config.from_date || "—"} 至 ${config.to_date || "—"}`));
      item.append(el("p", "history-times", `创建 ${time(first(job, ["created_at", "created"]))}${archive ? " · 归档 " + time(first(job, ["archived_at", "archived"])) : ""} · ${number(config.interval_seconds)} 秒 / 次 · ${config.client || "—"}`));
      const reason = first(job, ["archive_reason", "archived_reason"]);
      if (reason) item.append(el("p", "", reason));
      list.append(item);
    }
  }
  function renderStorage(data) {
    const storage = objectValue(data.data_storage);
    $("storage-panel").hidden = !data.data_storage && !data.storage_halt;
    if ($("storage-panel").hidden) { $("storage-error").hidden = true; return; }
    const failed = !!data.storage_halt || storage.status === "error";
    display("storage-state", failed ? "写入异常 · 已暂停" : storage.status === "ready" ? "可用" : "等待状态");
    $("storage-state").className = `tag ${failed ? "warning" : "success"}`;
    display("storage-path", storage.db_path || "采集数据库路径尚未返回");
    display("storage-path-full", storage.db_path || "采集数据库路径尚未返回");
    $("storage-path").title = storage.db_path || "采集数据库路径尚未返回";
    display("storage-note", storage.single_runtime_database === true || /^unified\./.test(storage.storage_layout || "") ? "本实例使用一份 collector.db 保存现有 posts / backfill 格式、请求记录和任务状态。采集数据的覆盖仍需核实，不会自动进入生产模型。" : "该实例尚未返回单库布局标记。升级并迁移后可将帖子、请求记录和任务状态统一保存到 collector.db；现有采集数据的覆盖仍需核实，不会自动进入生产模型。");
    display("storage-sync", `最近本地同步 ${time(storage.last_synced_at)}${storage.storage_version ? " · 格式 " + textValue(storage.storage_version) : ""}`);
    display("storage-sync-brief", `同步 ${time(storage.last_synced_at, false)}`);
    $("storage-sync-brief").title = $("storage-sync").textContent;
    const error = first(storage, ["last_error"], data.storage_halt);
    $("storage-error").hidden = !error;
    display("storage-error", error ? `${textValue(error)}。已取得的原始记录保留，重试本地写入不会重新请求来源。` : "");
  }
  function unpack(data, key) {
    if (Array.isArray(data)) return data;
    return Array.isArray(data && data[key]) ? data[key] : Array.isArray(data && data.items) ? data.items : [];
  }
  function resultClass(outcome) {
    if (/block|challenge|rate|429|403|removed|delete|unavailable|redirect/i.test(outcome)) return "warning";
    if (/error|fail|unknown|partial/i.test(outcome)) return "error";
    if (/success|^ok$|_ok$|complete|^real_data$/i.test(outcome)) return "success";
    return "";
  }
  function activityUrl(kind, cursor = null, snapshot = null) {
    let path = `${kind}?paged=1&limit=${activityPages[kind].limit}`;
    if (cursor != null) path += `&before_id=${encodeURIComponent(cursor)}`;
    if (snapshot > 0) path += `&snapshot_id=${encodeURIComponent(snapshot)}`;
    return path;
  }
  function updatePager(kind) {
    const state = activityPages[kind];
    const locked = busy || !authenticated || !online || state.loading;
    $(`${kind}-prev`).disabled = locked || state.index === 0;
    $(`${kind}-next`).disabled = locked || !state.page?.has_more;
    $(`${kind}-latest`).disabled = locked || state.latest;
    const total = state.page?.total;
    display(`${kind}-page-info`, state.loading ? "正在读取这一页…" : state.page ? `${state.latest ? "最新记录" : "历史快照"} · 第 ${state.index + 1} 页 / 共 ${Math.max(1, Math.ceil(Number(total) / state.limit))} 页 · ${number(total)} 条${state.latest ? " · 自动刷新" : " · 保持当前页"}` : "正在读取记录…");
  }
  function activityError(kind, message = "") {
    $(`${kind}-page-error`).hidden = !message;
    $(`${kind}-page-error`).textContent = message;
  }
  function acceptActivity(kind, page, latest = activityPages[kind].latest, index = activityPages[kind].index, cursors = activityPages[kind].cursors) {
    if (!page || !Array.isArray(page.items) || typeof page.has_more !== "boolean" || !Number.isInteger(page.total)) throw new Error("运行记录分页接口尚未就绪，请检查服务版本。");
    const state = activityPages[kind];
    state.page = page; state.latest = latest; state.index = index; state.snapshot = page.snapshot_id; state.cursors = cursors;
    if (kind === "requests") {
      if (latest) latestRequestItems = page.items;
      renderRequests(page);
    } else renderEvents(page);
    activityError(kind); updatePager(kind);
  }
  async function loadActivity(kind, action) {
    const state = activityPages[kind];
    if (!authenticated || !online || busy || state.loading) return;
    let index = state.index, cursor = null, snapshot = state.snapshot, latest = false, cursors = [...state.cursors];
    if (action === "next") {
      if (!state.page?.has_more) return;
      index += 1; cursor = state.page.next_cursor; cursors[index] = cursor;
    } else if (action === "prev") {
      if (index === 0) return;
      index -= 1; cursor = cursors[index];
    } else if (action === "latest") {
      index = 0; snapshot = null; latest = true; cursors = [null];
    } else return;
    const generation = ++state.generation;
    const selection = nodeEpoch, targetNode = selectedNode;
    state.loading = true; activityError(kind); updatePager(kind);
    try {
      const page = await api(activityUrl(kind, cursor, snapshot), "GET", undefined, targetNode);
      if (authenticated && selection === nodeEpoch && generation === state.generation) acceptActivity(kind, page, latest, index, cursors);
    } catch (error) {
      if (authenticated && selection === nodeEpoch && generation === state.generation) activityError(kind, `无法读取该页：${error.message}。原有记录保留，可重试翻页。`);
    } finally { if (selection === nodeEpoch && generation === state.generation) { state.loading = false; updatePager(kind); } }
  }
  async function autoActivity(kind) {
    const state = activityPages[kind];
    if (!state.latest || state.loading) return null;
    const generation = state.generation;
    const page = await api(activityUrl(kind));
    return { page, generation };
  }
  function renderRequests(data) {
    requestItems = unpack(data, "requests");
    display("request-count", data.total == null ? requestItems.length : number(data.total));
    const list = $("request-list"); list.replaceChildren();
    if (!requestItems.length) { list.append(el("div", "empty-state", "尚无源请求记录。")); return; }
    for (const request of requestItems) {
      const target = objectValue(request.target);
      const kind = first(request, ["kind", "type"], first(target, ["kind", "type"]));
      const stock = first(request, ["stock", "stock_code", "bar_code"], first(target, ["stock", "stock_code"], ""));
      const id = first(request, ["post_id", "source_item_id"], first(target, ["post_id", "source_item_id"]));
      const page = first(request, ["page", "page_number"], first(target, ["page"]));
      const attempt = first(request, ["id", "attempt_no", "sequence", "attempt"], "—");
      const outcome = textValue(first(request, ["outcome", "result", "state"], "unknown"));
      const http = first(request, ["http_status", "status_code", "status"]);
      const purpose = first(request, ["purpose"], first(target, ["purpose"], first(request.analysis, ["purpose"])));
      const title = `#${attempt} · ${stock ? stock + " · " : ""}${kind === "detail" || id != null ? "正文 " + (id || "") : (purpose === "seek" ? "日期定位列表" : purpose === "recovery" ? "校准列表" : "前进列表") + (page == null ? "" : " · 源页码 " + page)}`;
      const row = el("div", "activity-row");
      row.append(el("div", "activity-time", time(first(request, ["started_at", "requested_at", "at", "timestamp"]))));
      const main = el("div", "activity-main"); main.append(el("div", "activity-title", title));
      const reason = first(request, ["reason", "error", "message"]);
      const elapsed = first(request, ["elapsed_seconds", "duration_seconds", "duration"], epoch(request.finished_at) != null && epoch(request.started_at) != null ? (epoch(request.finished_at) - epoch(request.started_at)) / 1000 : null);
      const bytes = first(request, ["bytes", "body_bytes", "response_bytes", "size"]);
      const details = [reason ? textValue(reason) : "", elapsed == null ? "" : `耗时 ${Number(elapsed).toFixed(2)} 秒`, bytes == null ? "" : `${number(bytes)} 字节`].filter(Boolean).join(" · ");
      if (details) main.append(el("div", "activity-detail", details));
      const url = first(request, ["url", "requested_url"], target.url);
      if (url) main.append(el("div", "activity-url", url));
      const profile = objectValue(request.analysis);
      const headers = objectValue(profile.request_headers);
      if (headers["User-Agent"] || headers.Referer) {
        const sources = { google_search: "Google 搜索来源", baidu_search: "百度搜索来源", list_previous_page: "上一列表页", detail_observed_list: "该帖子最近观察到的列表页", detail_task_list_page: "详情任务关联的列表页" };
        const headerDetails = el("details", "activity-detail");
        headerDetails.append(el("summary", "", `请求特征 · ${sources[profile.referer_source] || profile.request_profile || "已记录"}`));
        if (headers.Referer) headerDetails.append(el("div", "activity-url", `Referer: ${headers.Referer}`));
        if (headers["User-Agent"]) headerDetails.append(el("div", "activity-url", `UA: ${headers["User-Agent"]}`));
        if (profile.referer_list_request_id != null) headerDetails.append(el("div", "activity-detail", `关联列表请求 #${profile.referer_list_request_id}`));
        main.append(headerDetails);
      }
      row.append(main, el("div", `activity-result ${resultClass(outcome)}`, `${outcomes[outcome] || outcome}${http == null ? "" : " · HTTP " + http}`));
      list.append(row);
    }
  }
  function renderEvents(data) {
    const items = unpack(data, "events"); display("event-count", data.total == null ? items.length : number(data.total));
    const list = $("event-list"); list.replaceChildren();
    if (!items.length) { list.append(el("div", "empty-state", "尚无控制事件。")); return; }
    const eventLabels = { job_created: "已保存任务配置", started: "开始采集", start: "开始采集", paused: "任务已暂停", pause: "任务已暂停", blocked: "访问拦截，自动暂停", error: "异常，自动暂停", network_retry_scheduled: "网络异常，等待退避重试", network_retry_cleared_legacy: "旧传输异常已转为待重试，保持暂停", network_probe_failed: "单次探测网络失败，未安排自动重试", window_seek_started: "开始日期定位", window_seek_progress: "日期定位继续探测", window_seek_completed: "日期定位完成", window_seek_upgraded: "旧任务升级为日期定位", window_seek_observed: "日期定位页面已观察", retry: "已安排单次探测", probe_scheduled: "已安排单次探测", retry_scheduled: "已安排单次探测", probe_success: "探测成功，保持暂停", completed: "任务结束", recovered: "重启恢复，保持暂停" };
    for (const item of items) {
      const kind = textValue(first(item, ["kind", "type", "event", "action"], "event"));
      const row = el("div", "activity-row"); row.append(el("div", "activity-time", time(first(item, ["at", "timestamp", "created_at", "time"]))));
      const main = el("div", "activity-main");
      const title = first(item, ["message", "reason"], eventLabels[kind] || kind);
      main.append(el("div", "activity-title", title));
      const detail = first(item, ["details", "data", "payload", "evidence"]);
      if (detail) main.append(el("div", "activity-detail", textValue(detail)));
      row.append(main, el("div", `activity-result ${resultClass(kind)}`, eventLabels[kind] ? "" : kind)); list.append(row);
    }
  }
  async function poll() {
    if (!authenticated || polling || busy) return;
    const generation = statusGeneration;
    const ticket = ++pollSerial;
    polling = true; controls();
    try {
      const results = await Promise.allSettled([api("status"), autoActivity("requests"), autoActivity("events"), api("jobs")]);
      if (!authenticated || generation !== statusGeneration) return;
      const [s, requests, events, jobs] = results;
      if (s.status === "rejected") { setConnection(false); notice(`无法读取 ${nodeName()} 的状态：${s.reason.message}。其他实例仍可查看。`, true); return; }
      for (const [kind, result] of [["requests", requests], ["events", events]]) {
        if (result.status === "fulfilled" && result.value && activityPages[kind].latest && result.value.generation === activityPages[kind].generation) acceptActivity(kind, result.value.page, true, 0, [null]);
        else if (result.status === "rejected") activityError(kind, `最新记录暂时无法读取：${result.reason.message}`);
      }
      lastPoll = Date.now();
      renderStatus(s.value);
      if (jobs.status === "fulfilled") renderJobs(jobs.value);
      setConnection(true);
      if (requests.status === "rejected" || events.status === "rejected" || jobs.status === "rejected") notice("状态已更新，但部分运行记录或任务历史暂时无法读取。", true);
      else if ($("notice").dataset.networkError === "true") { notice(""); delete $("notice").dataset.networkError; }
    } catch (error) {
      if (authenticated && generation === statusGeneration) { setConnection(false); notice(`所选实例连接失败：${error.message}`, true); }
    } finally { if (ticket === pollSerial) { if (!online) $("notice").dataset.networkError = "true"; polling = false; controls(); } }
  }
  function startTimers() {
    stopTimers();
    timer = setInterval(() => { if (!document.hidden) void poll(); }, 5000);
    tickTimer = setInterval(renderTiming, 1000);
    fleetTimer = setInterval(() => { if (!document.hidden) void refreshFleet(); }, 10000);
    void refreshFleet();
  }
  function stopTimers() { clearInterval(timer); clearInterval(tickTimer); clearInterval(fleetTimer); timer = null; tickTimer = null; fleetTimer = null; }
  function applyStatus(result) {
    if (result && result.state) { lastPoll = Date.now(); renderStatus(result); setConnection(true); }
  }
  async function pauseAndWait(expectedJobId = null, targetNode = selectedNode, selection = nodeEpoch) {
    let snapshot = await api("status", "GET", undefined, targetNode);
    if (selection !== nodeEpoch) throw new Error("查看实例已变化，原操作已停止。");
    if (expectedJobId != null && String(snapshot.job?.id) !== String(expectedJobId)) throw new Error("当前任务已变化，请刷新配置后重新操作。");
    applyStatus(snapshot);
    const waitingAutoRecovery = ["blocked", "error"].includes(snapshot.state) && snapshot.proxy?.settings?.auto_recover === true && snapshot.proxy?.auto_suspended === false;
    if (snapshot.state === "running" || snapshot.probe_pending || snapshot.request_inflight || waitingAutoRecovery) {
      notice("正在暂停采集并等待当前请求结束；已有响应将保留，之后不会自动开始。");
      snapshot = await api("control", "POST", { action: "pause" }, targetNode);
      applyStatus(snapshot);
    }
    const deadline = Date.now() + 45000;
    while (snapshot.state === "running" || snapshot.probe_pending || snapshot.request_inflight) {
      if (Date.now() >= deadline) throw new Error("暂停已提交，但当前请求尚未结束。请等待状态更新后重试；没有修改配置或删除任务。");
      await new Promise((resolve) => setTimeout(resolve, 750));
      snapshot = await api("status", "GET", undefined, targetNode);
      if (selection !== nodeEpoch) throw new Error("查看实例已变化，原操作已停止。");
      if (expectedJobId != null && String(snapshot.job?.id) !== String(expectedJobId)) throw new Error("等待期间当前任务已变化，请刷新后重新操作。");
      applyStatus(snapshot);
    }
    return snapshot;
  }
  async function post(path, payload, message, method = "POST", options = {}) {
    if (busy || !authenticated || !online) return false;
    const targetNode = selectedNode, selection = nodeEpoch;
    busy = true; statusGeneration += 1; controls(); notice(""); delete $("notice").dataset.networkError;
    let success = false;
    try {
      if (options.pauseFirst) await pauseAndWait(options.expectedJobId, targetNode, selection);
      if (selection !== nodeEpoch) throw new Error("查看实例已变化，尚未提交修改。");
      const result = await api(path, method, payload, targetNode);
      if (authenticated && selection === nodeEpoch) {
        if (options.clearDraft) { formDirty = false; loadedConfig = false; }
        if (options.clearForm || (options.clearDraft && result && !result.job && !result.config)) clearForm();
        if (options.clearProxyDraft) { proxyDirty = false; clearProxySecrets(); proxyError(); }
        applyStatus(result);
        notice(message); success = true;
      }
    } catch (error) {
      if (authenticated) {
        const message = options.proxyOperation ? safeProxyMessage(error.message, options.sensitiveValues || []) : error.message;
        notice(message, true); if (options.proxyOperation) proxyError(message);
      }
    } finally { if (options.proxyOperation) clearProxySecrets(); busy = false; controls(); if (authenticated) await poll(); }
    return success;
  }
  async function prepareEdit() {
    if (busy || !authenticated || !online || !jobConfig()) return;
    busy = true; statusGeneration += 1; controls();
    try {
      await pauseAndWait(status?.job?.id, selectedNode, nodeEpoch);
      notice(status?.active_halt ? "已停止请求，可以编辑配置。当前阻断原因仍保留，保存不会自动开始。" : "已暂停并等待当前请求结束，可以编辑配置。保存后由你决定何时开始。");
    } catch (error) { if (authenticated) notice(error.message, true); }
    finally {
      busy = false; controls();
      if (!$("config-fields").disabled) { $("config-form").scrollIntoView({ behavior: "smooth", block: "center" }); $("stocks").focus({ preventScroll: true }); }
      if (authenticated) await poll();
    }
  }
  function confirmDeletion(stock = null) {
    if (busy || !online || !jobConfig()) return;
    const stocks = jobConfig().stocks || [];
    const last = stock != null && stocks.length === 1;
    pendingDeletion = { stock, last, jobId: status?.job?.id, selection: nodeEpoch };
    display("delete-title", stock ? `移除股票 ${stock}？` : "删除当前任务？");
    display("delete-description", stock ? `取消 ${stock} 的后续采集，保留已经取得的记录。${last ? "这是当前任务的最后一只股票，移除后任务将归档。" : "其他股票的队列保留，任务保持暂停。"}` : "当前任务将归档，取消尚未执行的采集队列。");
    display("confirm-delete", stock ? "确认移除股票" : "确认删除任务");
    $("delete-dialog").showModal();
  }
  $("auth-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const token = $("token").value;
    if (!token || $("login").disabled) return;
    $("login").disabled = true; authMessage("正在建立会话…");
    try {
      const session = await api("session", "POST", { token });
      if (session.authenticated === false) throw new Error("密钥无效，未建立会话。");
      $("token").value = ""; showConsole(); await poll();
    } catch (error) { authMessage(error.message, true); }
    finally { $("login").disabled = false; }
  });
  $("logout").addEventListener("click", async () => {
    if (busy) return;
    busy = true; controls();
    try { await api("session", "DELETE"); showAuth("已退出控制台。服务端采集状态保持不变。"); }
    catch (error) { if (authenticated) notice(error.message, true); }
    finally { busy = false; controls(); }
  });
  function markDirty() { formDirty = true; display("config-state", "未保存"); controls(); }
  function markProxyDirty() { proxyDirty = true; display("proxy-config-state", "页面修改尚未保存"); proxyError(); controls(); }
  function proxyInteger(id, label, minimum = 0, required = false) {
    const raw = $(id).value.trim();
    if (!raw && !required) return undefined;
    if (!/^\d+$/.test(raw) || !Number.isSafeInteger(Number(raw)) || Number(raw) < minimum) throw new Error(`${label}必须是至少 ${minimum} 的整数。`);
    return Number(raw);
  }
  function proxyPayload() {
    const mode = $("proxy-mode").value;
    if (!Object.hasOwn(proxyModes, mode)) throw new Error("请选择支持的出站方式。");
    const payload = { mode, auto_recover: dynamicProxyMode(mode) && $("proxy-auto-recover").checked };
    if (mode === "http") {
      const endpoint = $("proxy-endpoint").value.trim(); let url;
      try { url = new URL(endpoint); } catch { throw new Error("请填写所选节点可访问的 HTTP 代理端点（http://）。"); }
      if (url.protocol !== "http:" || url.username || url.password || url.search || url.hash || !["", "/"].includes(url.pathname)) throw new Error("代理端点应为 HTTP 地址（http://），不带路径、查询、用户名或密码；HTTPS 加密代理端点和 SOCKS 端口不适用，认证请在下方填写。");
      payload.endpoint = endpoint;
    }
    if (dynamicProxyMode(mode)) {
      const seconds = proxyInteger("proxy-rotate-seconds", "按使用时间更换的秒数");
      if (seconds !== undefined && seconds > 0 && seconds < 60) throw new Error("按使用时间更换须为 0（关闭）或至少 60 秒。");
      if (seconds !== undefined) payload.rotate_seconds = seconds;
      const requests = proxyInteger("proxy-rotate-requests", "按来源尝试次数更换");
      if (requests !== undefined) payload.rotate_requests = requests;
    }
    if (dynamicProxyMode(mode)) {
      payload.daily_limit = proxyInteger("proxy-daily-limit", "每天最多提取次数", 1, true);
      const apiURL = $("proxy-api-url").value.trim();
      if ($("proxy-clear-api").checked) throw new Error("动态 IP 模式需要提取 API。要移除已有 API，请先切换为直连或 HTTP 代理再保存。");
      if (apiURL) {
        let url; try { url = new URL(apiURL); } catch { throw new Error("请填写供应商生成的有效 HTTP(S) 提取 API URL。"); }
        if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.hash) throw new Error("提取 API 必须是有效的 HTTP(S) URL，不带地址认证或片段。");
        payload.api_url = apiURL;
      } else if (first(proxyState, ["has_api_url"], proxyState?.settings?.has_api_url) !== true) throw new Error("首次使用动态 IP 需要填写提取 API URL；已有配置才可以留空保留。");
      else if (dynamicProxyMode(first(proxyState?.settings, ["mode"], proxyState?.mode)) && mode !== first(proxyState?.settings, ["mode"], proxyState?.mode)) throw new Error("切换动态 IP 供应商时，请填写新供应商生成的提取 API URL。");
      if (payload.auto_recover) {
        payload.recovery_cooldown_seconds = proxyInteger("proxy-recovery-cooldown", "恢复冷却时间", 60, true);
        payload.recovery_max_attempts = proxyInteger("proxy-recovery-max", "最大连续恢复次数", 1, true);
      }
    }
    if ($("proxy-clear-auth").checked) payload.clear_auth = true;
    else {
      if (first(proxyState, ["has_auth"], proxyState?.settings?.has_auth) !== true && !!$("proxy-username").value !== !!$("proxy-password").value) throw new Error("首次配置代理认证需要同时填写用户名与密码；已有认证的空值会保留。");
      if ($("proxy-username").value) payload.username = $("proxy-username").value;
      if ($("proxy-password").value) payload.password = $("proxy-password").value;
    }
    if ($("proxy-clear-api").checked) payload.clear_api_url = true;
    return payload;
  }
  $("proxy-panel").addEventListener("toggle", () => { if ($("proxy-panel").open) void readProxy(); });
  $("proxy-refresh").addEventListener("click", () => { void readProxy(); });
  $("proxy-form").addEventListener("input", markProxyDirty);
  $("proxy-form").addEventListener("change", markProxyDirty);
  $("proxy-clear-auth").addEventListener("change", () => { if ($("proxy-clear-auth").checked) { $("proxy-username").value = ""; $("proxy-password").value = ""; } proxyVisibility(); });
  $("proxy-clear-api").addEventListener("change", () => { if ($("proxy-clear-api").checked) $("proxy-api-url").value = ""; proxyVisibility(); });
  $("proxy-reset").addEventListener("click", () => {
    if (busy || !proxyState) return;
    proxyDirty = false; clearProxySecrets(); loadProxyForm(proxyState); display("proxy-config-state", "已读取保存配置"); proxyError(); controls();
    notice("已撤销页面中尚未保存的代理修改，恢复当前节点的保存配置。");
  });
  $("proxy-form").addEventListener("submit", async (event) => {
    event.preventDefault(); if ($("proxy-save").disabled) return;
    let payload; try { payload = proxyPayload(); } catch (error) { proxyError(error.message); return; }
    const name = nodeName();
    await post("proxy/config", payload, `${name} 的代理配置已保存。采集与自动恢复仍暂停；已有阻断、冷却和证据保留，点击开始或继续才运行。`, "POST", { pauseFirst: true, clearProxyDraft: true, proxyOperation: true, sensitiveValues: [payload.username, payload.password, payload.api_url] });
  });
  $("proxy-rotate").addEventListener("click", () => {
    if ($("proxy-rotate").disabled) return;
    const name = nodeName(), blocked = ["blocked", "error"].includes(status?.state);
    void post("proxy/rotate", {}, blocked ? `${name} 已安排一次按间隔与冷却执行的探测；更换操作本身不请求来源，成功后仍暂停，继续采集须手动开始。` : `${name} 已安排在下一次来源尝试时重选出站；此操作不会单独发送探测，不改变当前请求或采集间隔。暂停中的任务仍需手动开始。`, "POST", { proxyOperation: true });
  });
  $("config-form").addEventListener("input", markDirty);
  document.querySelectorAll("[data-years]").forEach((button) => button.addEventListener("click", () => { defaultDates(Number(button.dataset.years)); markDirty(); }));
  $("config-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if ($("save-config").disabled) return;
    const stocks = [...new Set($("stocks").value.trim().split(/[\s,，、;；]+/).filter(Boolean))];
    const interval = Number($("interval").value);
    const from = $("from-date").value;
    const to = $("to-date").value;
    if (!stocks.length || stocks.some((stock) => !/^\d{6}$/.test(stock))) { notice("请填写 6 位股票代码，用逗号、空格或换行分隔。", true); $("stocks").focus(); return; }
    if (!from || !to || from > to) { notice("开始日期不能晚于结束日期。日期以 Asia/Shanghai 为准。", true); $("from-date").focus(); return; }
    if (!$("interval").value.trim() || !Number.isFinite(interval) || interval < 0) { notice("请填写非负的请求间隔秒数，支持小数；0 表示不额外等待。", true); $("interval").focus(); return; }
    const editing = !!jobConfig();
    const saved = await post(editing ? "jobs/current" : "jobs", { stocks, from_date: from, to_date: to, interval_seconds: interval, client: $("client").value }, editing ? "当前任务修改已保存，已有数据和响应保留。任务保持暂停或原有阻断状态，尚未重新开始采集。" : "任务已创建，尚未发起源请求。点击开始采集后才会调度。", editing ? "PATCH" : "POST", { pauseFirst: editing, expectedJobId: editing ? status?.job?.id : null, clearDraft: true });
    if (saved && status && jobConfig()) { formDirty = false; loadedConfig = false; loadForm(jobConfig()); controls(); }
  });
  $("pause-edit").addEventListener("click", () => { void prepareEdit(); });
  $("reset-config").addEventListener("click", () => { formDirty = false; loadedConfig = false; loadForm(jobConfig()); controls(); notice("已撤销页面中尚未保存的修改，恢复当前任务配置。"); });
  $("delete-job").addEventListener("click", () => confirmDeletion());
  $("delete-dialog").addEventListener("close", () => {
    const target = pendingDeletion; pendingDeletion = null;
    if ($("delete-dialog").returnValue !== "confirm" || !target || target.selection !== nodeEpoch) return;
    const path = target.stock ? `jobs/current/stocks/${encodeURIComponent(target.stock)}` : "jobs/current";
    const message = target.stock && !target.last ? `已移除 ${target.stock} 的后续采集。已有记录保留，其他股票保持暂停；现有阻断原因也保留。` : "任务已归档，后续采集已取消。已有帖子、原始响应和阻断证据保留。";
    void post(path, undefined, message, "DELETE", { pauseFirst: true, expectedJobId: target.jobId, clearDraft: true, clearForm: !target.stock || target.last });
  });
  $("start").addEventListener("click", () => { void post("control", { action: "start" }, "已提交开始指令；源请求由服务端按全局间隔安排。"); });
  $("pause").addEventListener("click", () => { void post("control", { action: "pause" }, "已提交暂停指令，队列与响应证据会保留。"); });
  $("retry").addEventListener("click", () => {
    if (status?.storage_halt) { void post("control", { action: "retry" }, "已提交采集数据库写入重试，仅处理本地记录。检查状态后，仍需手动开始源采集。"); return; }
    retryNodeEpoch = nodeEpoch; renderTiming(); $("retry-dialog").showModal();
  });
  $("retry-dialog").addEventListener("close", () => { if ($("retry-dialog").returnValue === "confirm" && retryNodeEpoch === nodeEpoch) void post("control", { action: "retry" }, "已安排一次探测。发送时间遵守间隔与冷却期，成功后仍暂停。"); });
  $("download-mihomo").addEventListener("click", () => { void downloadMihomo(); });
  $("selected-node").addEventListener("change", () => requestSelection($("selected-node").value));
  $("switch-node-dialog").addEventListener("close", () => { const target = pendingSelection; pendingSelection = null; if ($("switch-node-dialog").returnValue === "confirm" && target) void switchNode(target); });
  $("add-node").addEventListener("click", () => openNodeForm());
  $("node-connect-mode").addEventListener("change", () => nodeConnectionMode());
  $("cancel-node").addEventListener("click", () => { $("node-token").value = ""; $("node-dialog").close(); });
  $("node-dialog").addEventListener("close", () => { $("node-token").value = ""; editingNode = null; });
  $("node-form").addEventListener("submit", async (event) => {
    event.preventDefault(); if (busy) return;
    const id = editingNode || $("node-id").value.trim(), token = $("node-token").value;
    const payload = { id, name: $("node-name").value.trim() };
    const showError = (message) => { $("node-form-error").hidden = false; display("node-form-error", message); };
    if (!id || id === "local" || !payload.name) { showError("请填写实例别名与名称；local 保留给本机。密钥请勿填入别名或地址。"); return; }
    if ($("node-connect-mode").value === "direct") {
      const host = nodeHost($("node-host").value), portText = $("node-port").value.trim(), scheme = $("node-scheme").value;
      if (!host) { showError("请填写有效的 IPv4 或 IPv6 地址；这里只填 IP，不带协议、端口或路径。"); return; }
      if (!/^[0-9]+$/.test(portText) || Number(portText) < 1 || Number(portText) > 65535) { showError("端口必须是 1 到 65535 的整数。"); return; }
      if (!["http", "https"].includes(scheme)) { showError("协议只能选择 HTTP 或 HTTPS。"); return; }
      Object.assign(payload, { host, port: Number(portText), scheme });
    } else {
      payload.base_url = $("node-url").value.trim();
      let url;
      try { url = new URL(payload.base_url); } catch { showError("请填写有效的 HTTP(S) 完整地址。"); return; }
      if (!["https:", "http:"].includes(url.protocol) || url.username || url.password || url.search || url.hash) { showError("地址只支持 HTTP(S)，不能包含用户名、密码、查询参数或片段；访问密钥请单独填写。"); return; }
    }
    if (!editingNode && !token) { showError("首次登记需要填写远端访问密钥。"); return; }
    if (token) payload.token = token;
    const editing = editingNode;
    const saved = await fleetMutation(editing ? `fleet/nodes/${encodeURIComponent(editing)}` : "fleet/nodes", payload, editing ? "PATCH" : "POST", editing ? "实例登记已更新。密钥仅保存在中央服务端，没有开始或暂停采集。" : "实例已登记。连接和同步由后台进行，登记不会开始来源采集。", token);
    if (saved) { $("node-dialog").close(); if (editing && editing === selectedNode) await switchNode(selectedNode, true); }
  });
  $("remove-node-dialog").addEventListener("close", async () => {
    const id = removingNode; removingNode = null;
    if ($("remove-node-dialog").returnValue !== "confirm" || !id) return;
    const removed = await fleetMutation(`fleet/nodes/${encodeURIComponent(id)}`, undefined, "DELETE", "实例登记已移除，已合并的数据和证据保留；远端任务状态没有改变。");
    if (removed && id === selectedNode) await switchNode("local");
  });
  $("sync-fleet").addEventListener("click", () => { void fleetMutation("fleet/sync", { node_id: "all" }, "POST", "已安排后台增量同步。同步只传输已经采集的记录与证据，不请求来源。"); });
  updateDownloadLinks();
  for (const scope of ["local", "fleet"]) for (const format of ["csv", "jsonl"]) {
    const link = $(`download-${scope}-${format}`);
    link.addEventListener("click", (event) => {
      if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault(); void downloadPosts(scope, format);
    });
  }
  $("refresh").addEventListener("click", () => { void poll(); });
  for (const kind of ["requests", "events"]) for (const action of ["prev", "next", "latest"]) $(`${kind}-${action}`).addEventListener("click", () => { void loadActivity(kind, action); });
  function switchTab(requests) {
    $("requests-tab").setAttribute("aria-selected", String(requests)); $("events-tab").setAttribute("aria-selected", String(!requests));
    $("requests-tab").tabIndex = requests ? 0 : -1; $("events-tab").tabIndex = requests ? -1 : 0;
    $("requests-panel").hidden = !requests; $("events-panel").hidden = requests;
  }
  $("requests-tab").addEventListener("click", () => switchTab(true));
  $("events-tab").addEventListener("click", () => switchTab(false));
  document.querySelector(".tabbar").addEventListener("keydown", (event) => {
    if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
      event.preventDefault(); const requests = event.key === "Home" || (event.key !== "End" && $("events-tab").getAttribute("aria-selected") === "true");
      switchTab(requests); $(requests ? "requests-tab" : "events-tab").focus();
    }
  });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) void poll(); });
  window.addEventListener("online", () => { void poll(); });
  defaultDates(1);
  clearProxyForm();
  void (async () => {
    try {
      const session = await api("session");
      if (session.authenticated === true) { showConsole(); await poll(); }
      else showAuth("输入部署时设置的访问密钥，建立控制台会话。");
    } catch (error) { showAuth(error.status === 401 ? "输入部署时设置的访问密钥，建立控制台会话。" : `无法确认会话：${error.message}`); }
  })();
})();
