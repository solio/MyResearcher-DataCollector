import { UIAbility, Want, AbilityConstant } from '@kit.AbilityKit';
import { window } from '@kit.ArkUI';
import { webview } from '@kit.ArkWeb';
import { runtime } from '../services/SessionRuntime';

export default class EntryAbility extends UIAbility {
  onCreate(want: Want, _launch: AbilityConstant.LaunchParam): void {
    AppStorage.setOrCreate('foreground', true);
    AppStorage.setOrCreate('incidentLink', this.link(want));
    webview.WebviewController.initializeWebEngine();
  }

  onNewWant(want: Want, _launch: AbilityConstant.LaunchParam): void {
    // Notification only opens the inbox; never triggers verification/probing.
    AppStorage.setOrCreate('incidentLink', this.link(want));
  }

  private link(want: Want): string {
    const incident = want.parameters?.['incident_id'];
    if (typeof incident === 'string' && /^[a-f0-9]{32}$/.test(incident)) {
      return 'waygate://incident/' + incident;
    }
    return want.uri || '';
  }

  onWindowStageCreate(stage: window.WindowStage): void { stage.loadContent('pages/Index'); }
  onForeground(): void { AppStorage.setOrCreate('foreground', true); }
  onBackground(): void {
    AppStorage.setOrCreate('foreground', false);
    runtime.background();
  }
  onDestroy(): void { runtime.background(); }
}
