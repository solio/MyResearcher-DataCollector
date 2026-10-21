import { LocalProxy } from './LocalProxy';
import { Api } from './Api';
import { DirectTarget, NodeProfile, VerificationDescriptor } from './Models';
import { close, stopForward } from 'libwaygate_ssh.so';

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

  async stopForwarding(): Promise<void> {
    if (this.forwardId >= 0) {
      const id = this.forwardId;
      this.forwardId = -1;
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
  }
}
export const runtime = new SessionRuntime();
