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
  async function api(path, method = "GET", payload) {
    const options = { method, credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" } };
    if (payload !== undefined) { options.headers["Content-Type"] = "application/json"; options.body = JSON.stringify(payload); }
    const response = await fetch(new URL(`api/${path}`, document.baseURI), options);
    const contentType = response.headers.get("content-type") || "";
    const data = contentType.includes("application/json") ? await response.json() : null;
    if (!response.ok) {
      if (response.status === 401 && authenticated) showAuth("会话已过期，请重新输入访问密钥。采集服务的状态不受页面退出影响。");
      const err = new Error(data && data.error ? textValue(data.error) : `本地接口返回 HTTP ${response.status}`);
      err.status = response.status;
      throw err;
    }
    if (data === null) throw new Error("本地接口返回了非 JSON 响应，请检查服务或反向代理配置。");
    return data;
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
    $("start").disabled = locked || !job || pending || status?.request_inflight || !["paused", "idle"].includes(state);
    $("pause").disabled = locked || !(state === "running" || pending);
    $("retry").disabled = locked || !job || pending || status?.request_inflight || !first(status, ["current_target", "current", "target"]) || !["paused", "blocked", "error"].includes(state);
    $("config-fields").disabled = locked || pending || (job && state !== "completed") || !["paused", "idle", "completed"].includes(state);
    $("save-config").disabled = $("config-fields").disabled;
    $("refresh").disabled = busy || polling;
    $("logout").disabled = busy;
    if (pending) display("action-note", "已安排单次探测；成功后保持暂停。");
    else if (state === "running") display("action-note", "关闭页面后服务端仍继续采集。暂停将在当前请求结束后生效。");
    else if (["blocked", "error"].includes(state)) display("action-note", "已暂停后续请求。检查证据后，可手动安排一次探测。");
    else if (state === "completed") display("action-note", "请核对每股的完成情况和缺口，可另建日期窗口。");
    else if (job) display("action-note", "点击开始后，按持久化的全局请求间隔调度。");
    else display("action-note", "保存配置不会发起源请求。");
    if (job && state !== "completed") display("config-message", "现有任务的范围已固定；完成后可新建窗口。");
  }
  function setConnection(ok) {
    online = ok;
    $("connection-dot").className = `connection-dot ${ok ? "online" : "offline"}`;
    display("connection-text", ok ? "控制台已连接" : "本地服务未连接");
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
    display("config-state", "已保存");
    display("config-message", "保存后保持暂停，手动启动。");
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
      if (!loadedConfig) loadForm(config);
      if (!formDirty) display("config-state", "已保存");
    } else display("job-window", "未配置日期窗口");
    const target = first(data, ["current_target", "current", "target"]);
    if (target && typeof target === "object") {
      const kind = first(target, ["kind", "type"]);
      const stock = first(target, ["stock", "stock_code", "bar_code"], "");
      const page = first(target, ["page", "page_number"]);
      const id = first(target, ["post_id", "source_item_id", "id"]);
      const label = kind === "detail" || id != null ? `${stock ? stock + " · " : ""}正文 ${id || ""}` : `${stock || "列表"}${page == null ? "" : " · 第 " + page + " 页"}`;
      display("current-target", label.trim());
    } else display("current-target", target || (config ? "等待下一项" : "—"));
    const success = first(data, ["last_success_at", "last_success", "last_source_success"]);
    display("last-success", success ? time(success) : "—");
    const rate = first(data, ["observed_requests_per_minute", "requests_per_minute", "observed_rate_per_minute"]);
    if (rate != null && Number.isFinite(Number(rate))) display("observed-rate", `实际 ${Number(rate).toFixed(2)} 次 / 分钟 · 间隔 ${config ? config.interval_seconds || 60 : "—"} 秒`);
    else {
      const times = requestItems.map((item) => epoch(first(item, ["started_at", "requested_at", "at", "timestamp"]))).filter((value) => value != null).sort((a, b) => a - b);
      const rateObserved = times.length > 1 && times[times.length - 1] > times[0] ? (times.length - 1) * 60000 / (times[times.length - 1] - times[0]) : null;
      display("observed-rate", rateObserved == null ? "实际请求速率尚无观测" : `最近 ${times.length} 次：${rateObserved.toFixed(2)} 次 / 分钟`);
    }
    display("metric-attempts", number(counter("attempts", ["requests", "request_count"])));
    display("metric-pages", number(counter("list_pages", ["list_pages_success", "pages"])));
    display("metric-posts", number(counter("unique_posts", ["posts", "posts_count"])));
    display("metric-bodies", number(counter("body_complete", ["bodies_complete", "detail_success", "complete_posts"])));
    display("metric-pending", number(counter("pending", ["pending_details", "body_pending"])));
    display("metric-failures", `失败 / 异常 ${number(counter("failures", ["failed", "errors"]))}`);
    display("metric-removed", `详情不可访问 ${number(counter("removed", ["deleted"]))}`);
    display("metric-body-breakdown", `非空 ${number(counter("body_nonempty", ["nonempty_body", "nonempty_bodies", "nonempty", "body_non_empty"]))} · 有效空正文 ${number(counter("body_empty", ["source_empty_body", "empty_bodies", "empty"]))}`);
    renderBlock(data);
    renderCoverage(data, config);
    renderTiming();
    controls();
  }
  function renderBlock(data) {
    const firstBlock = first(data, ["first_block_evidence", "first_block"]);
    const evidence = firstBlock || first(data, ["blocking_evidence", "block_evidence", "block"]);
    const blocked = ["blocked", "error"].includes(data.state);
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
    display("coverage-count", items.length ? `${items.length} 只股票` : "未配置");
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
      header.append(el("strong", "", stock), el("span", `tag${complete ? " success" : hasGap ? " warning" : ""}`, complete ? "已发现项完成" : hasGap ? "存在缺口" : item.date_boundary_reached ? "已到窗口边界" : labels[state] || state));
      row.append(header);
      const earliest = first(item, ["earliest_publish_time", "earliest_published_at", "earliest", "min_published_at", "oldest"]);
      const latest = first(item, ["latest_publish_time", "latest_published_at", "latest", "max_published_at", "newest"]);
      row.append(el("div", "coverage-range", earliest || latest ? `实际观察：${dateOnly(earliest)} 至 ${dateOnly(latest)}` : "尚未取得有效列表时间范围。"));
      const counters = el("div", "coverage-counts");
      const pages = first(item, ["list_pages", "pages", "pages_completed"]);
      const details = objectValue(item.details);
      const bodies = first(item, ["body_complete", "bodies_complete", "complete_posts"], first(details, ["complete"]));
      const posts = first(item, ["unique_posts", "posts", "total_posts"], first(details, ["required"]));
      const pending = first(item, ["pending", "pending_details"], first(details, ["pending"]));
      const counts = [["列表页", pages], ["原始列表行", first(item, ["rows"])], ["窗口内帖", posts], ["正文", bodies], ["待取", pending]];
      counts.forEach(([label, value]) => counters.append(el("span", "", `${label} ${number(value)}`)));
      row.append(counters);
      if (posts != null && bodies != null && Number(posts) > 0) {
        const bar = el("div", "coverage-progress");
        bar.setAttribute("role", "progressbar"); bar.setAttribute("aria-label", `${stock} 已取得正文占当前发现帖子的比例`);
        const percent = Math.min(100, Math.max(0, Number(bodies) / Number(posts) * 100));
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
      list.append(row);
    }
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
  function renderRequests(data) {
    requestItems = unpack(data, "requests");
    display("request-count", requestItems.length);
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
      const title = `#${attempt} · ${stock ? stock + " · " : ""}${kind === "detail" || id != null ? "正文 " + (id || "") : "列表" + (page == null ? "" : "第 " + page + " 页")}`;
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
    const items = unpack(data, "events"); display("event-count", items.length);
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
    polling = true; controls();
    try {
      const results = await Promise.allSettled([api("status"), api("requests?limit=30"), api("events?limit=20")]);
      if (!authenticated) return;
      const [s, requests, events] = results;
      if (s.status === "rejected") { setConnection(false); notice(`无法读取本地状态：${s.reason.message}。显示的是最近取得的状态。`, true); return; }
      if (requests.status === "fulfilled") renderRequests(requests.value);
      if (events.status === "fulfilled") renderEvents(events.value);
      lastPoll = Date.now();
      renderStatus(s.value);
      setConnection(true);
      if (requests.status === "rejected" || events.status === "rejected") notice("状态已更新，但部分运行记录暂时无法读取。", true);
      else if ($("notice").dataset.networkError === "true") { notice(""); delete $("notice").dataset.networkError; }
    } catch (error) {
      if (authenticated) { setConnection(false); notice(`本地服务连接失败：${error.message}`, true); }
    } finally { if (!online) $("notice").dataset.networkError = "true"; polling = false; controls(); }
  }
  function startTimers() {
    stopTimers();
    timer = setInterval(() => { if (!document.hidden) void poll(); }, 5000);
    tickTimer = setInterval(renderTiming, 1000);
  }
  function stopTimers() { clearInterval(timer); clearInterval(tickTimer); timer = null; tickTimer = null; }
  async function post(path, payload, message) {
    if (busy || !authenticated || !online) return false;
    busy = true; controls(); notice("");
    let success = false;
    try {
      const result = await api(path, "POST", payload);
      if (authenticated) {
        if (result && result.state) { lastPoll = Date.now(); renderStatus(result); setConnection(true); }
        notice(message); success = true;
      }
    } catch (error) {
      if (authenticated) notice(error.message, true);
    } finally { busy = false; controls(); if (authenticated) await poll(); }
    return success;
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
  $("config-form").addEventListener("input", () => { formDirty = true; display("config-state", "未保存"); });
  document.querySelectorAll("[data-years]").forEach((button) => button.addEventListener("click", () => { defaultDates(Number(button.dataset.years)); formDirty = true; display("config-state", "未保存"); }));
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
    if (jobConfig() && !window.confirm("保存会创建新的实验任务，并保持暂停。当前任务的证据由服务端保留。确认保存这组股票和日期窗口？")) return;
    const saved = await post("jobs", { stocks, from_date: from, to_date: to, interval_seconds: interval, client: $("client").value }, "配置已保存，尚未发起源请求。点击开始采集后才会调度。");
    if (saved && status && jobConfig()) { formDirty = false; loadedConfig = false; loadForm(jobConfig()); controls(); }
  });
  $("start").addEventListener("click", () => { void post("control", { action: "start" }, "已提交开始指令；源请求由服务端按全局间隔安排。"); });
  $("pause").addEventListener("click", () => { void post("control", { action: "pause" }, "已提交暂停指令，队列与响应证据会保留。"); });
  $("retry").addEventListener("click", () => { renderTiming(); $("retry-dialog").showModal(); });
  $("retry-dialog").addEventListener("close", () => { if ($("retry-dialog").returnValue === "confirm") void post("control", { action: "retry" }, "已安排一次探测。发送时间遵守间隔与冷却期，成功后仍暂停。"); });
  $("refresh").addEventListener("click", () => { void poll(); });
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
