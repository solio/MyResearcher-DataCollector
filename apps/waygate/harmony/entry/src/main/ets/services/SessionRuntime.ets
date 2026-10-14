import { LocalProxy } from './LocalProxy';
import { Api } from './Api';
import { EmptyReply } from './Models';

class SessionRuntime {
  proxy: LocalProxy = new LocalProxy();
  api: Api | undefined = undefined;
  session: string = '';
  dispatching: boolean = false;

  // Called directly by UIAbility when backgrounded, even if ArkUI stops rendering.
  // Keep Web proxy override pointed at the closed loopback listener until Web
  // has been destroyed. Removing it here could let a live page go direct.
  async background(): Promise<void> {
    await this.proxy.stop();
    if (this.api && this.session && !this.dispatching) {
      await this.api.post<EmptyReply>('/v1/sessions/' + this.session + '/cancel').catch(() => {});
    }
  }
}
export const runtime = new SessionRuntime();
