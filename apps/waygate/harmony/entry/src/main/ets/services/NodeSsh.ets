import { asset } from '@kit.AssetStoreKit';
import { util } from '@kit.ArkTS';
import { BusinessError } from '@kit.BasicServicesKit';
import { exec } from 'libwaygate_ssh.so';
import { Envelope, NodeProfile } from './Models';

// SSH login material lives only in the phone Asset Store, keyed by node_id, so a
// node is imported once and later verifications are one tap. Nothing is sent to
// the hub and nothing is written to logs.
const PREFIX = 'waygate-node-';
const NOT_FOUND = 24000002;

export class NodeSsh {
  private alias(nodeId: string): Uint8Array {
    return new util.TextEncoder().encodeInto(PREFIX + nodeId + '-v1');
  }

  async load(nodeId: string): Promise<NodeProfile | undefined> {
    const query: asset.AssetMap = new Map();
    query.set(asset.Tag.ALIAS, this.alias(nodeId));
    query.set(asset.Tag.RETURN_TYPE, asset.ReturnType.ALL);
    try {
      const rows = await asset.query(query);
      if (rows.length === 0) { return undefined; }
      const stored = rows[0].get(asset.Tag.SECRET) as Uint8Array;
      return JSON.parse(new util.TextDecoder().decodeToString(stored)) as NodeProfile;
    } catch (err) {
      if ((err as BusinessError).code === NOT_FOUND) { return undefined; }
      throw new Error('无法读取节点 SSH 凭据，请解锁后重试');
    }
  }

  async save(profile: NodeProfile): Promise<void> {
    const stored = new util.TextEncoder().encodeInto(JSON.stringify(profile));
    const query: asset.AssetMap = new Map();
    query.set(asset.Tag.ALIAS, this.alias(profile.nodeId));
    const attributes: asset.AssetMap = new Map();
    attributes.set(asset.Tag.SECRET, stored);
    try {
      await asset.update(query, attributes);
    } catch (err) {
      if ((err as BusinessError).code !== NOT_FOUND) { throw new Error('无法保存节点 SSH 凭据'); }
      const created: asset.AssetMap = new Map();
      created.set(asset.Tag.ALIAS, this.alias(profile.nodeId));
      created.set(asset.Tag.SECRET, stored);
      created.set(asset.Tag.ACCESSIBILITY, asset.Accessibility.DEVICE_FIRST_UNLOCKED);
      await asset.add(created);
    }
  }
}

// Run one existing worker action over SSH exec: one JSON payload on stdin, then
// EOF, and the same {"ok":...,"value":...} contract the desktop command uses.
export async function runAction<T>(sessionId: number, command: string, payload: string): Promise<T> {
  const result = await exec(sessionId, command, payload);
  if (result.exitStatus !== 0) {
    const detail = result.stderr.trim();
    throw new Error('验证命令在该节点执行失败' + (detail ? '：' + detail.slice(0, 200) : '（退出码 ' + result.exitStatus.toString() + '）'));
  }
  let parsed: Envelope<T> = { ok: false, error: '' };
  try {
    parsed = JSON.parse(result.stdout.trim()) as Envelope<T>;
  } catch (_) {
    throw new Error('节点返回的验证结果无法识别；请在控制台核对');
  }
  if (!parsed.ok) {
    const message = parsed.error;
    throw new Error(message ? message.slice(0, 300) : '验证命令未完成；请在控制台核对');
  }
  const value: T | undefined = parsed.value;
  if (value === undefined) { throw new Error('节点没有返回验证结果；请在控制台核对'); }
  return value;
}
