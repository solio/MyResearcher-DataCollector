import { LocalProxy } from './LocalProxy';
import { Api } from './Api';
import { DirectTarget } from './Models';
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

  // Called by UIAbility when backgrounded, even if ArkUI stops rendering. Only
  // resources this phone created are released; the hub is never told to cancel,
  // and a submitted probe claim is left intact.
  async background(): Promise<void> {
    await this.proxy.stop();
    await this.releaseSsh();
    this.incident = '';
    this.target = undefined;
  }
}
export const runtime = new SessionRuntime();
