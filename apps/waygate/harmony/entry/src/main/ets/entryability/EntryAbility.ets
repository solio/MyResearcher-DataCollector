import { UIAbility, Want, AbilityConstant, errorManager, bundleManager } from '@kit.AbilityKit';
import { BusinessError } from '@kit.BasicServicesKit';
import { window } from '@kit.ArkUI';
import { webview } from '@kit.ArkWeb';
import { runtime } from '../services/SessionRuntime';
import { screenOn } from '../services/ScreenOn';
import { crashLog, faultLog } from '../services/CrashLog';
import { hiAppEvent } from '@kit.PerformanceAnalysisKit';

export default class EntryAbility extends UIAbility {
  onCreate(want: Want, _launch: AbilityConstant.LaunchParam): void {
    crashLog.init(this.context.filesDir, 'crash.log', 400);
    faultLog.init(this.context.filesDir, 'fault.log', 700);
    this.watchCrashes();
    this.watchFaults();
    AppStorage.setOrCreate('foreground', true);
    AppStorage.setOrCreate('incidentLink', this.link(want));
    webview.WebviewController.initializeWebEngine();
  }

  onNewWant(want: Want, _launch: AbilityConstant.LaunchParam): void {
    // Notification only opens the inbox; never triggers verification/probing.
    AppStorage.setOrCreate('incidentLink', this.link(want));
  }

  /**
   * Uncaught JS exceptions (including unhandled promise rejections) are the only crash evidence
   * available on a phone without log permissions, so keep the last few in a bounded file.
   */
  private watchCrashes(): void {
    const observer: errorManager.ErrorObserver = {
      onUnhandledException: (errMsg: string) => {
        crashLog.append('unhandled', errMsg || 'no message');
        this.publishTail();
      },
      onException: (errObject: Error) => {
        crashLog.append('exception', (errObject?.message || 'no message') + ' ' + (errObject?.stack || ''));
        this.publishTail();
      }
    };
    try {
      errorManager.on('error', observer);
    } catch (_) {
      crashLog.append('hook', 'errorManager.on(error) unavailable');
    }
    let version = '?';
    try {
      version = bundleManager.getBundleInfoForSelfSync(bundleManager.BundleFlag.GET_BUNDLE_INFO_DEFAULT).versionName;
    } catch (_) {
      version = '?';
    }
    AppStorage.setOrCreate('version', version);
    crashLog.append('boot', 'v' + version);
    this.publishTail();
  }

  /**
   * Native crashes (cppcrash / appfreeze) never reach errorManager; HiAppEvent is the only
   * in-app source for them and needs no system log permission.
   */
  private watchFaults(): void {
    try {
      hiAppEvent.addWatcher({
        name: 'waygate_fault',
        appEventFilters: [{ domain: 'OS', eventTypes: [hiAppEvent.EventType.FAULT] }],
        onReceive: (domain: string, groups: hiAppEvent.AppEventGroup[]) => {
          for (const group of groups) {
            for (const info of group.appEventInfos) {
              let detail = '';
              try {
                detail = JSON.stringify(info.params);
              } catch (_) {
                detail = 'params unreadable';
              }
              faultLog.append(info.name, detail);
            }
          }
          this.publishTail();
        }
      });
    } catch (err) {
      const reason = (err as BusinessError)?.message || String(err);
      faultLog.append('hook', 'hiAppEvent unavailable: ' + reason);
    }
  }

  private publishTail(): void {
    AppStorage.setOrCreate('crashTail', crashLog.tail(4));
    AppStorage.setOrCreate('crashBytes', crashLog.size());
    AppStorage.setOrCreate('faultTail', faultLog.tail(3));
    AppStorage.setOrCreate('faultBytes', faultLog.size());
  }

  private link(want: Want): string {
    const incident = want.parameters?.['incident_id'];
    if (typeof incident === 'string' && /^[a-f0-9]{32}$/.test(incident)) {
      return 'waygate://incident/' + incident;
    }
    return want.uri || '';
  }

  onWindowStageCreate(stage: window.WindowStage): void {
    stage.loadContent('pages/Index');
    // The verification page holds the screen on while a captcha is on display; it needs the
    // real window to do that, and the ability is the only place that owns one.
    stage.getMainWindow().then((win: window.Window) => {
      screenOn.attach(win);
    }).catch(() => {});
  }
  onForeground(): void { AppStorage.setOrCreate('foreground', true); crashLog.append('life', 'foreground'); }
  // Locking the screen or switching apps must not tear the verification down: the tunnel,
  // the app-local proxy and the page are kept so an already-solved captcha survives. Only
  // the real end of the ability releases them.
  onBackground(): void { AppStorage.setOrCreate('foreground', false); crashLog.append('life', 'background'); }
  onDestroy(): void { crashLog.append('life', 'destroy'); runtime.background(); screenOn.hold(false); }
}
