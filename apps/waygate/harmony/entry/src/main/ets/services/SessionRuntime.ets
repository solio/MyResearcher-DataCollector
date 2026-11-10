import { LocalProxy } from './LocalProxy';
import { Api } from './Api';
import { DirectTarget, NodeProfile, VerificationDescriptor } from './Models';
import { close, forward, SshForwardOptions, stopForward } from 'libwaygate_ssh.so';

class SessionRuntime {
  proxy: LocalProxy = new LocalProxy();
  api: Api | undefined = undefined;
  // Incident of the verification window currently open on this phone.
  incident: string = '';
  // A probe has been submitted and its outcome is not settled yet. The claim is
  // persisted separately, so a restart only re-reads that result.
  dispatching: boolean = false;
  sshId: number = -1;
  forwardId: number = -1;
  nodeId: string = '';
  target: DirectTarget | undefined = undefined;
  // What the running verification was started with. It lives here rather than only in the
  // page so a page recreated by the system can offer to rebuild the same step instead of
  // silently dropping the operator back to the inbox with no explanation.
  descriptor: VerificationDescriptor | undefined = undefined;
  profile: NodeProfile | undefined = undefined;

  // direct 出口没有固定的 mihomo 监听口：每个目标主机各开一条转发，按 host:port 复用，
  // 否则一页验证码会反复握手几十次。
  private hostForwards: Map<string, number> = new Map();
  forwardIds: number[] = [];

  async forwardFor(host: string, port: number): Promise<number> {
    const key = host + ':' + port;
    const cached = this.hostForwards.get(key);
    if (cached !== undefined) {
      return cached;
    }
    const profile = this.profile;
    if (!profile) {
      throw new Error('缺少节点凭据，无法建立直连通道');
    }
    const options: SshForwardOptions = { host: profile.host, port: profile.port, user: profile.user,
      knownHostBase64: profile.knownHostBase64, remoteHost: host, remotePort: port };
    if (profile.authType === 'password') {
      options.password = profile.secret;
    } else {
      options.privateKey = profile.secret;
    }
    if (profile.passphrase) {
      options.passphrase = profile.passphrase;
    }
    const tunnel = await forward(options);
    this.hostForwards.set(key, tunnel.port);
    this.forwardIds.push(tunnel.id);
    return tunnel.port;
  }

  async stopForwarding(): Promise<void> {
    if (this.forwardId >= 0) {
      const id = this.forwardId;
      this.forwardId = -1;
      await stopForward(id).catch(() => {});
    }
    const ids = this.forwardIds.slice();
    this.forwardIds = [];
    this.hostForwards.clear();
    for (const id of ids) {
      await stopForward(id).catch(() => {});
    }
  }

  async releaseSsh(): Promise<void> {
    await this.stopForwarding();
    if (this.sshId >= 0) {
      const id = this.sshId;
      this.sshId = -1;
      await close(id).catch(() => {});
    }
  }

  // Called only when the ability is really destroyed. Backgrounding the app must never come
  // here: closing this proxy while the app-level proxy override still names it is exactly
  // what makes a returning page fail with ERR_PROXY_CONNECTION_FAILED.
  async background(): Promise<void> {
    await this.proxy.stop();
    await this.releaseSsh();
    this.incident = '';
    this.target = undefined;
    this.descriptor = undefined;
    this.profile = undefined;
    this.hostForwards.clear();
    this.forwardIds = [];
  }
}
export const runtime = new SessionRuntime();
