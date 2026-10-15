import { asset } from '@kit.AssetStoreKit';
import { preferences } from '@kit.ArkData';
import { common } from '@kit.AbilityKit';
import { util } from '@kit.ArkTS';
import { BusinessError } from '@kit.BasicServicesKit';
import { Credentials } from './Models';

const ALIAS = new util.TextEncoder().encodeInto('waygate-console-token-v1');

export class Vault {
  private prefs: preferences.Preferences | undefined = undefined;

  async load(context: common.UIAbilityContext): Promise<Credentials> {
    this.prefs = await preferences.getPreferences(context, { name: 'waygate' });
    let device = await this.prefs.get('device', '') as string;
    if (!device) {
      device = util.generateRandomUUID(true);
      await this.prefs.put('device', device);
      await this.prefs.flush();
    }
    let token = '';
    const query: asset.AssetMap = new Map();
    query.set(asset.Tag.ALIAS, ALIAS);
    query.set(asset.Tag.RETURN_TYPE, asset.ReturnType.ALL);
    try {
      const rows = await asset.query(query);
      if (rows.length > 0) {
        token = new util.TextDecoder().decodeToString(rows[0].get(asset.Tag.SECRET) as Uint8Array);
      }
    } catch (err) {
      const error = err as BusinessError;
      if (error.code !== 24000002) { throw new Error('系统凭据存储不可用，请解锁后重试'); }
    }
    return {
      hub: await this.prefs.get('hub', '') as string, token: token, deviceId: device,
      push: await this.prefs.get('push', false) as boolean,
      pendingClaim: await this.prefs.get('pendingClaim', '') as string
    };
  }

  async save(credentials: Credentials): Promise<void> {
    if (!this.prefs) { throw new Error('设置尚未初始化'); }
    const query: asset.AssetMap = new Map();
    query.set(asset.Tag.ALIAS, ALIAS);
    const attrs: asset.AssetMap = new Map();
    attrs.set(asset.Tag.SECRET, new util.TextEncoder().encodeInto(credentials.token));
    try {
      await asset.update(query, attrs);
    } catch (err) {
      const error = err as BusinessError;
      if (error.code !== 24000002) { throw new Error('无法安全保存主控凭据'); }
      attrs.set(asset.Tag.ALIAS, ALIAS);
      attrs.set(asset.Tag.ACCESSIBILITY, asset.Accessibility.DEVICE_FIRST_UNLOCKED);
      await asset.add(attrs);
    }
    await this.prefs.put('hub', credentials.hub);
    await this.prefs.put('push', credentials.push);
    await this.prefs.flush();
  }

  // Durable probe claim. Written before the one-shot probe leaves the phone, so a
  // restart only re-reads the original result instead of sending a second request.
  async claim(value: string): Promise<void> {
    if (this.prefs) {
      await this.prefs.put('pendingClaim', value);
      await this.prefs.flush();
    }
  }
}
