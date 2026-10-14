import { pushService } from '@kit.PushKit';
import { notificationManager } from '@kit.NotificationKit';
import { common } from '@kit.AbilityKit';
import { Api } from './Api';
import { EmptyReply } from './Models';

export async function registerPush(context: common.UIAbilityContext, api: Api, ask: boolean): Promise<void> {
  if (ask) { await notificationManager.requestEnableNotification(context); }
  if (!await notificationManager.isNotificationEnabled()) {
    throw new Error('通知未开启，可在系统设置中开启 Waygate 通知');
  }
  const token = await pushService.getToken();
  await api.post<EmptyReply>('/v1/devices', JSON.stringify({ id: api.credentials.deviceId, token: token }));
}
