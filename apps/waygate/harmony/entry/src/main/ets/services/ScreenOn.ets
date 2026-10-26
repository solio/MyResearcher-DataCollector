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

class ScreenOn {
  private win: window.Window | undefined = undefined;
  private held: boolean = false;

  // Called once from EntryAbility, so the page never has to thread a context through.
  attach(win: window.Window): void {
    this.win = win;
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
      hilog.info(LOG_DOMAIN, LOG_TAG, 'keep screen on=%{public}s', on ? 'yes' : 'no');
    } catch (err) {
      // A refused hold degrades the experience; it must never fail the verification.
      hilog.warn(LOG_DOMAIN, LOG_TAG, 'keep screen on=%{public}s failed code=%{public}d',
        on ? 'yes' : 'no', (err as BusinessError).code);
    }
  }
}

export const screenOn = new ScreenOn();
