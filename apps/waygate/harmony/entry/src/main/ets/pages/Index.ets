import { webview } from '@kit.ArkWeb';
import { common } from '@kit.AbilityKit';
import { hilog } from '@kit.PerformanceAnalysisKit';
import { util } from '@kit.ArkTS';
import { connection, http } from '@kit.NetworkKit';
import { Api, readableError } from '../services/Api';
import { Vault } from '../services/Vault';
import { registerPush } from '../services/Push';
import { runtime } from '../services/SessionRuntime';
import { AuditView, BuiltinNode, BuiltinConfig, Credentials, DirectClaim, DirectTarget, EmptyReply, ExitValue, Inbox, Incident, Link, NodeProfile, NodeState, PrepareValue, ProbeValue, ResultValue, ResumeValue, StockState, VerificationDescriptor } from '../services/Models';
import { NodeSsh, runAction } from '../services/NodeSsh';
import { screenOn } from '../services/ScreenOn';
import { close, connect, exec, forward, probeHostKey, SshConnectOptions, SshForwardOptions } from 'libwaygate_ssh.so';

const INK = '#173E37';
const MUTED = '#657970';
const BG = '#F3F6F5';

// The hub exposes no device push channel yet, so the app still has to ask; it only
// decides how often from how much can have changed. A moving feed (or any user action)
// keeps the short interval, an unchanged feed backs off, and a running verification
// keeps the short interval because its outcome is what the operator is waiting on.
const POLL_ACTIVE_MS = 15000;
const POLL_BASE_MS = 30000;
const POLL_SLOW_MS = 60000;
const POLL_IDLE_MS = 120000;
const POLL_IDLE_ROUNDS = 3;
// A step that never returns must not leave the screen with every control disabled, which
// used to leave killing the process as the only way out. Every callback-based wait is
// therefore raced against a timeout, and a stuck busy flag is force-released.
const CALLBACK_WAIT_MS = 2500;
const BUSY_WATCHDOG_MS = 300000;
// An unconfirmed probe is a claim, not a lock. After this long its outcome can no longer be
// read by request id, so the operator is given an explicit way to close it instead of the
// app sitting on "结果尚未确认" forever.
const CLAIM_TTL_MS = 15 * 60 * 1000;

function bounded(work: Promise<void>, ms: number = CALLBACK_WAIT_MS): Promise<void> {
  // A rejection that arrives after the race settled has nobody left to observe it, so it
  // is consumed here; a rejection that arrives in time still propagates through the race.
  work.catch(() => {});
  return Promise.race([work, new Promise<void>((resolve) => { setTimeout(() => { resolve(); }, ms); })]);
}

@Entry
@Component
struct Index {
  @State screen: string = 'loading';
  @State items: Incident[] = [];
  @State nodes: NodeState[] = [];
  @State message: string = '';
  @State syncMessage: string = '';
  @State failure: string = '';
  // Messages from other nodes are folded away instead of being shown in place of the node
  // the operator is working on now.
  @State otherNodeNotes: string[] = [];
  @State showOtherNotes: boolean = false;
  @State diagnosticSnapshot: string = '';
  @State busy: boolean = false;
  @State hub: string = '';
  @State token: string = '';
  @State notification: string = '通知尚未开启';
  @State showWeb: boolean = false;
  @State webUrl: string = 'about:blank';
  @State verifiedExit: boolean = false;
  @State stockTitle: string = '';
  // One guidance line for the probe, derived from its state. The screen used to show a
  // "continue checking" button next to a "go to the console" sentence, which pointed the
  // operator in two directions at once; there is now exactly one instruction per state.
  @State claimFailure: string = '';
  @State claimCanResume: boolean = false;
  @State claimAge: string = '';
  @State claimExpired: boolean = false;
  @State nodeHint: string = '';
  @State nodeHost: string = '';
  @State nodePort: string = '22';
  @State nodeUser: string = 'root';
  @State nodeAuth: string = 'password';
  @State nodeSecret: string = '';
  @State nodePassphrase: string = '';
  @State nodeKey: string = '';
  @State nodeFingerprint: string = '';
  @State channelOutput: string = '';
  @StorageLink('foreground') @Watch('visibilityChanged') foreground: boolean = true;
  @StorageLink('incidentLink') @Watch('linkChanged') incidentLink: string = '';

  private vault: Vault = new Vault();
  private ssh: NodeSsh = new NodeSsh();
  private api: Api | undefined = undefined;
  private credentials: Credentials = { hub: '', token: '', deviceId: '', push: false, pendingClaim: '' };
  private browser: webview.WebviewController = new webview.WebviewController();
  private descriptor: VerificationDescriptor | undefined = undefined;
  private incidentId: string = '';
  private claim: DirectClaim | undefined = undefined;
  private nodeConfirmed: boolean = false;
  private channelMode: boolean = false;
  private bundledNodes: BuiltinNode[] = [];
  private bundledSecrets: Map<string, string> = new Map();
  private bundledChecked: boolean = false;
  private bundledFailed: string = '';
  private measuredOnce: boolean = false;
  private probeWaitAborted: boolean = false;
  private exitWaiter: ((ip: string) => void) | undefined = undefined;
  private timer: number = -1;
  private busyTimer: number = -1;
  private disposed: boolean = false;
  // Counts consecutive polls whose actionable state did not move; drives the back-off.
  private unchangedRounds: number = 0;
  private feedSignature: string = '';
  private polling: boolean = false;
  private proxyApplied: boolean = false;
  // True once the SSH tunnel and the browser proxy are up and usable, so a failure after
  // that can be retried without rebuilding them; cleared whenever everything is released.
  private channelReady: boolean = false;
  private destroyWeb: (() => void) | undefined = undefined;
  private detachPending: Promise<void> | undefined = undefined;
  private cleanupPending: Promise<void> | undefined = undefined;
  private generation: number = 0;
  private lastRefreshAt: string = '';
  private lastCollectorSync: string = '';
  private lastSyncFailure: string = '';
  // Latest message per node label, so one node's miss is never read as another node's.
  private nodeNotes: Map<string, string> = new Map();
  // The step that failed keeps everything it needs to be retried in place.
  private retryDescriptor: VerificationDescriptor | undefined = undefined;
  private retryProfile: NodeProfile | undefined = undefined;
  // Profile of the flow currently running, so a channel that died while the app was hidden
  // can be rebuilt without asking the operator for anything.
  private activeProfile: NodeProfile | undefined = undefined;

  aboutToAppear(): void { this.initialize(); this.watchNetwork(); }
  // The page going away is not a reason to tear the connection down either; the ability's
  // onDestroy releases everything when the app really ends.
  aboutToDisappear(): void { this.disposed = true; this.stopRefresh(); this.stopBusyTimer(); }

  // A real detection mechanism instead of waiting for a timeout to declare failure:
  // the system tells the app when the link disappears and when it comes back, so the
  // operator sees the reason immediately and a returning link recovers on its own.
  private watchNetwork(): void {
    try {
      const monitor = connection.createNetConnection();
      monitor.on('netLost', () => { this.message = '网络已断开；恢复后会自动重试，已建立的验证不受影响'; });
      monitor.on('netAvailable', () => {
        this.message = '网络已恢复，正在重新同步';
        this.scheduleRefresh(true);
      });
      monitor.register((err) => {
        if (err) { this.message = '网络状态监听不可用；仍会按超时提示失败原因'; }
      });
    } catch (_) {
      this.message = '网络状态监听不可用；仍会按超时提示失败原因';
    }
  }

  // One self-rescheduling poll instead of a fixed tick: each round picks the next delay
  // from the current state, so an idle phone backs off while an active one stays quick.
  // Passing immediate=true is how user actions and link/network events jump the queue.
  private scheduleRefresh(immediate: boolean = false): void {
    this.stopRefresh();
    if (this.disposed || !this.api) { return; }
    const delay = immediate ? 0 : this.nextRefreshDelay();
    this.timer = setTimeout(() => {
      this.timer = -1;
      this.refresh().finally(() => { this.scheduleRefresh(); });
    }, delay);
  }

  private stopRefresh(): void {
    if (this.timer !== -1) { clearTimeout(this.timer); this.timer = -1; }
  }

  // Busy is the flag that greys out every control, so it is armed together with a
  // watchdog: however a step ends, the operator always gets the app back.
  private enterBusy(): void {
    this.busy = true;
    this.stopBusyTimer();
    this.busyTimer = setTimeout(() => {
      this.busyTimer = -1;
      if (this.busy) {
        this.busy = false;
        this.message = '上一步超时未返回，已恢复操作；可重试或进入控制台';
      }
    }, BUSY_WATCHDOG_MS);
  }

  private leaveBusy(): void {
    this.stopBusyTimer();
    this.busy = false;
  }

  private stopBusyTimer(): void {
    if (this.busyTimer !== -1) { clearTimeout(this.busyTimer); this.busyTimer = -1; }
  }

  private nextRefreshDelay(): number {
    if (!this.foreground) { return POLL_IDLE_MS; }
    if (runtime.dispatching || this.showWeb) { return POLL_ACTIVE_MS; }
    if (this.unchangedRounds >= POLL_IDLE_ROUNDS) { return POLL_IDLE_MS; }
    if (this.unchangedRounds >= 1) { return POLL_SLOW_MS; }
    return POLL_BASE_MS;
  }

  private context(): common.UIAbilityContext {
    return this.getUIContext().getHostContext() as common.UIAbilityContext;
  }

  // Failures carry the node/task they belong to, so a stale message is never mistaken
  // for the node the operator is working on now.
  private error(err: Error): void {
    const context = this.stockTitle ? this.stockTitle + '：' : '';
    const text = readableError(context + (err.message || '操作未完成，请重试连接'));
    this.message = text;
    this.noteForNode(text);
  }

  private noteForNode(text: string): void {
    const current = this.stockTitle ? this.stockTitle : '其他';
    this.nodeNotes.set(current, text);
    const others: string[] = [];
    this.nodeNotes.forEach((note: string, label: string) => {
      if (label !== current) { others.push(note); }
    });
    this.otherNodeNotes = others;
  }

  private syncFailed(message: string, details: string): void {
    const safeMessage = message.split(this.credentials.token).join('[已隐藏]').slice(0, 400);
    this.syncMessage = readableError(safeMessage);
    this.lastSyncFailure = new Date().toLocaleString() + '\n' + safeMessage + '\n' + details;
  }

  private openDiagnostics(): void {
    this.diagnosticSnapshot = '当前状态：' + (this.syncMessage || (this.lastRefreshAt ? '连接正常' : '尚未同步')) +
      '\n主控：' + (this.api ? this.api.credentials.hub : this.hub) +
      '\n最近成功刷新：' + (this.lastRefreshAt || '尚无') +
      '\ncollector 最近同步：' + (this.lastCollectorSync || '尚无') +
      '\n\n最近一次同步故障' + (this.syncMessage ? '：\n' : '（当前无同步故障）：\n') +
      (this.lastSyncFailure || '无') +
      '\n\n最近一次接口失败：\n' + (this.api && this.api.lastFailureDetails ? this.api.lastFailureDetails : '无');
    this.screen = 'diagnostics';
  }

  private readClaim(): DirectClaim | undefined {
    if (!this.credentials.pendingClaim) { return undefined; }
    try { return JSON.parse(this.credentials.pendingClaim) as DirectClaim; } catch (_) { return undefined; }
  }

  private async initialize(): Promise<void> {
    try {
      this.credentials = await this.vault.load(this.context());
      this.hub = this.credentials.hub;
      this.token = this.credentials.token;
      if (this.hub && this.token) {
        this.api = new Api(this.credentials);
        runtime.api = this.api;
        this.screen = 'home';
        const claim = this.readClaim();
        if (claim) {
          this.claim = claim;
          runtime.dispatching = true;
          this.claimCanResume = claim.before_request_id > 0;
          this.claimAge = this.ageText(claim.at);
          this.claimExpired = Date.now() - claim.at > CLAIM_TTL_MS;
          this.claimFailure = '';
          this.message = '';
          this.screen = 'result';
        } else if (runtime.incident && runtime.descriptor && runtime.profile) {
          // The system recreated this page while a verification was open. The session that
          // lived in memory is gone, so the flow cannot silently continue; say so and let the
          // same step be rebuilt without asking for credentials again.
          this.descriptor = runtime.descriptor;
          this.retryDescriptor = runtime.descriptor;
          this.retryProfile = runtime.profile;
          this.incidentId = runtime.incident;
          this.failure = '页面被系统重建，节点通道需要重新建立；任务保持暂停，不会重发探测';
          this.message = this.failure;
          this.screen = 'retry';
        }
        await this.refresh();
        if (this.credentials.push) { this.enablePush(false); }
      } else { this.screen = 'settings'; }
    } catch (err) {
      this.error(err as Error);
      this.screen = this.api ? 'home' : 'settings';
    }
    this.scheduleRefresh(true);
  }

  private async save(): Promise<void> {
    const address = this.hub.trim().replace(/\/+$/, '');
    if (!/^https:\/\/[^/?#@\s]+(?:\/[^?#\s]*)?$/.test(address)) {
      this.message = '请填写主控 HTTPS 地址，例如 https://你的域名/waygate'; return;
    }
    if (this.token.trim().length < 16 || this.token.trim().length > 1023) {
      this.message = '请填写主控的 console.token'; return;
    }
    this.enterBusy();
    this.message = '正在连接主控并校验令牌…';
    try {
      const next: Credentials = { hub: address, token: this.token.trim(), deviceId: this.credentials.deviceId,
        push: false, pendingClaim: this.credentials.pendingClaim };
      const api = new Api(next);
      await api.request<Inbox>('/v1/inbox');
      // Stop notifications to the previous hub when changing the login.
      if (this.api && this.credentials.push) {
        await this.api.request<EmptyReply>('/v1/devices/' + this.credentials.deviceId, http.RequestMethod.DELETE);
      }
      await this.vault.save(next);
      this.credentials = next; this.api = api; runtime.api = api;
      this.screen = 'home'; this.message = ''; this.notification = '通知尚未开启';
      this.syncMessage = ''; this.lastRefreshAt = ''; this.lastCollectorSync = ''; this.lastSyncFailure = '';
      await this.refresh();
    } catch (err) { this.error(err as Error); }
    finally { this.leaveBusy(); }
  }

  private async refresh(): Promise<void> {
    if (!this.api || this.polling || !this.foreground) { return; }
    this.polling = true;
    try {
      const feed = await this.api.request<Inbox>('/v1/inbox', http.RequestMethod.GET, '', 2);
      this.items = feed.items; this.nodes = feed.nodes;
      // Only the actionable state decides the cadence: an incident appearing or clearing,
      // or a node going offline. Post counters move constantly while the fleet collects,
      // so including them would keep resetting the back-off and defeat the whole point.
      const signature = feed.items.map((item: Incident): string => item.id + '|' + item.snoozed).join(',') +
        '#' + feed.nodes.map((node: NodeState): string => node.id + ':' + (node.online ? '1' : '0')).join(',') +
        '#' + (feed.error ? '1' : '0');
      if (signature === this.feedSignature) { this.unchangedRounds++; }
      else { this.unchangedRounds = 0; this.feedSignature = signature; }
      this.lastCollectorSync = feed.last_poll > 0 ? new Date(feed.last_poll * 1000).toLocaleString() : '';
      if (feed.error) {
        // The feed error is the hub's own collector sync, not a node problem: label it so
        // it cannot be read as "the node I just touched failed".
        this.syncFailed('主控同步失败：' + feed.error, '接口：GET /v1/inbox\nHTTP：200\n阶段：服务端同步 collector');
      } else {
        this.syncMessage = '';
        this.lastRefreshAt = new Date().toLocaleString();
      }
      if (!feed.push_enabled) { this.notification = '主控推送尚未配置，待办仍会保存'; }
      else if (feed.push_errors > 0) { this.notification = '有通知发送失败，请检查主控推送配置'; }
      this.prioritizeLink();
      await this.settleIfResolved();
    } catch (err) {
      this.syncFailed((err as Error).message || '主控刷新失败', this.api.lastFailureDetails);
    } finally { this.polling = false; }
  }

  // The hub drops an incident only after the node reports no active halt, so a
  // vanished claim means the outcome is settled elsewhere; stop asking about it.
  private async settleIfResolved(): Promise<void> {
    const claim = this.claim;
    if (!claim || !runtime.dispatching) { return; }
    if (this.items.some((item: Incident) => item.id === claim.incident)) { return; }
    this.claim = undefined;
    runtime.dispatching = false;
    this.claimCanResume = false;
    this.claimFailure = '';
    this.message = '上次的验证已在主控或控制台结束，无需再核对';
    await this.vault.claim('');
  }

  private prioritizeLink(): void {
    const id = this.incidentLink.replace('waygate://incident/', '');
    if (/^[a-f0-9]{32}$/.test(id)) {
      const selected = this.items.find((item: Incident) => item.id === id);
      if (selected) { this.items = [selected].concat(this.items.filter((item: Incident) => item.id !== id)); }
    }
  }

  private linkChanged(): void {
    if (!runtime.incident && this.screen !== 'settings') { this.goHome(); }
    this.prioritizeLink();
  }

  // Locking the screen or switching apps must pause the app, not reset it. Tearing down the
  // tunnel and the browser here used to throw away a captcha the operator had already
  // solved and force the whole flow from the start on a link that is slow to begin with.
  // Everything is therefore kept: the SSH session, the tunnel, the proxy override and the
  // page itself. Only the poll stops, because nothing can be seen while hidden.
  private visibilityChanged(): void {
    if (!this.foreground) {
      this.stopRefresh();
      return;
    }
    // The system drops a keep-screen-on hold when the window leaves the foreground; if the
    // verification page is still the one on screen, it has to be taken again.
    if (this.showWeb) { screenOn.hold(true); }
    this.scheduleRefresh(true);
    this.resumeChannel();
  }

  // The page itself survives a lock, but the operating system may still drop the underlying
  // connection. A read-only audit tells us which happened: alive means the operator keeps the
  // page exactly as it was, dead means the channel is honestly rebuilt instead of leaving a
  // browser stuck on ERR_PROXY_CONNECTION_FAILED.
  private async resumeChannel(): Promise<void> {
    if (!this.channelReady || !this.descriptor || runtime.sshId < 0 || this.busy) { return; }
    let alive = false;
    const descriptor = this.descriptor;
    const check = runAction<AuditView>(runtime.sshId, descriptor.worker_command, '{"action":"audit"}')
      .then(() => { alive = true; })
      .catch(() => { alive = false; });
    await bounded(check, 8000);
    if (alive) { return; }
    this.channelReady = false;
    this.retryDescriptor = descriptor;
    this.retryProfile = this.activeProfile;
    this.failure = '从后台回来时节点通道已断开；点“重试本步”重建，任务保持暂停，不会重发探测';
    this.message = this.failure;
    await this.removeProxy();
    await bounded(this.detach());
    this.screen = 'retry';
  }

  private async detach(): Promise<void> {
    if (this.detachPending) { return this.detachPending; }
    if (!this.showWeb) { return; }
    // Unmounting the component is the cancellation. A synchronous controller call here could
    // block the UI thread while the page is still waiting on a stuck proxy request, which is
    // exactly when the operator presses back.
    this.detachPending = new Promise<void>((resolve) => {
      this.destroyWeb = resolve;
      this.showWeb = false;
      // The hold belongs to the verification page; no page means no reason to defeat sleep.
      screenOn.hold(false);
    });
    await bounded(this.detachPending);
    // Drop the pending promise even after a timeout: keeping a never-resolving one here
    // would make every later detach() wait on it forever.
    this.detachPending = undefined;
    this.destroyWeb = undefined;
  }

  private async removeProxy(): Promise<void> {
    if (!this.proxyApplied) { return; }
    // The override is also dropped by the app itself when the process ends, so a callback
    // that never arrives may not park the operator here: this is bounded and best effort.
    await bounded(new Promise<void>((resolve) => {
      try { webview.ProxyController.removeProxyOverride(() => resolve()); }
      catch (_) { resolve(); }
    }));
    this.proxyApplied = false;
  }

  // One teardown order for every release path, because a stale proxy override pointing at a
  // closed listener is what produced both the blank page and ERR_PROXY_CONNECTION_FAILED:
  //   1. stop the page from using the proxy, 2. drop the override that names it,
  //   3. only then close the listener, 4. release the SSH resources.
  private async stopWindow(): Promise<void> {
    this.channelReady = false;
    await this.detach();
    await this.removeProxy();
    await runtime.proxy.stop();
    // Covers the paths that never got as far as showing a page (a failed proxy override, an
    // unreachable exit check), where detach() returns immediately and nothing else releases.
    screenOn.hold(false);
    // Releasing the waiter must also settle it. Dropping the reference alone left
    // measureExit() pending forever, so the whole verification chain never unwound, busy
    // stayed true and every control felt dead until the request happened to finish.
    const waiter = this.exitWaiter;
    this.exitWaiter = undefined;
    if (waiter) { waiter(''); }
    this.verifiedExit = false;
  }

  // Releases every resource this phone created for the current verification.
  private async releaseAll(): Promise<void> {
    this.generation++;
    this.probeWaitAborted = true;
    this.channelReady = false;
    await this.stopWindow();
    await runtime.releaseSsh();
    this.activeProfile = undefined;
    runtime.incident = '';
    runtime.target = undefined;
    runtime.descriptor = undefined;
    runtime.profile = undefined;
  }

  // Going "back" always means the inbox; an unsettled claim is a durable record, not a reason
  // to keep the operator on the result screen. This used to branch on runtime.dispatching,
  // which made the header's 返回 button a no-op while a claim existed.
  private async goHome(): Promise<void> {
    this.screen = 'home';
    this.message = '';
    try {
      await bounded(this.releaseAll());
      await this.refresh();
    } catch (err) { this.error(err as Error); }
  }

  // The one routing table for the system back key and the on-screen 返回 button, so the two
  // can never disagree again.
  private navigateBack(): boolean {
    if (this.screen === 'retry') { this.abandonRetry(); return true; }
    if (this.screen === 'result') { this.leaveResult(); return true; }
    if (this.screen === 'diagnostics') { this.screen = 'home'; return true; }
    if (this.screen !== 'home' && this.screen !== 'settings') { this.goHome(); return true; }
    return false;
  }

  // Leaving the result screen must not lock the app: the claim stays durable and the
  // same incident routes back here, while every other incident and the console become
  // usable again. The claim itself is not cleared just because the screen was left.
  private async leaveResult(): Promise<void> {
    this.screen = 'home';
    this.message = '';
    try {
      await bounded(this.releaseAll());
      await this.refresh();
    } catch (err) { this.error(err as Error); }
  }

  private async openConsole(): Promise<void> {
    // The console is never blocked, by anything: not a running verification, not a pending
    // probe result, and not a busy flag that some earlier step has not cleared yet. Reaching
    // it ends the phone-side verification (the worker's task stays paused) and resends
    // nothing, so there is no correctness reason to refuse it.
    if (!this.api) { return; }
    if (runtime.incident || this.busy) {
      this.message = this.busy ? '已中断上一步并进入控制台（该任务保持暂停）'
        : '已结束本次验证并进入控制台（该任务保持暂停）';
    }
    this.enterBusy();
    try {
      await bounded(this.releaseAll());
      runtime.dispatching = false;
      runtime.incident = '';
      const link = await this.api.post<Link>('/v1/console-ticket');
      if (!this.foreground) { return; }
      this.browser = new webview.WebviewController();
      this.webUrl = link.url; this.screen = 'console'; this.showWeb = true;
    } catch (err) { this.error(err as Error); }
    finally { this.leaveBusy(); }
  }

  private text(value: string | number | undefined): string {
    if (typeof value === 'string') { return value; }
    if (typeof value === 'number') { return value.toString(); }
    return '';
  }

  // The hub descriptor and the node's live view both come from collector JSON;
  // compare normalised text so an int/string difference cannot hide a match.
  private same(left: string | number | undefined, right: string | number | undefined): boolean {
    const a = this.text(left);
    const b = this.text(right);
    return a.length > 0 && a === b;
  }

  private matchTarget(view: AuditView, expected: DirectTarget): DirectTarget {
    if (!this.same(view.instance_id, expected.instance_id) || !this.same(view.revision, expected.revision)) {
      throw new Error('节点任务配置已变更，请刷新待办');
    }
    for (const row of view.targets) {
      if (!this.same(row.job, expected.job) || !this.same(row.stock, expected.stock) ||
          !this.same(row.task, expected.task) || !this.same(row.request_id, expected.request_id)) { continue; }
      if (!row.route || !row.url) { throw new Error('节点未返回该任务的代理出口信息，请稍后重试'); }
      const target: DirectTarget = { instance_id: view.instance_id, revision: view.revision, job: row.job,
        stock: row.stock, task: row.task, request_id: row.request_id, url: row.url, route: row.route };
      return target;
    }
    throw new Error('原阻断已变化或解除，请刷新待办');
  }

  // Only the neutral endpoint is reachable before the exit check; the source
  // domains open after markReady(). The Web source is bound so the first load
  // happens on mount, later measurements navigate explicitly.
  private async measureExit(): Promise<string> {
    const measured = new Promise<string>((resolve) => { this.exitWaiter = resolve; });
    const guard = setTimeout(() => {
      const waiter = this.exitWaiter;
      if (waiter) { this.exitWaiter = undefined; waiter(''); }
    }, 45000);
    try {
      if (this.measuredOnce) { this.browser.loadUrl('https://api.ipify.org'); }
      return await measured;
    } finally { clearTimeout(guard); this.measuredOnce = true; }
  }

  private async verify(item: Incident): Promise<void> {
    // One probe per incident, never a second one: an incident that already has an
    // unconfirmed claim goes back to the result screen instead of verifying again.
    if (this.claim && this.claim.incident === item.id) {
      // Only the guidance line speaks here; a second instruction would compete with it.
      this.claimFailure = '';
      this.message = '';
      this.screen = 'result';
      return;
    }
    // A pending claim on another incident must not lock the app; only a verification
    // that is actually running does.
    const blocked = this.startBlocked(item);
    if (!this.api || blocked.length > 0) {
      if (blocked.length > 0) { this.message = blocked; }
      return;
    }
    this.enterBusy(); this.message = '正在读取该节点的验证信息';
    this.incidentId = item.id;
    this.stockTitle = item.name + ' · ' + item.stock;
    this.verifiedExit = false;
    this.measuredOnce = false;
    try {
      const descriptor = await this.api.request<VerificationDescriptor>(
        '/v1/incidents/' + item.id + '/verification', http.RequestMethod.GET, '', 3);
      this.descriptor = descriptor;
      this.loadBundled();
      const hint = descriptor.ssh_host_hint ? descriptor.ssh_host_hint : '';
      const bundled = this.bundledFor(descriptor.node_id, hint);
      let profile = await this.ssh.load(descriptor.node_id);
      // A packaged channel is authoritative for a node it covers: it ships both the
      // credential and the pinned host keys, so neither a stale stored profile nor a host
      // hint the phone cannot dial may win. Nothing has to be typed for a bundled node.
      if (bundled) {
        profile = await this.bundledProfileFor(descriptor.node_id, bundled);
        await this.ssh.save(profile);
      }
      if (!profile) {
        this.nodeHint = descriptor.ssh_host_hint
          ? '该节点 SSH 主机提示：' + descriptor.ssh_host_hint
          : '主控未提供该节点主机提示，请填写实际 SSH 地址';
        this.nodeHost = descriptor.ssh_host_hint ? descriptor.ssh_host_hint : '';
        this.nodeConfirmed = false;
        this.nodeFingerprint = '';
        this.message = '首次处理该节点，请填写该节点的 SSH 登录凭据（只保存在本机）';
        this.screen = 'node';
        return;
      }
      await this.begin(descriptor, profile);
    } catch (err) {
      // The operator may have taken the console instead, which deliberately ends this
      // flow; that screen must not be knocked back to the home screen.
      if (this.screen === 'console') { return; }
      await this.releaseAll();
      this.screen = 'home';
      this.error(err as Error);
    } finally { this.leaveBusy(); }
  }

  private async begin(descriptor: VerificationDescriptor, profile: NodeProfile): Promise<void> {
    runtime.incident = this.incidentId;
    runtime.nodeId = descriptor.node_id;
    runtime.descriptor = descriptor;
    runtime.profile = profile;
    this.activeProfile = profile;
    this.screen = 'transition';
    this.message = '正在连接节点并核对任务';
    const generation = ++this.generation;
    try {
      await this.detach();
      const options: SshConnectOptions = { host: profile.host, port: profile.port, user: profile.user,
        knownHostBase64: profile.knownHostBase64 };
      if (profile.authType === 'password') { options.password = profile.secret; } else { options.privateKey = profile.secret; }
      if (profile.passphrase) { options.passphrase = profile.passphrase; }
      const session = await connect(options);
      runtime.sshId = session.id;
      if (generation !== this.generation) { throw new Error('验证已取消'); }
      const view = await runAction<AuditView>(session.id, descriptor.worker_command, '{"action":"audit"}');
      const target = this.matchTarget(view, descriptor.target);
      runtime.target = target;
      if (generation !== this.generation) { throw new Error('验证已取消'); }
      this.message = '正在暂停该任务的后续采集';
      await runAction<PrepareValue>(session.id, descriptor.worker_command, JSON.stringify({ action: 'prepare', target: target }));
      if (generation !== this.generation) { throw new Error('验证已取消'); }
      this.message = '正在核对节点出口';
      const exitInfo = await runAction<ExitValue>(session.id, descriptor.worker_command,
        JSON.stringify({ action: 'exit', stock: target.stock, job: target.job }));
      if (!target.route || exitInfo.route.signature !== target.route.signature) {
        throw new Error('节点出口已变化，已停止验证');
      }
      target.exit_ip = exitInfo.exit_ip;
      runtime.target = target;
      if (generation !== this.generation) { throw new Error('验证已取消'); }
      this.message = '正在建立应用内 SSH 通道';
      const forwarding: SshForwardOptions = { host: profile.host, port: profile.port, user: profile.user,
        knownHostBase64: profile.knownHostBase64, remoteHost: target.route.host, remotePort: target.route.port };
      if (profile.authType === 'password') { forwarding.password = profile.secret; } else { forwarding.privateKey = profile.secret; }
      if (profile.passphrase) { forwarding.passphrase = profile.passphrase; }
      const tunnel = await forward(forwarding);
      runtime.forwardId = tunnel.id;
      this.message = '正在绑定本地转发端口';
      const endpoint = await runtime.proxy.start(tunnel.port, descriptor.source_domains);
      this.message = '正在应用浏览器代理';
      const config = new webview.ProxyConfig();
      config.insertProxyRule(endpoint);
      config.clearImplicitRules();
      this.proxyApplied = true;
      await bounded(new Promise<void>((resolve, reject) => {
        try { webview.ProxyController.applyProxyOverride(config, () => resolve()); }
        catch (_) { reject(new Error('此系统无法应用 ArkWeb 验证代理')); }
      }));
      // The channel is up. The rest only has to confirm the common exit and load the source
      // page, and it is split out so a weak link that fails here can be retried without
      // rebuilding the channel or touching the worker's task again.
      this.channelReady = true;
      await this.openWindow(target);
    } catch (err) {
      await this.holdForRetry(descriptor, profile, err as Error);
    }
  }

  // Shows the browser through the established tunnel, confirms the common exit, then hands
  // the source page to the operator.
  private async openWindow(target: DirectTarget): Promise<void> {
    const generation = this.generation;
    this.measuredOnce = false;
    this.verifiedExit = false;
    this.browser = new webview.WebviewController();
    this.webUrl = 'https://api.ipify.org';
    this.screen = 'verify'; this.showWeb = true;
    // From here until the page is taken down the operator is watching a captcha without
    // touching the screen, which is precisely the case the system heuristic does not cover.
    screenOn.hold(true);
    this.message = '正在核对手机与节点的共同出口';
    let ip = await this.measureExit();
    if (ip.length === 0) {
      // One bounded retry before failing the whole flow: on a weak link the neutral
      // page often needs a second attempt, and tearing the tunnel down for that is
      // expensive for the operator.
      this.message = '未能读取手机出口地址，正在重试';
      ip = await this.measureExit();
    }
    if (generation !== this.generation) { throw new Error('验证已取消'); }
    if (ip.length === 0) {
      // The tunnel may be up while the node's own proxy is degraded, which is a real
      // state (a throttled subscription serves placeholder nodes) and not a phone fault.
      throw new Error('未能读取手机出口地址；可能是节点代理不可用，可重试或进入控制台检查该节点');
    }
    if (ip !== target.exit_ip) { throw new Error('手机出口与节点出口不一致，已关闭通道'); }
    runtime.proxy.markReady();
    this.verifiedExit = true;
    this.message = '已确认共同出口，请在下方手动完成验证';
    const targetUrl = target.url ? target.url : 'https://api.ipify.org';
    hilog.info(0x3200, 'waygate_ui', 'showing target url=%{public}s', targetUrl);
    this.browser.loadUrl(targetUrl);
  }

  // Holds at the failing step instead of tearing everything down and dumping the operator on
  // the home screen: the worker's task stays paused exactly as prepare left it, nothing is
  // ever resent, and the screen offers retry, abandon and the console.
  private async holdForRetry(descriptor: VerificationDescriptor, profile: NodeProfile, err: Error): Promise<void> {
    // Taking the console or leaving for the inbox ends this flow on purpose, so a late
    // cancellation must not put the operator back on a retry screen.
    if (this.screen === 'console' || this.screen === 'home') { return; }
    runtime.incident = '';
    runtime.dispatching = false;
    if (this.channelReady) {
      // The tunnel and the browser proxy are still good, so only the Web is taken down; a
      // dead Web is what turned into a blank screen and swallowed the back key, and
      // rebuilding the channel would make the worker run its steps again for nothing.
      await bounded(this.detach());
    } else {
      // Nothing usable was established, so nothing may be left behind: a dead Web or a
      // proxy override pointing at a torn-down tunnel is exactly the blank-screen state.
      await this.removeProxy();
      await this.stopWindow();
      this.showWeb = false;
      this.proxyApplied = false;
    }
    this.retryDescriptor = descriptor;
    this.retryProfile = profile;
    this.failure = readableError(err.message || '验证未完成，请重试');
    this.message = this.failure;
    this.screen = 'retry';
  }

  // Retrying only the step that failed: when the channel is still up, the SSH session, the
  // tunnel and the proxy are reused as is and only the browser stage is re-entered, so a
  // weak link never pays for a second connect or a second worker prepare. Otherwise the
  // whole flow starts over.
  private async retryStep(): Promise<void> {
    const descriptor = this.retryDescriptor;
    const profile = this.retryProfile;
    if (!descriptor || !profile || this.busy) { return; }
    this.enterBusy();
    try {
      const target = runtime.target;
      if (this.channelReady && target) {
        await bounded(this.detach());
        await this.openWindow(target);
        return;
      }
      await this.removeProxy();
      await this.stopWindow();
      await runtime.releaseSsh();
      await this.begin(descriptor, profile);
    } catch (err) {
      await this.holdForRetry(descriptor, profile, err as Error);
    }
    finally { this.leaveBusy(); }
  }

  private async abandonRetry(): Promise<void> {
    this.retryDescriptor = undefined;
    this.retryProfile = undefined;
    // Leave the screen first; cleaning up afterwards must never delay navigation.
    this.showWeb = false;
    this.proxyApplied = false;
    this.screen = 'home';
    this.message = this.failure.length > 0 ? this.failure + '；已返回，任务保持暂停' : '';
    await bounded(this.releaseAll());
    runtime.dispatching = false;
    runtime.incident = '';
    await this.refresh();
  }

  private async pageEnded(url: string): Promise<void> {
    hilog.info(0x3200, 'waygate_ui', 'pageEnd %{public}s', url);
    if (this.exitWaiter && url.indexOf('https://api.ipify.org') === 0) {
      const waiter = this.exitWaiter;
      this.exitWaiter = undefined;
      try {
        const output = await this.browser.runJavaScript('JSON.stringify(document.body.innerText.trim())');
        let ip = JSON.parse(output) as string;
        // ArkWeb versions may JSON-encode the JS string result one extra time.
        if (ip.startsWith('"')) { ip = JSON.parse(ip) as string; }
        waiter(ip);
      } catch (_) { waiter(''); }
    }
  }

  private async waitProbeResult(command: string, target: DirectTarget, beforeId: number): Promise<string> {
    const deadline = Date.now() + 125000;
    while (Date.now() < deadline) {
      // Leaving the screen or backgrounding has to take effect now, not after this poll
      // finishes; the claim stays readable and nothing is ever resent.
      if (this.probeWaitAborted || !this.foreground) {
        throw new Error('已停止等待探测结果；没有再次请求，可在结果页重新核对');
      }
      const result = await runAction<ResultValue>(runtime.sshId, command,
        JSON.stringify({ action: 'result', target: target, before_request_id: beforeId }));
      if (result.done && result.request) { return result.request.outcome; }
      await new Promise<void>((resolve) => setTimeout(resolve, 2000));
    }
    throw new Error('等待时限内没有读到这次探测的结果');
  }

  private ageText(at: number): string {
    if (!at) { return '时间未知'; }
    const minutes = Math.floor((Date.now() - at) / 60000);
    if (minutes < 1) { return '刚刚'; }
    if (minutes < 60) { return minutes.toString() + ' 分钟前'; }
    const hours = Math.floor(minutes / 60);
    return hours.toString() + ' 小时前';
  }

  // The single source of truth for what the result screen tells the operator and which action
  // it offers. Nothing else may write probe guidance into the message bar.
  private claimGuidance(): string {
    const claim = this.claim;
    if (!claim || !runtime.dispatching) { return ''; }
    if (this.busy) { return '正在读取这次探测的结果，请稍候。没有重发请求。'; }
    if (this.claimFailure.length > 0) {
      return '上次核对没有成功：' + this.claimFailure + '。没有重发请求；可以再试一次，或结束核对。';
    }
    const age = this.ageText(claim.at);
    if (claim.before_request_id > 0) {
      return '这次探测已提交（' + age + '），结果尚未确认。没有重发请求；点“继续核对探测结果”只读取结果。';
    }
    return '这次探测没有记录到请求编号（' + age + '）。没有重发请求；点“按节点当前状态判定”从节点当前状态判断。';
  }

  // A claim is a record of one probe, not a lock on the app. Every way out of it goes through
  // here: the operator can close it without resending anything, and no other incident stays
  // disabled while it waits.
  private async endClaim(message: string): Promise<void> {
    this.claim = undefined;
    this.claimCanResume = false;
    this.claimFailure = '';
    this.claimAge = '';
    this.claimExpired = false;
    runtime.dispatching = false;
    this.message = message;
    await this.vault.claim('');
    await runtime.releaseSsh();
  }

  // When the probe's request id was never recorded, the node's own live view still answers the
  // question: a halt that is no longer reported for the same job and stock means the probe took
  // effect, while one that is still reported means it did not. Either way the claim is closed,
  // so the state cannot stay "unknown" forever.
  private async resolveClaimByState(): Promise<void> {
    if (!this.api || !this.claim || this.busy) { return; }
    const claim = this.claim;
    const target = JSON.parse(claim.target) as DirectTarget;
    this.enterBusy();
    this.message = '正在按节点当前状态判定这次探测';
    try {
      let profile = runtime.profile;
      if (!profile) { profile = await this.ssh.load(claim.node); }
      if (!profile) { throw new Error('该节点凭据已被删除；请到控制台核对'); }
      const options: SshConnectOptions = { host: profile.host, port: profile.port, user: profile.user,
        knownHostBase64: profile.knownHostBase64 };
      if (profile.authType === 'password') { options.password = profile.secret; } else { options.privateKey = profile.secret; }
      if (profile.passphrase) { options.passphrase = profile.passphrase; }
      const session = await connect(options);
      runtime.sshId = session.id;
      const view = await runAction<AuditView>(session.id, claim.worker_command, '{"action":"audit"}');
      const sameTask = view.targets.find((row: DirectTarget): boolean =>
        this.same(row.job, target.job) && this.same(row.stock, target.stock) && this.same(row.task, target.task));
      if (sameTask) {
        await this.endClaim('节点仍报告同一阻断：这次探测没有生效；任务保持暂停，可从待办重新处理');
      } else {
        await this.endClaim('节点已不再报告该阻断：探测已生效或任务已在别处继续；无需再核对');
      }
      await this.refresh();
    } catch (err) {
      this.claimFailure = '按节点状态判定失败：' + (err as Error).message;
      this.message = '';
      await runtime.releaseSsh();
    } finally { this.leaveBusy(); }
  }

  // Which incidents may be started right now. A claim only holds its own incident, so an
  // unconfirmed probe never disables the rest of the inbox.
  private startBlocked(item: Incident): string {
    if (this.busy) { return '正在处理上一步'; }
    if (item.kind === 'challenge' && !item.can_verify) { return '节点离线或状态过期'; }
    if (runtime.incident === item.id && !runtime.dispatching) { return '该待办正在验证中'; }
    if (this.claim && this.claim.incident === item.id && !runtime.dispatching) {
      return '该待办已有一次探测结果待核对';
    }
    return '';
  }

  private async settleClaim(message: string): Promise<void> {
    runtime.dispatching = false;
    runtime.incident = '';
    runtime.target = undefined;
    runtime.descriptor = undefined;
    runtime.profile = undefined;
    this.claim = undefined;
    this.claimCanResume = false;
    this.claimFailure = '';
    await this.vault.claim('');
    await runtime.releaseSsh();
    this.message = message;
  }

  private async finish(): Promise<void> {
    if (!this.api || !runtime.incident || !this.verifiedExit || this.busy || !this.descriptor || !runtime.target) { return; }
    const descriptor = this.descriptor;
    const target = runtime.target;
    this.enterBusy();
    this.message = '正在复核共同出口';
    try {
      const ip = await this.measureExit();
      if (ip.length === 0 || ip !== target.exit_ip) { throw new Error('手机浏览器出口与节点出口不一致，未提交探测'); }
      // Durable claim precedes the single remote mutation; a restart cannot resend it.
      const claim: DirectClaim = { incident: runtime.incident, node: descriptor.node_id,
        target: JSON.stringify(target), worker_command: descriptor.worker_command, before_request_id: 0, at: Date.now() };
      await this.vault.claim(JSON.stringify(claim));
      this.claim = claim;
      runtime.dispatching = true;
      this.claimCanResume = false;
      this.claimFailure = '';
      this.screen = 'result';
      await this.stopWindow();
      await runtime.stopForwarding();
      this.message = '';
      const queued = await runAction<ProbeValue>(runtime.sshId, descriptor.worker_command,
        JSON.stringify({ action: 'probe', target: target }));
      claim.before_request_id = queued.before_request_id;
      claim.at = Date.now();
      await this.vault.claim(JSON.stringify(claim));
      this.claimCanResume = true;
      this.probeWaitAborted = false;
      const outcome = await this.waitProbeResult(descriptor.worker_command, target, queued.before_request_id);
      if (outcome !== 'real_data' && outcome !== 'detail_unavailable') {
        throw new Error('探测未通过，任务保持暂停');
      }
      await runAction<ResumeValue>(runtime.sshId, descriptor.worker_command,
        JSON.stringify({ action: 'resume', target: target }));
      await this.settleClaim('验证通过，已继续 ' + target.stock);
      await this.refresh();
    } catch (err) {
      if (runtime.dispatching) {
        this.screen = 'result';
        this.claimFailure = (err as Error).message;
        this.message = '';
        await this.stopWindow();
        await runtime.stopForwarding();
        await runtime.releaseSsh();
      } else {
        await this.releaseAll();
        this.screen = 'home';
        this.error(err as Error);
      }
    } finally { this.leaveBusy(); }
  }

  // Read-only follow-up after a restart or disconnect: only the original probe's
  // result is queried, and resume still goes through the worker's own guards.
  private async resumeClaim(): Promise<void> {
    if (!this.api || this.busy) { return; }
    const claim = this.claim;
    if (!claim) { return; }
    if (claim.before_request_id <= 0) {
      // claimGuidance() already names the one available check for this state.
      this.message = '';
      return;
    }
    this.claimAge = this.ageText(claim.at);
    this.claimExpired = Date.now() - claim.at > CLAIM_TTL_MS;
    const target = JSON.parse(claim.target) as DirectTarget;
    this.enterBusy();
    this.message = '正在连接节点核对探测结果';
    try {
      const profile = await this.ssh.load(claim.node);
      if (!profile) { throw new Error('该节点凭据已被删除，无法核对；请到控制台核对'); }
      const options: SshConnectOptions = { host: profile.host, port: profile.port, user: profile.user,
        knownHostBase64: profile.knownHostBase64 };
      if (profile.authType === 'password') { options.password = profile.secret; } else { options.privateKey = profile.secret; }
      if (profile.passphrase) { options.passphrase = profile.passphrase; }
      const session = await connect(options);
      runtime.sshId = session.id;
      this.probeWaitAborted = false;
      const outcome = await this.waitProbeResult(claim.worker_command, target, claim.before_request_id);
      if (outcome !== 'real_data' && outcome !== 'detail_unavailable') {
        throw new Error('探测未通过，任务保持暂停');
      }
      await runAction<ResumeValue>(runtime.sshId, claim.worker_command,
        JSON.stringify({ action: 'resume', target: target }));
      await this.settleClaim('验证通过，已继续 ' + target.stock);
      await this.refresh();
    } catch (err) {
      this.claimFailure = (err as Error).message;
      this.message = '';
      await runtime.releaseSsh();
    } finally { this.leaveBusy(); }
  }

  // Channel configuration packaged with the build: endpoints, pinned host keys and
  // the credential files, which are git-ignored and therefore only present in local
  // builds. Absent files simply leave the manual form in place.
  private loadBundled(): void {
    if (this.bundledChecked) { return; }
    this.bundledChecked = true;
    try {
      const manager = this.context().resourceManager;
      const config = JSON.parse(new util.TextDecoder().decodeToString(manager.getRawFileContentSync('channel.json'))) as BuiltinConfig;
      this.bundledNodes = config.nodes;
      for (const node of config.nodes) {
        if (this.bundledSecrets.has(node.secretFile)) { continue; }
        try {
          this.bundledSecrets.set(node.secretFile, new util.TextDecoder().decodeToString(manager.getRawFileContentSync(node.secretFile)).trim());
        } catch (_) { this.bundledFailed = '本构建缺少内置凭据文件：' + node.secretFile; }
      }
    } catch (_) { this.bundledNodes = []; }
  }

  private secretFor(node: BuiltinNode): string {
    const stored = this.bundledSecrets.get(node.secretFile);
    return stored ? stored : '';
  }

  // A packaged channel is found by the hub's node_id first: it is stable, and it is the
  // same key a stored profile uses. The host hint is only a fallback, because the hub can
  // advertise a host the phone cannot dial (its own domain, or the node's LAN address).
  private bundledFor(nodeId: string, hint: string): BuiltinNode | undefined {
    const usable = this.bundledNodes.filter((node: BuiltinNode): boolean => this.secretFor(node).length > 0);
    const byId = usable.find((node: BuiltinNode): boolean =>
      node.nodeIds !== undefined && node.nodeIds.indexOf(nodeId) >= 0);
    if (byId) { return byId; }
    if (hint.length === 0) { return undefined; }
    return usable.find((node: BuiltinNode): boolean =>
      node.host === hint || (node.aliases !== undefined && node.aliases.indexOf(hint) >= 0));
  }

  // The channel screen only has a typed host, so it matches on host/alias alone.
  private bundledMatches(host: string): boolean {
    return this.bundledByHost(host) !== undefined;
  }

  private bundledByHost(host: string): BuiltinNode | undefined {
    return this.bundledNodes.find((node: BuiltinNode): boolean => this.secretFor(node).length > 0 &&
      (node.host === host || (node.aliases !== undefined && node.aliases.indexOf(host) >= 0)));
  }

  // Probes first so the pinned host keys from the packaged config are verified
  // before any bundled credential is used.
  private async bundledProfileFor(nodeId: string, node: BuiltinNode): Promise<NodeProfile> {
    const host = node.host;
    const key = await probeHostKey({ host: host, port: node.port });
    const fingerprint = key.type + ' ' + key.fingerprint;
    const accepted = node.hostKeys;
    if (accepted.length > 0 && !accepted.some((pinned: string) => pinned === fingerprint)) {
      throw new Error('节点主机指纹与内置配置不一致，已拒绝连接（本次协商到 ' + fingerprint + '）');
    }
    return { nodeId: nodeId, host: host, port: node.port, user: node.user, authType: node.authType,
      secret: this.secretFor(node), passphrase: '', knownHostBase64: key.base64,
      knownHostType: key.type, fingerprint: fingerprint };
  }

  private pickNode(node: BuiltinNode): void {
    this.nodeHost = node.host;
    this.nodePort = node.port.toString();
    this.nodeUser = node.user;
    this.nodeAuth = node.authType;
    this.nodeFingerprint = '';
    this.nodeConfirmed = false;
    this.channelOutput = '';
    this.nodeHint = '内置通道：' + node.label + '（配置随本构建打包，无需在手机上填写）';
  }

  private nodeActionLabel(): string {
    if (this.busy) { return '正在读取…'; }
    if (this.bundledMatches(this.nodeHost.trim())) { return '建立通道（内置凭据）'; }
    if (this.channelMode) { return this.nodeFingerprint ? '确认指纹并建立通道' : '读取指纹'; }
    return this.nodeFingerprint ? '确认指纹并连接' : '读取指纹';
  }

  // Opens the node form without any hub involvement, so the phone-to-node SSH
  // channel can be established and proven on device by itself.
  private openChannel(): void {
    if (this.busy || runtime.incident) { return; }
    this.loadBundled();
    this.channelMode = true;
    this.channelOutput = '';
    this.nodeHost = '';
    this.nodePort = '22';
    this.nodeUser = 'root';
    this.nodeAuth = 'password';
    this.nodeSecret = '';
    this.nodeKey = '';
    this.nodePassphrase = '';
    this.nodeFingerprint = '';
    this.nodeConfirmed = false;
    this.message = '';
    const node = this.bundledNodes.length > 0 ? this.bundledNodes[0] : undefined;
    if (node) {
      this.pickNode(node);
    } else {
      this.nodeHint = '直接建立到该节点的 SSH 通道，不经过主控，也不使用待办信息。';
    }
    this.screen = 'node';
  }

  // Connects and runs one read-only command. Nothing here can pause collection or
  // issue a source request, so it is safe before the full flow is unblocked.
  private async channelConnect(profile: NodeProfile): Promise<void> {
    this.message = '正在建立 SSH 通道并执行只读命令';
    try {
      const options: SshConnectOptions = { host: profile.host, port: profile.port, user: profile.user,
        knownHostBase64: profile.knownHostBase64 };
      if (profile.authType === 'password') { options.password = profile.secret; } else { options.privateKey = profile.secret; }
      if (profile.passphrase) { options.passphrase = profile.passphrase; }
      const session = await connect(options);
      try {
        const result = await exec(session.id, 'id; hostname; echo SSH_CONNECTION=$SSH_CONNECTION', '');
        const stderr = result.stderr.trim();
        this.channelOutput = '通道已建立，主机指纹已核对\n退出码：' + result.exitStatus.toString() +
          '\n--- stdout ---\n' + result.stdout.trim() + (stderr ? '\n--- stderr ---\n' + stderr : '');
        this.message = 'SSH 通道可用';
      } finally {
        await close(session.id).catch(() => {});
      }
    } catch (err) { this.error(err as Error); }
    finally { this.leaveBusy(); }
  }

  private async saveNode(): Promise<void> {
    const descriptor = this.descriptor;
    if (this.busy) { return; }
    if (!this.channelMode && !descriptor) { return; }
    const host = this.nodeHost.trim();
    const portText = this.nodePort.trim();
    const user = this.nodeUser.trim();
    if (!/^[A-Za-z0-9][A-Za-z0-9.:\-\[\]]*$/.test(host)) { this.message = '请填写节点 SSH 地址（域名或 IP）'; return; }
    if (!/^[0-9]{1,5}$/.test(portText)) { this.message = 'SSH 端口需为 1-65535 的数字'; return; }
    const port = Number(portText);
    if (port < 1 || port > 65535) { this.message = 'SSH 端口需为 1-65535'; return; }
    if (!/^[A-Za-z0-9_]+$/.test(user)) { this.message = 'SSH 用户名不合法'; return; }
    // Packaged channel: nothing is typed on the phone; the pinned host key from
    // the bundled config is verified inside bundledProfileFor.
    const bundledNode = this.bundledByHost(host);
    const bundled = bundledNode !== undefined;
    const secret = this.nodeAuth === 'password' ? this.nodeSecret : this.nodeKey.trim();
    if (!bundled && secret.length < 8) {
      this.message = this.nodeAuth === 'password' ? '请填写该节点的登录密码' : '请粘贴该节点的私钥内容';
      return;
    }
    this.enterBusy();
    this.message = bundled ? '正在核对内置通道的主机指纹' : (this.nodeConfirmed ? '正在连接节点并建立通道' : '正在读取节点主机指纹…');
    try {
      let profile: NodeProfile;
      if (bundledNode) {
        const nodeId = this.channelMode ? 'channel-test' : (descriptor ? descriptor.node_id : '');
        profile = await this.bundledProfileFor(nodeId, bundledNode);
        this.nodeFingerprint = profile.fingerprint;
      } else {
        const key = await probeHostKey({ host: host, port: port });
        const fingerprint = key.type + ' ' + key.fingerprint;
        if (!this.nodeConfirmed || this.nodeFingerprint !== fingerprint) {
          this.nodeFingerprint = fingerprint;
          this.nodeConfirmed = false;
          this.message = '请核对该指纹与该节点实际一致，再点“' + this.nodeActionLabel() + '”';
          return;
        }
        profile = { nodeId: this.channelMode ? 'channel-test' : (descriptor ? descriptor.node_id : ''),
          host: host, port: port, user: user, authType: this.nodeAuth, secret: secret,
          passphrase: this.nodePassphrase, knownHostBase64: key.base64, knownHostType: key.type,
          fingerprint: fingerprint };
      }
      if (this.channelMode) {
        await this.channelConnect(profile);
      } else if (descriptor) {
        await this.ssh.save(profile);
        await this.begin(descriptor, profile);
      }
    } catch (err) { this.error(err as Error); }
    finally { this.leaveBusy(); }
  }

  private async enablePush(ask: boolean): Promise<void> {
    if (!this.api) { return; }
    try {
      await registerPush(this.context(), this.api, ask);
      this.credentials.push = true;
      await this.vault.save(this.credentials);
      this.notification = '通知已登记';
    } catch (_) { this.notification = '通知未就绪：请检查系统权限、签名和主控 Push Kit 配置'; }
  }

  private async snooze(item: Incident): Promise<void> {
    if (!this.api) { return; }
    try {
      await this.api.post<EmptyReply>('/v1/incidents/' + item.id + '/snooze');
      this.message = '一小时后提醒，采集保持原状态'; await this.refresh();
    } catch (err) { this.error(err as Error); }
  }

  private stateLabel(state: string): string {
    if (state === 'running') { return '采集中'; }
    if (state === 'completed') { return '已结束'; }
    if (state === 'blocked') { return '需处理'; }
    if (state === 'paused') { return '已暂停'; }
    return state;
  }

  // The system back key must work on every screen: it is easy for a live Web component
  // to swallow it, and silently doing nothing leaves the operator stuck. Each branch
  // routes to the same action as the on-screen button, and leaving mid-flight
  // invalidates the running flow at its next checkpoint while the worker's task stays
  // paused and the phone-side resources are released.
  onBackPress(): boolean {
    // Evidence first: if this line never appears for a real back gesture, the key never
    // reaches the page, and the fix has to be at the Web/ability layer, not here.
    hilog.info(0x3200, 'waygate_ui', 'backPressed screen=%{public}s web=%{public}s busy=%{public}s',
      this.screen, this.showWeb ? 'yes' : 'no', this.busy ? 'yes' : 'no');
    return this.navigateBack();
  }

  @Builder
  header() {
    Row() {
      Text('Waygate').fontSize(27).fontWeight(FontWeight.Bold).fontColor(INK).layoutWeight(1)
      if (this.screen === 'home') {
        // The poll backs off when nothing moves, so an explicit refresh stays available.
        Button('刷新').backgroundColor(Color.Transparent).fontColor(MUTED)
          .onClick(() => this.scheduleRefresh(true))
        Button('诊断').backgroundColor(Color.Transparent).fontColor(MUTED)
          .onClick(() => this.openDiagnostics())
        Button('设置').backgroundColor(Color.Transparent).fontColor(MUTED)
          .onClick(() => { this.screen = 'settings'; })
      } else if (this.api) {
        Button('返回').backgroundColor(Color.Transparent).fontColor(INK)
          .onClick(() => { this.navigateBack(); })
      }
    }.width('100%').padding({ left: 22, right: 12, top: 14, bottom: 12 })
  }

  @Builder
  home() {
    Scroll() {
      Column({ space: 16 }) {
        Row() {
          Text('需要处理').fontSize(22).fontWeight(FontWeight.Bold).fontColor(INK).layoutWeight(1)
          Text(this.items.length.toString()).fontSize(18).fontColor(MUTED)
        }.width('100%')
        if (this.items.length === 0) {
          Text('暂无待处理事项').width('100%').padding(24).backgroundColor(Color.White).borderRadius(18).fontColor(MUTED)
        }
        ForEach(this.items, (item: Incident) => {
          Column({ space: 12 }) {
            Text(item.name + ' · ' + item.stock).width('100%').fontSize(19).fontWeight(FontWeight.Bold).fontColor(INK)
            Text(item.online ? item.title : '节点暂时离线 · 阻断待核对').width('100%').fontColor(MUTED)
            if (item.snoozed > Date.now() / 1000) { Text('已设稍后提醒').width('100%').fontSize(12).fontColor(MUTED) }
            Row({ space: 10 }) {
              Button(item.kind === 'challenge' ? '现在处理' : '查看控制台')
                .backgroundColor(INK).fontColor(Color.White).layoutWeight(1)
                .enabled(this.startBlocked(item).length === 0)
                .onClick(() => { if (item.kind === 'challenge') { this.verify(item); } else { this.openConsole(); } })
              Button('稍后提醒').backgroundColor(BG).fontColor(INK).onClick(() => this.snooze(item))
            }
          }.padding(20).width('100%').backgroundColor(Color.White).borderRadius(18)
        }, (item: Incident) => item.id)
        Button('打开采集控制台').width('100%').height(50).fontColor(INK).backgroundColor('#DDECE4')
          .onClick(() => this.openConsole())
        Text('任务进度').width('100%').fontSize(20).fontWeight(FontWeight.Bold).fontColor(INK)
        ForEach(this.nodes, (node: NodeState) => {
          Column({ space: 10 }) {
            Text(node.name + (node.online ? '' : ' · 离线')).width('100%').fontWeight(FontWeight.Bold).fontColor(INK)
            ForEach(node.stocks, (stock: StockState) => {
              Column({ space: 6 }) {
                Row() {
                  Text(stock.stock).layoutWeight(1).fontColor(INK)
                  Text(this.stateLabel(stock.state)).fontColor(MUTED)
                }.width('100%')
                Text('窗口内 ' + stock.posts + ' 帖 · 待补正文 ' + stock.pending).width('100%').fontSize(13).fontColor(MUTED)
                Text(stock.window).width('100%').fontSize(12).fontColor(MUTED)
              }.width('100%')
            }, (stock: StockState) => stock.stock)
          }.width('100%').padding(20).backgroundColor(Color.White).borderRadius(18)
        }, (node: NodeState) => node.id)
        Text(this.notification).width('100%').fontColor(MUTED).fontSize(12)
      }.width('100%').padding(20)
    }.layoutWeight(1).scrollBar(BarState.Off)
  }

  build() {
    Column() {
      this.header()
      if (this.message) {
        Text(this.message).fontSize(14).fontColor(INK).width('100%').padding({ left: 22, right: 22, bottom: 12 })
      }
      if (this.otherNodeNotes.length > 0) {
        Text(this.showOtherNotes ? '收起其他节点消息' : '其他节点 · ' + this.otherNodeNotes.length + ' 条历史')
          .fontSize(12).fontColor(MUTED).width('100%').padding({ left: 22, right: 22, bottom: 8 })
          .onClick(() => { this.showOtherNotes = !this.showOtherNotes; })
        if (this.showOtherNotes) {
          ForEach(this.otherNodeNotes, (note: string) => {
            Text(note).fontSize(12).fontColor(MUTED).width('100%').padding({ left: 22, right: 22, bottom: 6 })
          }, (note: string, index: number): string => index.toString())
        }
      }
      if (this.syncMessage) {
        Text(this.syncMessage).fontSize(14).fontColor(INK).width('100%').padding({ left: 22, right: 22, bottom: 12 })
      }
      if (this.screen === 'settings') {
        Column({ space: 18 }) {
          Text('连接你的主控').fontSize(23).fontWeight(FontWeight.Bold).fontColor(INK).width('100%')
          TextInput({ text: this.hub, placeholder: 'https://你的域名/waygate' }).onChange((v: string) => this.hub = v)
          TextInput({ text: this.token, placeholder: '主控 console.token' }).type(InputType.Password).onChange((v: string) => this.token = v)
          Button(this.busy ? '正在连接…' : '连接并保存').width('100%').backgroundColor(INK)
            .opacity(this.busy ? 0.6 : 1).enabled(!this.busy).onClick(() => this.save())
          if (this.busy) {
            Row({ space: 8 }) {
              LoadingProgress().width(18).height(18).color(INK)
              Text('正在连接主控并校验令牌…').fontColor(MUTED).fontSize(13)
            }.width('100%').justifyContent(FlexAlign.Center)
          }
          if (this.api) {
            Button('开启任务通知').width('100%').backgroundColor('#DDECE4').fontColor(INK).onClick(() => this.enablePush(true))
            Text(this.notification).fontColor(MUTED).fontSize(13)
          }
          Button('SSH 通道测试（手机 → 节点）').width('100%').height(46).backgroundColor('#DDECE4').fontColor(INK)
            .enabled(!this.busy && !runtime.incident).onClick(() => this.openChannel())
          Text('不依赖主控：只建立到节点的 SSH 通道并执行一条只读命令，不暂停采集、不发起来源请求。')
            .fontColor(MUTED).fontSize(12).width('100%')
          Text('节点 SSH 凭据由手机在首次处理时导入并保存在本机，不需要主控登录节点。').fontColor(MUTED).fontSize(14)
        }.padding(24).width('100%')
      } else if (this.screen === 'node') {
        Scroll() {
          Column({ space: 14 }) {
            Text(this.channelMode ? 'SSH 通道测试' : '节点 SSH 凭据').fontSize(23).fontWeight(FontWeight.Bold).fontColor(INK).width('100%')
            Text(this.nodeHint).fontColor(MUTED).fontSize(13).width('100%')
            if (this.channelMode && this.bundledNodes.length > 1) {
              Row({ space: 8 }) {
                ForEach(this.bundledNodes, (node: BuiltinNode) => {
                  Button(node.id).fontSize(13).height(38).layoutWeight(1)
                    .backgroundColor(this.nodeHost === node.host ? INK : BG)
                    .fontColor(this.nodeHost === node.host ? Color.White : INK)
                    .onClick(() => this.pickNode(node))
                }, (node: BuiltinNode) => node.id)
              }.width('100%')
            }
            TextInput({ text: this.nodeHost, placeholder: '节点 SSH 地址（域名或 IP）' })
              .onChange((v: string) => { this.nodeHost = v; this.nodeFingerprint = ''; this.nodeConfirmed = false; })
            TextInput({ text: this.nodePort, placeholder: 'SSH 端口（默认 22）' }).type(InputType.Number)
              .onChange((v: string) => this.nodePort = v)
            TextInput({ text: this.nodeUser, placeholder: 'SSH 用户（默认 root）' })
              .onChange((v: string) => { this.nodeUser = v; this.nodeFingerprint = ''; this.nodeConfirmed = false; })
            if (!this.bundledMatches(this.nodeHost.trim())) {
              Row({ space: 10 }) {
                Button('密码').layoutWeight(1).fontColor(this.nodeAuth === 'password' ? Color.White : INK)
                  .backgroundColor(this.nodeAuth === 'password' ? INK : BG).onClick(() => this.nodeAuth = 'password')
                Button('私钥').layoutWeight(1).fontColor(this.nodeAuth === 'key' ? Color.White : INK)
                  .backgroundColor(this.nodeAuth === 'key' ? INK : BG).onClick(() => this.nodeAuth = 'key')
              }.width('100%')
              if (this.bundledFailed) {
                Text(this.bundledFailed).fontColor(MUTED).fontSize(12).width('100%')
              }
              if (this.nodeAuth === 'password') {
                TextInput({ text: this.nodeSecret, placeholder: '该节点的登录密码' }).type(InputType.Password)
                  .onChange((v: string) => this.nodeSecret = v)
              } else {
                TextArea({ text: this.nodeKey, placeholder: '粘贴该节点可用的私钥内容' }).height(120)
                  .onChange((v: string) => this.nodeKey = v)
                TextInput({ text: this.nodePassphrase, placeholder: '私钥口令（无则留空）' }).type(InputType.Password)
                  .onChange((v: string) => this.nodePassphrase = v)
              }
            } else {
              Text('使用本构建内置的凭据，无需在手机上输入；主机指纹已按内置配置固定校验。')
                .fontColor(MUTED).fontSize(12).width('100%')
            }
            if (this.nodeFingerprint) {
              Text((this.bundledMatches(this.nodeHost.trim()) ? '主机指纹（已与内置配置比对通过）\n' : '主机指纹（请与该节点实际指纹核对）\n') + this.nodeFingerprint)
                .fontSize(13).fontColor(INK).width('100%').copyOption(CopyOptions.LocalDevice)
            }
            Button(this.nodeActionLabel())
              .width('100%').backgroundColor(INK).opacity(this.busy ? 0.6 : 1).enabled(!this.busy)
              .onClick(() => { this.nodeConfirmed = this.nodeFingerprint.length > 0; this.saveNode(); })
            if (this.channelMode && this.channelOutput) {
              Text(this.channelOutput).fontSize(13).fontColor(INK).width('100%').padding(12)
                .backgroundColor('#EAEFEC').borderRadius(12).copyOption(CopyOptions.LocalDevice)
            }
            Text(this.channelMode
              ? '通道测试只执行 id/hostname/SSH_CONNECTION 这条只读命令；不暂停采集、不发起来源请求。凭据只在本机内存中使用，不写日志。'
              : '凭据只保存在手机的凭据存储中，不上传主控、不写入日志。指纹不一致会拒绝连接。')
              .fontColor(MUTED).fontSize(12).width('100%')
          }.padding(22).width('100%')
        }.layoutWeight(1).scrollBar(BarState.Off)
      } else if (this.screen === 'home') {
        this.home()
      } else if (this.screen === 'diagnostics') {
        Scroll() {
          Column({ space: 16 }) {
            Text('连接诊断').fontSize(22).fontWeight(FontWeight.Bold).fontColor(INK).width('100%')
            Text('长按下方文字可复制').fontColor(MUTED).fontSize(13).width('100%')
            Text(this.diagnosticSnapshot).fontSize(14).fontColor(INK).width('100%')
              .copyOption(CopyOptions.LocalDevice)
            Button('刷新诊断').backgroundColor('#DDECE4').fontColor(INK).onClick(() => this.openDiagnostics())
          }.width('100%').padding(22)
        }.layoutWeight(1).width('100%')
      } else if (this.screen === 'verify' || this.screen === 'console') {
        if (this.screen === 'verify') { Text(this.stockTitle).fontColor(INK).padding(8) }
      } else {
        Column({ space: 20 }) {
          if ((this.screen !== 'result' && this.screen !== 'retry') || this.busy) {
            LoadingProgress().width(44).height(44).color(INK)
          }
          if (this.screen === 'retry') {
            // The failing step is held, not swallowed: retry it, abandon it, or walk to
            // the console. Nothing is ever resent, so retrying is safe.
            Text(this.failure).fontSize(14).fontColor(INK).width('100%')
              .padding({ left: 30, right: 30 }).textAlign(TextAlign.Center)
            Button(this.busy ? '正在重试…' : '重试本步').backgroundColor(INK)
              .opacity(this.busy ? 0.6 : 1).enabled(!this.busy).onClick(() => this.retryStep())
            Button('放弃并返回待办').backgroundColor('#DDECE4').fontColor(INK).onClick(() => this.abandonRetry())
            Button('进入控制台').backgroundColor('#DDECE4').fontColor(INK).onClick(() => this.openConsole())
          }
          if (this.screen === 'result' && this.claimGuidance().length > 0) {
            Text(this.claimGuidance()).fontSize(14).fontColor(INK).width('100%')
              .padding({ left: 30, right: 30 }).textAlign(TextAlign.Center)
          }
          if (this.screen === 'result' && runtime.dispatching && this.claimCanResume) {
            Button(this.busy ? '正在核对…' : '核对探测结果').backgroundColor(INK)
              .opacity(this.busy ? 0.6 : 1).enabled(!this.busy).onClick(() => this.resumeClaim())
          }
          if (this.screen === 'result' && runtime.dispatching) {
            // The claim is never a dead end: it can be decided from the node's live view, or
            // closed outright. Neither path resends the probe.
            Button(this.busy ? '正在判定…' : '按节点当前状态判定').backgroundColor('#DDECE4').fontColor(INK)
              .opacity(this.busy ? 0.6 : 1).enabled(!this.busy).onClick(() => this.resolveClaimByState())
            Button('结束核对（任务保持暂停）').backgroundColor('#EAEFEC').fontColor(INK)
              .enabled(!this.busy)
              .onClick(() => this.endClaim('已结束本次核对；任务保持暂停，可从待办重新处理或到控制台查看'))
          }
          if (this.screen === 'result' && !runtime.dispatching) {
            Button('返回待办').backgroundColor('#DDECE4').fontColor(INK).onClick(() => this.leaveResult())
          }
          if (this.screen === 'result') {
            Button('进入控制台').backgroundColor('#DDECE4').fontColor(INK).onClick(() => this.openConsole())
          }
        }.layoutWeight(1).justifyContent(FlexAlign.Center)
      }
      if (this.showWeb) {
        Web({ src: this.webUrl, controller: this.browser })
          .javaScriptAccess(true).domStorageAccess(true)
          .mixedMode(MixedMode.None).fileAccess(false)
          .onPageEnd((event) => { if (event) { this.pageEnded(event.url); } })
          .onErrorReceive((event) => {
            if (event && event.request.isMainFrame()) { this.message = '页面未加载：' + event.error.getErrorInfo(); }
          })
          .onSslErrorEventReceive((event) => { if (event) { event.handler.handleCancel(); } })
          .onDisAppear(() => {
            if (this.destroyWeb) { const done = this.destroyWeb; this.destroyWeb = undefined; done(); }
          })
          .layoutWeight(1).width('100%')
      }
      if (this.screen === 'verify') {
        Row({ space: 12 }) {
          Button('取消').fontColor(INK).backgroundColor('#DDECE4').onClick(() => this.goHome())
          Button('控制台').fontColor(INK).backgroundColor('#DDECE4').onClick(() => this.openConsole())
          Button('验证完成并继续').layoutWeight(1).backgroundColor(INK)
            .enabled(this.verifiedExit && !this.busy).onClick(() => this.finish())
        }.padding(16).width('100%')
      }
    }.width('100%').height('100%').backgroundColor(BG)
  }
}
