import { window } from '@kit.ArkUI';
import { BusinessError } from '@kit.BasicServicesKit';
import { hilog } from '@kit.PerformanceAnalysisKit';

// HarmonyOS ships "注视屏幕不熄屏" as a system heuristic: the front camera decides whether
// somebody is looking, and Huawei documents it failing in low light, at an angle, behind
// glasses or in a media scene. A captcha window is exactly the case that heuristic cannot
// cover reliably — the operator stares at the page for minutes without touching it — so the
// app has to hold its own screen-on instead of hoping the heuristic fires.
//
// The hold is deliberately scoped to the verification browser: the platform warns that it
// may cancel keep-screen-on for apps that use it outside necessary scenarios, and the inbox
// or the console have no reason to defeat the user's sleep setting.
const LOG_DOMAIN = 0x3200;
const LOG_TAG = 'waygate_screen';
// A hold with no bound is a battery bug: the operator may walk away from the captcha page.
// Ten minutes matches the hub's own session lifetime, so the screen can sleep once the
// verification itself has expired.
const MAX_HOLD_MS = 10 * 60 * 1000;

class ScreenOn {
  private win: window.Window | undefined = undefined;
  private held: boolean = false;
  private timer: number = -1;

  // Called once from EntryAbility, so the page never has to thread a context through.
  attach(win: window.Window): void {
    this.win = win;
  }

  // The platform drops the hold when the window leaves the foreground, without telling us.
  // Forgetting the flag here is what makes the next hold(true) apply again instead of being
  // skipped as a no-op.
  suspend(): void {
    this.held = false;
    this.disarm();
  }

  private disarm(): void {
    if (this.timer >= 0) {
      clearTimeout(this.timer);
      this.timer = -1;
    }
  }

  // Idempotent: every release path may call it without first checking what is held.
  async hold(on: boolean): Promise<void> {
    const win = this.win;
    if (!win || this.held === on) {
      return;
    }
    try {
      await win.setWindowKeepScreenOn(on);
      this.held = on;
      this.disarm();
      if (on) {
        this.timer = setTimeout(() => { this.hold(false); }, MAX_HOLD_MS);
      }
      hilog.info(LOG_DOMAIN, LOG_TAG, 'keep screen on=%{public}s', on ? 'yes' : 'no');
    } catch (err) {
      // A refused hold degrades the experience; it must never fail the verification.
      hilog.warn(LOG_DOMAIN, LOG_TAG, 'keep screen on=%{public}s failed code=%{public}d',
        on ? 'yes' : 'no', (err as BusinessError).code);
    }
  }
}

export const screenOn = new ScreenOn();
