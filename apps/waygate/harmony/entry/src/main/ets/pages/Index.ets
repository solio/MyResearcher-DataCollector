import { webview } from '@kit.ArkWeb';
import { common } from '@kit.AbilityKit';
import { http } from '@kit.NetworkKit';
import { Api, readableError } from '../services/Api';
import { Vault } from '../services/Vault';
import { registerPush } from '../services/Push';
import { runtime } from '../services/SessionRuntime';
import { Credentials, EmptyReply, Inbox, Incident, Link, NodeState, Session, StockState } from '../services/Models';

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
  @StorageLink('foreground') @Watch('visibilityChanged') foreground: boolean = true;
  @StorageLink('incidentLink') @Watch('linkChanged') incidentLink: string = '';

  private vault: Vault = new Vault();
  private api: Api | undefined = undefined;
  private credentials: Credentials = { hub: '', token: '', deviceId: '', push: false, pendingSession: '' };
  private browser: webview.WebviewController = new webview.WebviewController();
  private timer: number = -1;
  private polling: boolean = false;
  private proxyApplied: boolean = false;
  private destroyWeb: (() => void) | undefined = undefined;
  private detachPending: Promise<void> | undefined = undefined;
  private cleanupPending: Promise<void> | undefined = undefined;
  private checkingExit: boolean = false;
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

  private async initialize(): Promise<void> {
    try {
      this.credentials = await this.vault.load(this.context());
      this.hub = this.credentials.hub;
      this.token = this.credentials.token;
      if (this.hub && this.token) {
        this.api = new Api(this.credentials);
        runtime.api = this.api;
        this.screen = 'home';
        if (this.credentials.pendingSession) {
          runtime.session = this.credentials.pendingSession;
          const previous = await this.api.request<Session>('/v1/sessions/' + runtime.session);
          if (previous.phase === 'dispatching') {
            runtime.dispatching = true;
            this.screen = 'result';
            this.message = '正在查询上次的单次探测结果';
          } else {
            if (previous.phase === 'verifying' || previous.phase === 'preparing') {
              await this.api.post<EmptyReply>('/v1/sessions/' + runtime.session + '/cancel');
            }
            this.message = previous.result.message || '上次验证窗口已结束，可重新处理待办';
            runtime.session = '';
            await this.vault.session('');
          }
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
    try {
      const next: Credentials = { hub: address, token: this.token.trim(), deviceId: this.credentials.deviceId,
        push: false, pendingSession: '' };
      const api = new Api(next);
      await api.request<Inbox>('/v1/inbox');
      // Stop notifications to the previous hub when changing the login.
      if (this.api && this.credentials.push) {
        await this.api.request<EmptyReply>('/v1/devices/' + this.credentials.deviceId, http.RequestMethod.DELETE);
      }
      await this.vault.save(next);
      await this.vault.session('');
      this.credentials = next; this.api = api; runtime.api = api;
      this.screen = 'home'; this.message = ''; this.notification = '通知尚未开启';
      this.syncMessage = ''; this.lastRefreshAt = ''; this.lastCollectorSync = ''; this.lastSyncFailure = '';
      await this.refresh();
    } catch (err) { this.error(err as Error); }
    finally { this.busy = false; }
  }

  private async refresh(): Promise<void> {
    if (!this.api || this.polling || !this.foreground) { return; }
    if (this.busy && runtime.dispatching) { return; }
    this.polling = true;
    try {
      if (runtime.session) {
        const session = await this.api.request<Session>('/v1/sessions/' + runtime.session);
        if (runtime.dispatching && session.phase !== 'dispatching') {
          if (session.phase === 'verifying' || session.phase === 'preparing') {
            await this.api.post<EmptyReply>('/v1/sessions/' + runtime.session + '/cancel');
          }
          this.message = session.result.message || '已结束，请查看任务状态';
          runtime.dispatching = false; runtime.session = '';
          await this.vault.session('');
        } else if (this.screen === 'verify' && session.phase !== 'verifying') {
          await this.closeWindow();
          this.screen = 'home';
          this.message = '验证会话已结束，任务保持暂停';
        }
      }
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
    } catch (err) {
      this.syncFailed((err as Error).message || '主控刷新失败', this.api.lastFailureDetails);
      if (this.screen === 'verify') {
        await this.closeWindow();
        this.screen = 'home';
      }
    } finally { this.polling = false; }
  }

  private prioritizeLink(): void {
    const id = this.incidentLink.replace('waygate://incident/', '');
    if (/^[a-f0-9]{32}$/.test(id)) {
      const selected = this.items.find((item: Incident) => item.id === id);
      if (selected) { this.items = [selected].concat(this.items.filter((item: Incident) => item.id !== id)); }
    }
  }

  private linkChanged(): void {
    if (!runtime.session && this.screen !== 'settings') { this.goHome(); }
    this.prioritizeLink();
  }

  private visibilityChanged(): void {
    if (!this.foreground) {
      this.generation++;
      if (this.showWeb || this.proxyApplied || runtime.session && !runtime.dispatching) {
        this.closeWindow().then(() => { this.screen = runtime.dispatching ? 'result' : 'home'; });
      }
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

  private async closeWindow(): Promise<void> {
    if (this.cleanupPending) { return this.cleanupPending; }
    this.cleanupPending = this.cleanup();
    try { await this.cleanupPending; }
    finally { this.cleanupPending = undefined; }
  }

  private async cleanup(): Promise<void> {
    await runtime.proxy.stop(); // Immediately fail closed, even before Web teardown.
    await this.detach();
    await this.removeProxy();
    if (this.api && runtime.session && !runtime.dispatching) {
      await this.api.post<EmptyReply>('/v1/sessions/' + runtime.session + '/cancel').catch(() => {});
      runtime.session = ''; await this.vault.session('');
    }
    this.verifiedExit = false;
  }

  private async goHome(): Promise<void> {
    this.generation++;
    try { await this.closeWindow(); this.screen = 'home'; await this.refresh(); }
    catch (err) { this.error(err as Error); }
  }

  private async openConsole(): Promise<void> {
    if (!this.api || this.busy || runtime.dispatching) { return; }
    this.busy = true;
    try {
      await this.closeWindow();
      const link = await this.api.post<Link>('/v1/console-ticket');
      if (!this.foreground) { return; }
      this.browser = new webview.WebviewController();
      this.webUrl = link.url; this.screen = 'console'; this.showWeb = true;
    } catch (err) { this.error(err as Error); }
    finally { this.busy = false; }
  }

  private async verify(item: Incident): Promise<void> {
    if (!this.api || this.busy || runtime.session) { return; }
    this.busy = true; this.message = '正在建立临时验证通道';
    const generation = ++this.generation;
    try {
      await this.detach();
      this.screen = 'transition';
      this.stockTitle = item.name + ' · ' + item.stock;
      const created = await this.api.post<Session>('/v1/incidents/' + item.id + '/sessions');
      runtime.session = created.id;
      await this.vault.session(created.id);
      let session = created;
      while (session.phase === 'preparing') {
        if (generation !== this.generation || !this.foreground) { throw new Error('验证已取消'); }
        await new Promise<void>((resolve) => setTimeout(resolve, 1500));
        session = await this.api.request<Session>('/v1/sessions/' + created.id);
      }
      if (session.phase !== 'verifying') { throw new Error(session.result.message || '无法建立验证会话'); }
      if (generation !== this.generation || !this.foreground) { throw new Error('验证已取消'); }
      const endpoint = await runtime.proxy.start(this.credentials.hub, created.id, this.credentials.token);
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
      this.message = '正在核对共同出口';
    } catch (err) {
      await this.closeWindow(); this.screen = 'home'; this.error(err as Error);
    } finally { this.busy = false; }
  }

  private async pageEnded(url: string): Promise<void> {
    if (this.screen !== 'verify' || this.verifiedExit || this.checkingExit || !this.api) { return; }
    if (url !== 'https://api.ipify.org/' && url !== 'https://api.ipify.org') { return; }
    this.checkingExit = true;
    try {
      const output = await this.browser.runJavaScript('JSON.stringify(document.body.innerText.trim())');
      let ip = JSON.parse(output) as string;
      // ArkWeb versions may JSON-encode the JS string result one extra time.
      if (ip.startsWith('"')) { ip = JSON.parse(ip) as string; }
      const source = await this.api.post<Link>('/v1/sessions/' + runtime.session + '/ready', JSON.stringify({ browser_ip: ip }));
      if (!this.foreground || this.screen !== 'verify') { return; }
      this.verifiedExit = true;
      this.message = '已确认共同出口。请在下方手动完成验证';
      this.browser.loadUrl(source.url);
    } catch (err) {
      await this.closeWindow(); this.screen = 'home'; this.error(err as Error);
    } finally { this.checkingExit = false; }
  }

  private async finish(): Promise<void> {
    if (!this.api || !runtime.session || !this.verifiedExit || this.busy) { return; }
    this.busy = true;
    this.screen = 'result';
    // Prevent cancellation while the authorized probe is being handed off.
    runtime.dispatching = true;
    try {
      await this.detach(); await runtime.proxy.stop(); await this.removeProxy();
      this.screen = 'result'; this.message = '正在单次探测，通过后继续本股';
      await this.api.post<Session>('/v1/sessions/' + runtime.session + '/finish', '{"human_confirmed":true}');
      await this.refresh();
    } catch (err) {
      this.message = '提交结果待核对；没有重复探测。' + (err as Error).message;
      // Keep persisted session id. refresh queries its durable state only.
    } finally { this.busy = false; }
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
                .enabled(!this.busy && !runtime.session && (item.kind !== 'challenge' || item.can_verify))
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
          Button('连接并保存').width('100%').backgroundColor(INK).enabled(!this.busy).onClick(() => this.save())
          if (this.api) {
            Button('开启任务通知').width('100%').backgroundColor('#DDECE4').fontColor(INK).onClick(() => this.enablePush(true))
            Text(this.notification).fontColor(MUTED).fontSize(13)
          }
          Text('节点和代理沿用主控配置。').fontColor(MUTED).fontSize(14)
        }.padding(24).width('100%')
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
          if (this.screen === 'result' && !runtime.dispatching) {
            Button('返回待办').backgroundColor(INK).onClick(() => this.goHome())
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
