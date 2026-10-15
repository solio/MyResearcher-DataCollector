import { webview } from '@kit.ArkWeb';
import { common } from '@kit.AbilityKit';
import { http } from '@kit.NetworkKit';
import { Api, readableError } from '../services/Api';
import { Vault } from '../services/Vault';
import { registerPush } from '../services/Push';
import { runtime } from '../services/SessionRuntime';
import { AuditView, Credentials, DirectClaim, DirectTarget, EmptyReply, ExitValue, Inbox, Incident, Link, NodeProfile, NodeState, PrepareValue, ProbeValue, ResultValue, ResumeValue, StockState, VerificationDescriptor } from '../services/Models';
import { NodeSsh, runAction } from '../services/NodeSsh';
import { close, connect, exec, forward, probeHostKey, SshConnectOptions, SshForwardOptions } from 'libwaygate_ssh.so';

const INK = '#173E37';
const MUTED = '#657970';
const BG = '#F3F6F5';

@Entry
@Component
struct Index {
  @State screen: string = 'loading';
  @State items: Incident[] = [];
  @State nodes: NodeState[] = [];
  @State message: string = '';
  @State syncMessage: string = '';
  @State diagnosticSnapshot: string = '';
  @State busy: boolean = false;
  @State hub: string = '';
  @State token: string = '';
  @State notification: string = '通知尚未开启';
  @State showWeb: boolean = false;
  @State webUrl: string = 'about:blank';
  @State verifiedExit: boolean = false;
  @State stockTitle: string = '';
  @State claimMessage: string = '';
  @State claimCanResume: boolean = false;
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
  private measuredOnce: boolean = false;
  private exitWaiter: ((ip: string) => void) | undefined = undefined;
  private timer: number = -1;
  private polling: boolean = false;
  private proxyApplied: boolean = false;
  private destroyWeb: (() => void) | undefined = undefined;
  private detachPending: Promise<void> | undefined = undefined;
  private cleanupPending: Promise<void> | undefined = undefined;
  private generation: number = 0;
  private lastRefreshAt: string = '';
  private lastCollectorSync: string = '';
  private lastSyncFailure: string = '';

  aboutToAppear(): void { this.initialize(); }
  aboutToDisappear(): void { clearInterval(this.timer); runtime.background(); }

  private context(): common.UIAbilityContext {
    return this.getUIContext().getHostContext() as common.UIAbilityContext;
  }

  private error(err: Error): void { this.message = readableError(err.message || '操作未完成，请重试连接'); }

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
          this.claimMessage = this.claimCanResume
            ? '上次探测已提交，结果尚未确认'
            : '上次未收到探测编号，结果未知';
          this.screen = 'result';
          this.message = this.claimCanResume ? '点击“继续核对探测结果”只读取结果，不会重发请求' : '请到控制台核对该任务状态';
        }
        await this.refresh();
        if (this.credentials.push) { this.enablePush(false); }
      } else { this.screen = 'settings'; }
    } catch (err) {
      this.error(err as Error);
      this.screen = this.api ? 'home' : 'settings';
    }
    this.timer = setInterval(() => { if (this.foreground) { this.refresh(); } }, 5000);
  }

  private async save(): Promise<void> {
    const address = this.hub.trim().replace(/\/+$/, '');
    if (!/^https:\/\/[^/?#@\s]+(?:\/[^?#\s]*)?$/.test(address)) {
      this.message = '请填写主控 HTTPS 地址，例如 https://你的域名/waygate'; return;
    }
    if (this.token.trim().length < 16 || this.token.trim().length > 1023) {
      this.message = '请填写主控的 console.token'; return;
    }
    this.busy = true;
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
    finally { this.busy = false; }
  }

  private async refresh(): Promise<void> {
    if (!this.api || this.polling || !this.foreground) { return; }
    this.polling = true;
    try {
      const feed = await this.api.request<Inbox>('/v1/inbox');
      this.items = feed.items; this.nodes = feed.nodes;
      this.lastCollectorSync = feed.last_poll > 0 ? new Date(feed.last_poll * 1000).toLocaleString() : '';
      if (feed.error) {
        this.syncFailed(feed.error, '接口：GET /v1/inbox\nHTTP：200\n阶段：服务端同步 collector');
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
    this.claimMessage = '';
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

  private visibilityChanged(): void {
    if (!this.foreground) {
      const open = this.showWeb || this.proxyApplied || runtime.incident.length > 0;
      if (open) {
        this.releaseAll().then(() => { this.screen = runtime.dispatching ? 'result' : 'home'; });
      } else { this.generation++; }
    } else { this.refresh(); }
  }

  private async detach(): Promise<void> {
    if (this.detachPending) { return this.detachPending; }
    if (!this.showWeb) { return; }
    this.browser.stop();
    this.detachPending = new Promise<void>((resolve) => {
      this.destroyWeb = resolve;
      this.showWeb = false;
    });
    await this.detachPending;
    this.detachPending = undefined;
  }

  private async removeProxy(): Promise<void> {
    if (!this.proxyApplied) { return; }
    await new Promise<void>((resolve, reject) => {
      try { webview.ProxyController.removeProxyOverride(() => resolve()); }
      catch (_) { reject(new Error('清理应用代理失败，请关闭应用后重新打开')); }
    });
    this.proxyApplied = false;
  }

  // Closes the temporary browser channel only. The SSH exec session is kept when a
  // probe is in flight so its single result can still be read.
  private async stopWindow(): Promise<void> {
    await runtime.proxy.stop(); // Immediately fail closed, even before Web teardown.
    await this.detach();
    await this.removeProxy();
    this.exitWaiter = undefined;
    this.verifiedExit = false;
  }

  // Releases every resource this phone created for the current verification.
  private async releaseAll(): Promise<void> {
    this.generation++;
    await this.stopWindow();
    await runtime.releaseSsh();
    runtime.incident = '';
    runtime.target = undefined;
  }

  private async goHome(): Promise<void> {
    try {
      await this.releaseAll();
      this.screen = runtime.dispatching ? 'result' : 'home';
      await this.refresh();
    } catch (err) { this.error(err as Error); }
  }

  private async leaveResult(): Promise<void> {
    try {
      await this.releaseAll();
      this.screen = 'home';
      await this.refresh();
    } catch (err) { this.error(err as Error); }
  }

  private async openConsole(): Promise<void> {
    if (!this.api || this.busy || runtime.dispatching) { return; }
    this.busy = true;
    try {
      await this.releaseAll();
      const link = await this.api.post<Link>('/v1/console-ticket');
      if (!this.foreground) { return; }
      this.browser = new webview.WebviewController();
      this.webUrl = link.url; this.screen = 'console'; this.showWeb = true;
    } catch (err) { this.error(err as Error); }
    finally { this.busy = false; }
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
    }, 25000);
    try {
      if (this.measuredOnce) { this.browser.loadUrl('https://api.ipify.org'); }
      return await measured;
    } finally { clearTimeout(guard); this.measuredOnce = true; }
  }

  private async verify(item: Incident): Promise<void> {
    if (!this.api || this.busy || runtime.incident || runtime.dispatching) {
      if (runtime.dispatching) { this.message = '请先核对上次的探测结果'; }
      return;
    }
    this.busy = true; this.message = '正在读取该节点的验证信息';
    this.incidentId = item.id;
    this.stockTitle = item.name + ' · ' + item.stock;
    this.verifiedExit = false;
    this.measuredOnce = false;
    try {
      const descriptor = await this.api.request<VerificationDescriptor>('/v1/incidents/' + item.id + '/verification');
      this.descriptor = descriptor;
      const profile = await this.ssh.load(descriptor.node_id);
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
      await this.releaseAll();
      this.screen = 'home';
      this.error(err as Error);
    } finally { this.busy = false; }
  }

  private async begin(descriptor: VerificationDescriptor, profile: NodeProfile): Promise<void> {
    runtime.incident = this.incidentId;
    runtime.nodeId = descriptor.node_id;
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
      if (generation !== this.generation || !this.foreground) { throw new Error('验证已取消'); }
      const view = await runAction<AuditView>(session.id, descriptor.worker_command, '{"action":"audit"}');
      const target = this.matchTarget(view, descriptor.target);
      runtime.target = target;
      if (generation !== this.generation || !this.foreground) { throw new Error('验证已取消'); }
      this.message = '正在暂停该任务的后续采集';
      await runAction<PrepareValue>(session.id, descriptor.worker_command, JSON.stringify({ action: 'prepare', target: target }));
      if (generation !== this.generation || !this.foreground) { throw new Error('验证已取消'); }
      this.message = '正在核对节点出口';
      const exitInfo = await runAction<ExitValue>(session.id, descriptor.worker_command,
        JSON.stringify({ action: 'exit', stock: target.stock, job: target.job }));
      if (!target.route || exitInfo.route.signature !== target.route.signature) {
        throw new Error('节点出口已变化，已停止验证');
      }
      target.exit_ip = exitInfo.exit_ip;
      runtime.target = target;
      if (generation !== this.generation || !this.foreground) { throw new Error('验证已取消'); }
      this.message = '正在建立应用内 SSH 通道';
      const forwarding: SshForwardOptions = { host: profile.host, port: profile.port, user: profile.user,
        knownHostBase64: profile.knownHostBase64, remoteHost: target.route.host, remotePort: target.route.port };
      if (profile.authType === 'password') { forwarding.password = profile.secret; } else { forwarding.privateKey = profile.secret; }
      if (profile.passphrase) { forwarding.passphrase = profile.passphrase; }
      const tunnel = await forward(forwarding);
      runtime.forwardId = tunnel.id;
      const endpoint = await runtime.proxy.start(tunnel.port, descriptor.source_domains);
      const config = new webview.ProxyConfig();
      config.insertProxyRule(endpoint);
      config.clearImplicitRules();
      this.proxyApplied = true;
      await new Promise<void>((resolve, reject) => {
        try { webview.ProxyController.applyProxyOverride(config, () => resolve()); }
        catch (_) { reject(new Error('此系统无法应用 ArkWeb 验证代理')); }
      });
      if (generation !== this.generation || !this.foreground) { throw new Error('验证已取消'); }
      this.browser = new webview.WebviewController();
      this.webUrl = 'https://api.ipify.org';
      this.screen = 'verify'; this.showWeb = true;
      this.message = '正在核对手机与节点的共同出口';
      const ip = await this.measureExit();
      if (generation !== this.generation || !this.foreground) { throw new Error('验证已取消'); }
      if (ip.length === 0) { throw new Error('未能读取手机出口地址，请重试'); }
      if (ip !== target.exit_ip) { throw new Error('手机出口与节点出口不一致，已关闭通道'); }
      runtime.proxy.markReady();
      this.verifiedExit = true;
      this.message = '已确认共同出口，请在下方手动完成验证';
      this.browser.loadUrl(target.url ? target.url : 'https://api.ipify.org');
    } catch (err) {
      await this.releaseAll();
      this.screen = 'home';
      this.error(err as Error);
    }
  }

  private async pageEnded(url: string): Promise<void> {
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
      const result = await runAction<ResultValue>(runtime.sshId, command,
        JSON.stringify({ action: 'result', target: target, before_request_id: beforeId }));
      if (result.done && result.request) { return result.request.outcome; }
      await new Promise<void>((resolve) => setTimeout(resolve, 2000));
    }
    throw new Error('探测结果尚未确认；没有再次请求，请在控制台核对');
  }

  private async settleClaim(message: string): Promise<void> {
    runtime.dispatching = false;
    runtime.incident = '';
    runtime.target = undefined;
    this.claim = undefined;
    this.claimCanResume = false;
    this.claimMessage = '';
    await this.vault.claim('');
    await runtime.releaseSsh();
    this.message = message;
  }

  private async finish(): Promise<void> {
    if (!this.api || !runtime.incident || !this.verifiedExit || this.busy || !this.descriptor || !runtime.target) { return; }
    const descriptor = this.descriptor;
    const target = runtime.target;
    this.busy = true;
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
      this.claimMessage = '已提交一次探测，正在等待结果';
      this.screen = 'result';
      await this.stopWindow();
      await runtime.stopForwarding();
      this.message = '正在单次探测，通过后继续本股';
      const queued = await runAction<ProbeValue>(runtime.sshId, descriptor.worker_command,
        JSON.stringify({ action: 'probe', target: target }));
      claim.before_request_id = queued.before_request_id;
      claim.at = Date.now();
      await this.vault.claim(JSON.stringify(claim));
      this.claimCanResume = true;
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
        this.claimMessage = '探测结果尚未确认：' + (err as Error).message;
        this.message = '没有重发请求；可点“继续核对探测结果”或到控制台核对';
        await this.stopWindow();
        await runtime.stopForwarding();
        await runtime.releaseSsh();
      } else {
        await this.releaseAll();
        this.screen = 'home';
        this.error(err as Error);
      }
    } finally { this.busy = false; }
  }

  // Read-only follow-up after a restart or disconnect: only the original probe's
  // result is queried, and resume still goes through the worker's own guards.
  private async resumeClaim(): Promise<void> {
    if (!this.api || this.busy) { return; }
    const claim = this.claim;
    if (!claim) { return; }
    if (claim.before_request_id <= 0) { this.message = '上次未收到探测编号，无法核对；请到控制台核对'; return; }
    const target = JSON.parse(claim.target) as DirectTarget;
    this.busy = true;
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
      const outcome = await this.waitProbeResult(claim.worker_command, target, claim.before_request_id);
      if (outcome !== 'real_data' && outcome !== 'detail_unavailable') {
        throw new Error('探测未通过，任务保持暂停');
      }
      await runAction<ResumeValue>(runtime.sshId, claim.worker_command,
        JSON.stringify({ action: 'resume', target: target }));
      await this.settleClaim('验证通过，已继续 ' + target.stock);
      await this.refresh();
    } catch (err) {
      this.claimMessage = '探测结果尚未确认：' + (err as Error).message;
      this.message = '没有重发请求；请到控制台核对';
      await runtime.releaseSsh();
    } finally { this.busy = false; }
  }

  private nodeActionLabel(): string {
    if (this.busy) { return '正在读取…'; }
    if (this.channelMode) { return this.nodeFingerprint ? '确认指纹并建立通道' : '读取指纹'; }
    return this.nodeFingerprint ? '确认指纹并连接' : '读取指纹';
  }

  // Opens the node form without any hub involvement, so the phone-to-node SSH
  // channel can be established and proven on device by itself.
  private openChannel(): void {
    if (this.busy || runtime.incident) { return; }
    this.channelMode = true;
    this.channelOutput = '';
    this.nodeHint = '直接建立到该节点的 SSH 通道，不经过主控，也不使用待办信息。';
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
    finally { this.busy = false; }
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
    const secret = this.nodeAuth === 'password' ? this.nodeSecret : this.nodeKey.trim();
    if (secret.length < 8) {
      this.message = this.nodeAuth === 'password' ? '请填写该节点的登录密码' : '请粘贴该节点的私钥内容';
      return;
    }
    this.busy = true;
    this.message = this.nodeConfirmed ? '正在连接节点并建立通道' : '正在读取节点主机指纹…';
    try {
      const key = await probeHostKey({ host: host, port: port });
      const fingerprint = key.type + ' ' + key.fingerprint;
      if (!this.nodeConfirmed || this.nodeFingerprint !== fingerprint) {
        this.nodeFingerprint = fingerprint;
        this.nodeConfirmed = false;
        this.message = '请核对该指纹与该节点实际一致，再点“' + this.nodeActionLabel() + '”';
        return;
      }
      const profile: NodeProfile = { nodeId: this.channelMode ? 'channel-test' : (descriptor ? descriptor.node_id : ''),
        host: host, port: port, user: user, authType: this.nodeAuth, secret: secret, passphrase: this.nodePassphrase,
        knownHostBase64: key.base64, knownHostType: key.type, fingerprint: fingerprint };
      if (this.channelMode) {
        await this.channelConnect(profile);
      } else if (descriptor) {
        await this.ssh.save(profile);
        await this.begin(descriptor, profile);
      }
    } catch (err) { this.error(err as Error); }
    finally { this.busy = false; }
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

  onBackPress(): boolean {
    if (this.screen !== 'home' && this.screen !== 'settings') { this.goHome(); return true; }
    return false;
  }

  @Builder
  header() {
    Row() {
      Text('Waygate').fontSize(27).fontWeight(FontWeight.Bold).fontColor(INK).layoutWeight(1)
      if (this.screen === 'home') {
        Button('诊断').backgroundColor(Color.Transparent).fontColor(MUTED)
          .onClick(() => this.openDiagnostics())
        Button('设置').backgroundColor(Color.Transparent).fontColor(MUTED)
          .onClick(() => { this.screen = 'settings'; })
      } else if (this.api) {
        Button('返回').backgroundColor(Color.Transparent).fontColor(INK).onClick(() => this.goHome())
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
                .enabled(!this.busy && !runtime.incident && !runtime.dispatching && (item.kind !== 'challenge' || item.can_verify))
                .onClick(() => { if (item.kind === 'challenge') { this.verify(item); } else { this.openConsole(); } })
              Button('稍后提醒').backgroundColor(BG).fontColor(INK).onClick(() => this.snooze(item))
            }
          }.padding(20).width('100%').backgroundColor(Color.White).borderRadius(18)
        }, (item: Incident) => item.id)
        Button('打开采集控制台').width('100%').height(50).fontColor(INK).backgroundColor('#DDECE4')
          .enabled(!this.busy && !runtime.dispatching).onClick(() => this.openConsole())
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
            TextInput({ text: this.nodeHost, placeholder: '节点 SSH 地址（域名或 IP）' })
              .onChange((v: string) => { this.nodeHost = v; this.nodeFingerprint = ''; this.nodeConfirmed = false; })
            TextInput({ text: this.nodePort, placeholder: 'SSH 端口（默认 22）' }).type(InputType.Number)
              .onChange((v: string) => this.nodePort = v)
            TextInput({ text: this.nodeUser, placeholder: 'SSH 用户（默认 root）' })
              .onChange((v: string) => { this.nodeUser = v; this.nodeFingerprint = ''; this.nodeConfirmed = false; })
            Row({ space: 10 }) {
              Button('密码').layoutWeight(1).fontColor(this.nodeAuth === 'password' ? Color.White : INK)
                .backgroundColor(this.nodeAuth === 'password' ? INK : BG).onClick(() => this.nodeAuth = 'password')
              Button('私钥').layoutWeight(1).fontColor(this.nodeAuth === 'key' ? Color.White : INK)
                .backgroundColor(this.nodeAuth === 'key' ? INK : BG).onClick(() => this.nodeAuth = 'key')
            }.width('100%')
            if (this.nodeAuth === 'password') {
              TextInput({ text: this.nodeSecret, placeholder: '该节点的登录密码' }).type(InputType.Password)
                .onChange((v: string) => this.nodeSecret = v)
            } else {
              TextArea({ text: this.nodeKey, placeholder: '粘贴该节点可用的私钥内容' }).height(120)
                .onChange((v: string) => this.nodeKey = v)
              TextInput({ text: this.nodePassphrase, placeholder: '私钥口令（无则留空）' }).type(InputType.Password)
                .onChange((v: string) => this.nodePassphrase = v)
            }
            if (this.nodeFingerprint) {
              Text('主机指纹（请与该节点实际指纹核对）\n' + this.nodeFingerprint)
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
          if (this.screen !== 'result' || runtime.dispatching) { LoadingProgress().width(44).height(44).color(INK) }
          if (this.claimMessage) {
            Text(this.claimMessage).fontSize(14).fontColor(INK).width('100%')
              .padding({ left: 30, right: 30 }).textAlign(TextAlign.Center)
          }
          if (this.screen === 'result' && runtime.dispatching && this.claimCanResume) {
            Button(this.busy ? '正在核对…' : '继续核对探测结果').backgroundColor(INK)
              .opacity(this.busy ? 0.6 : 1).enabled(!this.busy).onClick(() => this.resumeClaim())
          }
          if (this.screen === 'result') {
            Button('返回待办').backgroundColor('#DDECE4').fontColor(INK).onClick(() => this.leaveResult())
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
          Button('验证完成并继续').layoutWeight(1).backgroundColor(INK)
            .enabled(this.verifiedExit && !this.busy).onClick(() => this.finish())
        }.padding(16).width('100%')
      }
    }.width('100%').height('100%').backgroundColor(BG)
  }
}
