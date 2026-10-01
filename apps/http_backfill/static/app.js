"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const display = (id, value) => { $(id).textContent = value == null || value === "" ? "—" : String(value); };
  const states = { idle: "待配置", running: "采集中", paused: "已暂停", blocked: "已拦截", error: "需要检查", completed: "任务结束" };
  const outcomes = { real_data: "数据已核实", reserved: "请求中 / 尚未确认", redirect: "重定向，等待下一次请求", detail_unavailable: "详情不可用", list_ok: "列表已核实", detail_ok: "正文已核实", success: "已核实", ok: "已核实", removed: "源已移除", deleted: "源已删除", access_block: "访问拦截", blocked: "访问拦截", challenge: "验证码响应", rate_limited: "限流响应", parse_error: "解析异常", schema_error: "结构异常", transport_error: "传输异常", error: "异常", pending: "待处理", unknown: "结果未确认" };
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
    const routed = node !== "local" && /^(status|jobs|control|requests|events|posts)(?:[/?]|$)/.test(path) ? `nodes/${encodeURIComponent(node)}/${path}` : path;
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
      card.append(el("p", "node-status", `${states[state.state] || "状态尚未取得"}${state.reason ? " · " + textValue(state.reason) : ""}`));
      const summary = el("div", "post-summary"); summary.append(el("strong", "", `已采集帖子 ${number(counts.unique_posts)}`));
      const subsets = el("div", "post-subset-counts"); subsets.append(el("span", "subset-prefix", "其中"));
      for (const [label, value] of [["已补详情", counts.body_complete], ["待补详情", counts.pending], ["未触发补详情", counts.list_only]]) subsets.append(el("span", "", `${label} ${number(value)}`));
      summary.append(subsets); card.append(summary, el("p", "node-sync", `源请求尝试 ${number(counts.attempts)} · 标题与详情属于同一条帖子`));
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
    display("selected-node-note", `下方任务、配置、控制与记录属于 ${nodeName()}。切换只改变查看对象，不会开始或暂停采集。`);
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
    if (formDirty) { pendingSelection = id; $("switch-node-dialog").showModal(); return; }
    void switchNode(id);
  }
  async function switchNode(id, force = false) {
    if (busy || (!force && id === selectedNode)) return false;
    selectedNode = id; nodeEpoch += 1; statusGeneration += 1; pollSerial += 1; polling = false;
    status = null; online = false; lastPoll = null; requestItems = []; latestRequestItems = [];
    pendingDeletion = null; retryNodeEpoch = null;
    for (const dialog of ["retry-dialog", "delete-dialog"]) if ($(dialog).open) { $(dialog).returnValue = "cancel"; $(dialog).close?.(); }
    clearForm();
    for (const kind of ["requests", "events"]) {
      const previous = activityPages[kind]; activityPages[kind] = { ...previous, latest: true, index: 0, cursors: [null], snapshot: null, page: null, loading: false, generation: previous.generation + 1 };
      $(kind === "requests" ? "request-list" : "event-list").replaceChildren(el("div", "empty-state", "正在读取所选实例的记录…")); activityError(kind); display(kind === "requests" ? "request-count" : "event-count", "—");
    }
    $("history-list").replaceChildren(el("div", "empty-state", "正在读取所选实例的任务历史…")); display("history-count", "—");
    $("coverage-list").replaceChildren(el("div", "empty-state", "正在读取所选实例的覆盖状态…")); display("coverage-count", "等待状态");
    for (const field of ["metric-attempts", "metric-pages", "metric-posts", "metric-list-only", "metric-bodies", "metric-pending", "run-duration", "current-target", "last-success"]) display(field, "—");
    for (const field of ["metric-failures", "metric-calibration", "metric-list-total", "metric-removed", "metric-body-breakdown", "observed-rate", "next-request", "next-request-time"]) display(field, "等待所选实例状态");
    $("block-panel").hidden = true; $("storage-panel").hidden = true; display("recovery-reason", "等待所选实例的校准状态。"); display("recovery-phase", "等待状态"); $("recovery-facts").replaceChildren();
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
  function controls() {
    const state = status ? status.state : "idle";
    const job = jobConfig();
    const locked = busy || !online || !authenticated;
    const pending = probePending();
    $("start").disabled = locked || !job || pending || status?.storage_halt || status?.active_halt || status?.request_inflight || !["paused", "idle"].includes(state);
    $("pause").disabled = locked || !(state === "running" || pending);
    $("retry").disabled = locked || pending || status?.request_inflight || (status?.storage_halt ? state === "running" : ((!job && !status?.active_halt) || !first(status, ["current_target", "current", "target"]) || !["paused", "blocked", "error"].includes(state)));
    display("retry", status?.storage_halt ? "重试数据库写入" : "单次探测重试");
    const awaitingStop = state === "running" || pending || status?.request_inflight;
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
    document.querySelectorAll("[data-fleet-action]").forEach((button) => { button.disabled = busy || !authenticated || button.dataset.current === "1"; });
    updatePager("requests"); updatePager("events");
    if (status?.storage_halt) display("action-note", "采集数据库写入异常，源采集已暂停。重试仅修复本地写入，成功后仍暂停。");
    else if (pending) display("action-note", "已安排单次探测；成功后保持暂停。");
    else if (state === "running") display("action-note", "关闭页面后服务端仍继续采集。暂停将在当前请求结束后生效。");
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
    $("interval").value = config.interval_seconds || 60;
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
    display("state-label", pending ? "单次探测已安排" : states[state] || state);
    $("state-badge").dataset.state = state;
    const titles = { idle: "配置窗口，开始长期观察", running: "正在按间隔采集", paused: config ? "任务已暂停，进度已保留" : "配置窗口，开始长期观察", blocked: "观测到访问拦截，已暂停", error: "观测到异常，已暂停", completed: "采集已结束，请核对覆盖" };
    display("run-title", pending ? "等待单次探测，不会自动继续" : titles[state] || "等待检查任务状态");
    const genericReasons = { idle: "尚未设置股票和日期窗口。保存配置后，手动启动采集。", paused: config ? "恢复采集后继续使用已保存的队列和请求间隔。" : "尚未设置股票和日期窗口。保存配置后，手动启动采集。", running: "列表和正文串行获取，任何源异常都会暂停后续请求。", blocked: "保留响应和失败位置，等待人工检查。", error: "保留响应和异常原因，等待人工检查。", completed: "本任务已停止，请逐股核对请求范围、源数据终点和未解决的缺口。" };
    display("run-reason", textValue(data.reason) || genericReasons[state]);
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
      const label = kind === "detail" || id != null ? `${stock ? stock + " · " : ""}正文 ${id || ""}` : `${stock || "列表"}${target.purpose === "recovery" ? " · 校准" : ""}${page == null ? "" : " · 源页码 " + page}`;
      display("current-target", label.trim());
    } else display("current-target", target || (config ? "等待下一项" : "—"));
    const success = first(data, ["last_success_at", "last_success", "last_source_success"]);
    display("last-success", success ? time(success) : "—");
    const rate = first(data, ["observed_requests_per_minute", "requests_per_minute", "observed_rate_per_minute"]);
    if (rate != null && Number.isFinite(Number(rate))) display("observed-rate", `实际 ${Number(rate).toFixed(2)} 次 / 分钟 · 间隔 ${config ? config.interval_seconds || 60 : "—"} 秒`);
    else {
      const times = latestRequestItems.map((item) => epoch(first(item, ["started_at", "requested_at", "at", "timestamp"]))).filter((value) => value != null).sort((a, b) => a - b);
      const rateObserved = times.length > 1 && times[times.length - 1] > times[0] ? (times.length - 1) * 60000 / (times[times.length - 1] - times[0]) : null;
      display("observed-rate", rateObserved == null ? "实际请求速率尚无观测" : `最近 ${times.length} 次：${rateObserved.toFixed(2)} 次 / 分钟`);
    }
    display("metric-attempts", number(counter("attempts", ["requests", "request_count"])));
    display("metric-pages", number(counter("list_pages", ["list_pages_success", "pages"])));
    const forwardPages = counter("list_pages", ["list_pages_success", "pages"]);
    const calibrationPages = counter("calibration_pages");
    display("metric-calibration", `校准成功 ${number(calibrationPages)} / 尝试 ${number(counter("calibration_requests"))}`);
    display("metric-list-total", `列表成功合计 ${number(forwardPages == null || calibrationPages == null ? null : Number(forwardPages) + Number(calibrationPages))} 次`);
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
    renderTiming();
    controls();
  }
  function renderBlock(data) {
    const firstBlock = first(data, ["first_block_evidence", "first_block"]);
    const evidence = firstBlock || first(data, ["blocking_evidence", "block_evidence", "block"]);
    const blocked = ["blocked", "error"].includes(data.state) && !data.storage_halt;
    $("block-panel").hidden = !blocked && !evidence;
    if ($("block-panel").hidden) return;
    const info = objectValue(evidence);
    const reason = first(info, ["reason", "error", "message"], data.reason || "历史拦截证据已保留。");
    display("block-title", blocked ? "已停止后续请求" : "历史阻断证据仍保留");
    display("block-kind", outcomes[first(info, ["outcome", "kind", "type"])] || first(info, ["outcome", "kind", "type"], "待检查"));
    display("block-reason", reason);
    const facts = [
      [firstBlock ? "首次阻断时间" : "阻断记录时间", time(first(info, ["at", "timestamp", "started_at", "started", "blocked_at", "time"]))],
      ["请求序号 / ID", first(info, ["attempt", "attempt_no", "request_id", "id", "sequence"], "—")],
      ["HTTP 状态", first(info, ["http_status", "status_code", "status"], "未知 / 无响应")],
    ];
    const list = $("block-facts"); list.replaceChildren();
    facts.forEach(([label, value]) => { const fact = el("div", "evidence-fact"); fact.append(el("span", "", label), el("strong", "", value)); list.append(fact); });
    display("block-evidence", evidence ? JSON.stringify(firstBlock ? { first_block: firstBlock, latest_block: data.block_evidence } : evidence, null, 2) : JSON.stringify({ state: data.state, reason: data.reason }, null, 2));
  }
  function renderCoverage(data, config) {
    let items = first(data, ["coverage", "stock_coverage", "per_stock"], []);
    if (!Array.isArray(items) && items && typeof items === "object") items = Object.entries(items).map(([stock, value]) => ({ stock, ...objectValue(value) }));
    if (!Array.isArray(items)) items = [];
    const list = $("coverage-list"); list.replaceChildren();
    if (!items.length && config && Array.isArray(config.stocks)) items = config.stocks.map((stock) => ({ stock, status: "pending" }));
    display("coverage-count", items.length ? `${items.length} 只股票 · 覆盖未确认` : "未配置");
    if (!items.length) { list.append(el("div", "empty-state", "保存股票和日期窗口后，这里会显示覆盖进度。")); return; }
    for (const item of items) {
      const stock = first(item, ["stock", "stock_code", "bar_code", "code"], "未知代码");
      const state = first(item, ["status", "state", "stop_reason"], Number(item.pages || item.list_pages) > 0 ? data.state === "running" ? "running" : "paused" : "pending");
      const complete = item.date_boundary_reached && item.details_complete && !item.gaps?.length;
      const gaps = first(item, ["gaps", "gap", "coverage_gap"]);
      const labels = { pending: "待观察", running: "进行中", active: "进行中", paused: "待继续", complete: "已发现项完成", completed: "已发现项完成", date_boundary_confirmed: "已到窗口边界", boundary_reached: "已到窗口边界", source_exhausted: "源数据到尾", gap: "存在缺口", blocked: "已拦截", exhausted: "源数据到尾" };
      const row = el("article", "coverage-item");
      const header = el("div", "coverage-item-header");
      const hasGap = Array.isArray(gaps) ? gaps.length > 0 : !!gaps;
      const actions = el("div", "coverage-item-actions");
      actions.append(el("span", `tag${complete ? " success" : hasGap ? " warning" : ""}`, complete ? "已发现项完成" : hasGap ? "存在缺口" : item.date_boundary_reached ? "已到窗口边界" : labels[state] || state));
      if (config && config.stocks?.includes(String(stock)) && /^\d{6}$/.test(String(stock))) {
        const remove = el("button", "stock-remove", "移除");
        remove.type = "button"; remove.dataset.removeStock = String(stock);
        remove.setAttribute("aria-label", `移除股票 ${stock} 的后续采集`);
        remove.addEventListener("click", () => confirmDeletion(String(stock)));
        actions.append(remove);
      }
      header.append(el("strong", "", stock), actions);
      row.append(header);
      const earliest = first(item, ["earliest_publish_time", "earliest_published_at", "earliest", "min_published_at", "oldest"]);
      const latest = first(item, ["latest_publish_time", "latest_published_at", "latest", "max_published_at", "newest"]);
      row.append(el("div", "coverage-range", earliest || latest ? `实际观察：${dateOnly(earliest)} 至 ${dateOnly(latest)}` : "尚未取得有效列表时间范围。"));
      const counters = el("div", "coverage-counts");
      const pages = first(item, ["list_pages", "pages", "pages_completed"]);
      const details = objectValue(item.details);
      const bodies = first(item, ["body_complete", "bodies_complete", "complete_posts"], first(details, ["complete"]));
      const required = first(details, ["required"], first(item, ["detail_required"]));
      const listOnly = first(details, ["list_only"], first(item, ["list_only"]));
      const posts = first(details, ["observed"], first(item, ["unique_posts", "posts", "total_posts"], required == null || listOnly == null ? null : Number(required) + Number(listOnly)));
      const pending = first(item, ["pending", "pending_details"], first(details, ["pending"]));
      const summary = el("div", "post-summary"); summary.append(el("strong", "", `已采集帖子 ${number(posts)}`));
      const subsets = el("div", "post-subset-counts"); subsets.append(el("span", "subset-prefix", "其中"));
      for (const [label, value] of [["已补详情", bodies], ["待补详情", pending], ["未触发补详情", listOnly]]) subsets.append(el("span", "", `${label} ${number(value)}`));
      summary.append(subsets, el("p", "post-record-note", "每条帖子保留列表记录与标题，详情补到同一条帖子。")); row.append(summary);
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
      list.append(row);
    }
  }
  const recoveryPhases = { pending: "等待校准", scheduled: "等待校准", seek: "定位 ID 与时间区间", anchor: "检查原始源页码", probe: "检查原始源页码", backtrack: "向前校准", scan: "回扫局部区间", scanning: "回扫局部区间", verify: "核对发现项", reconciling: "核对发现项", complete: "本轮校准结束", completed: "本轮校准结束", done: "本轮校准结束", paused: "校准已暂停", blocked: "校准被拦截", error: "校准异常", idle: "尚未校准" };
  const recoveryReasons = { process_restart: "进程重启后重新定位", manual_resume: "暂停恢复后重新定位", config_updated: "配置修改后重新核对列表位置", config_changed: "配置修改后重新核对列表位置", details_completed_recheck: "详情取得后核对列表偏移", forward_no_progress: "前进列表没有新增 ID，重新定位", source_tail_recheck: "核对来源尾页", date_boundary_confirmed: "已到请求日期边界，核对局部区间", source_exhausted: "来源列表到尾，核对局部区间" };
  function recoveryProof(info) {
    if (info.time_fallback || info.proof_level === "time_boundary_with_gap") return "旧 ID 不可见，仅按时间回扫，缺口保留。";
    if (info.time_order_verified === false || info.proof_level === "id_interval_time_order_unverified") return "发布时间次序尚未核实，局部覆盖不能确认。";
    if (info.proof_level === "two_matching_anchor_interval_observations") return "已观察的 ID 与时间局部区间两轮一致；整体覆盖仍未确认。";
    return "";
  }
  function recoveryDescription(recovery) {
    const info = objectValue(recovery);
    const phase = first(info, ["phase"], "pending");
    const facts = [recoveryPhases[phase] || phase, info.anchor_page == null ? "" : `原始源页码 ${number(info.anchor_page)}`, info.current_page == null ? "" : `当前源页码 ${number(info.current_page)}`, info.passes == null ? "" : `校准轮次 ${number(info.passes)}`, info.new_posts == null ? "" : `新增发现 ${number(info.new_posts)} 帖`].filter(Boolean);
    if (info.reason) facts.push(recoveryReasons[info.reason] || textValue(info.reason));
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
    const values = [["股票", first(info, ["stock", "stock_code"], data.current?.stock)], ["原始源页码", info.anchor_page], ["当前源页码", info.current_page], ["校准轮次", info.passes], ["偏移观察", info.drift_count], ["新增发现帖", info.new_posts]];
    values.forEach(([label, value]) => { if (value != null) facts.append(el("span", "", `${label} ${label === "股票" ? textValue(value) : number(value)}`)); });
    display("recovery-reason", [recoveryReasons[info.reason] || info.reason || "校准进度由服务端保存。暂停、编辑和刷新不会自动开始采集。", recoveryProof(info)].filter(Boolean).join(" · "));
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
    if ($("storage-panel").hidden) return;
    const failed = !!data.storage_halt || storage.status === "error";
    display("storage-state", failed ? "写入异常 · 已暂停" : storage.status === "ready" ? "可用" : "等待状态");
    $("storage-state").className = `tag ${failed ? "warning" : "success"}`;
    display("storage-path", storage.db_path || "采集数据库路径尚未返回");
    display("storage-note", storage.single_runtime_database === true || /^unified\./.test(storage.storage_layout || "") ? "本实例使用一份 collector.db 保存现有 posts / backfill 格式、请求记录和任务状态。采集数据的覆盖仍需核实，不会自动进入生产模型。" : "该实例尚未返回单库布局标记。升级并迁移后可将帖子、请求记录和任务状态统一保存到 collector.db；现有采集数据的覆盖仍需核实，不会自动进入生产模型。");
    display("storage-sync", `最近本地同步 ${time(storage.last_synced_at)}${storage.storage_version ? " · 格式 " + textValue(storage.storage_version) : ""}`);
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
      const title = `#${attempt} · ${stock ? stock + " · " : ""}${kind === "detail" || id != null ? "正文 " + (id || "") : (purpose === "recovery" ? "校准列表" : "前进列表") + (page == null ? "" : " · 源页码 " + page)}`;
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
      row.append(main, el("div", `activity-result ${resultClass(outcome)}`, `${outcomes[outcome] || outcome}${http == null ? "" : " · HTTP " + http}`));
      list.append(row);
    }
  }
  function renderEvents(data) {
    const items = unpack(data, "events"); display("event-count", data.total == null ? items.length : number(data.total));
    const list = $("event-list"); list.replaceChildren();
    if (!items.length) { list.append(el("div", "empty-state", "尚无控制事件。")); return; }
    const eventLabels = { job_created: "已保存任务配置", started: "开始采集", start: "开始采集", paused: "任务已暂停", pause: "任务已暂停", blocked: "访问拦截，自动暂停", error: "异常，自动暂停", retry: "已安排单次探测", probe_scheduled: "已安排单次探测", retry_scheduled: "已安排单次探测", probe_success: "探测成功，保持暂停", completed: "任务结束", recovered: "重启恢复，保持暂停" };
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
    if (snapshot.state === "running" || snapshot.probe_pending || snapshot.request_inflight) {
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
        applyStatus(result);
        notice(message); success = true;
      }
    } catch (error) {
      if (authenticated) notice(error.message, true);
    } finally { busy = false; controls(); if (authenticated) await poll(); }
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
    if (!Number.isInteger(interval) || interval < 60) { notice("全局请求间隔必须是至少 60 秒的整数。", true); $("interval").focus(); return; }
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
  void (async () => {
    try {
      const session = await api("session");
      if (session.authenticated === true) { showConsole(); await poll(); }
      else showAuth("输入部署时设置的访问密钥，建立控制台会话。");
    } catch (error) { showAuth(error.status === 401 ? "输入部署时设置的访问密钥，建立控制台会话。" : `无法确认会话：${error.message}`); }
  })();
})();
