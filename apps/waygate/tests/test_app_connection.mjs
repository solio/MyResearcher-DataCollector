// Execute the actual request/refresh methods with platform APIs stubbed.
// Node >= 22.13; this does not compile ArkUI or replace device acceptance.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
import test from 'node:test';
import vm from 'node:vm';

const sourceRoot = new URL('../harmony/entry/src/main/ets/', import.meta.url);
const apiSource = readFileSync(new URL('services/Api.ets', sourceRoot), 'utf8');
const indexSource = readFileSync(new URL('pages/Index.ets', sourceRoot), 'utf8');
const token = 'fixture-private-token';

function javascript(source) {
  return stripTypeScriptTypes(source.replace(/^import .*;\r?$/gm, '').replace(/^export /gm, ''));
}

function setup(request) {
  let destroyed = 0;
  const context = vm.createContext({
    http: { RequestMethod: { GET: 'GET', POST: 'POST' }, HttpDataType: { STRING: 'string' },
      createHttp: () => ({ request, destroy: () => { destroyed++; } }) },
    Vault: class {}, NodeSsh: class {}, webview: { WebviewController: class {} },
    runtime: { incident: '', session: '', dispatching: false }
  });
  vm.runInContext(javascript(apiSource) + '\nglobalThis.ApiClass = Api;', context);
  // Only the normal class fields/methods; do not pretend Node understands ArkUI builders.
  // Cut at the first @Builder regardless of CRLF (Windows checkouts use CRLF).
  const indexMethods = indexSource.slice(0, indexSource.search(/\n  @Builder\r?\n/)) + '}';
  const plain = indexMethods.replace(/^@Entry\r?\n|^@Component\r?\n/gm, '')
    .replace('struct Index {', 'class Index {').replace(/@State\s+/g, '')
    .replace(/@StorageLink\('[^']+'\)\s*@Watch\('[^']+'\)\s*/g, '');
  vm.runInContext(javascript(plain) + '\nglobalThis.IndexClass = Index;', context);
  const api = new context.ApiClass({ hub: 'https://console.example/collector', token });
  const index = new context.IndexClass();
  index.api = api;
  index.credentials = api.credentials;
  index.foreground = true;
  index.incidentLink = '';
  return { api, index, destroyed: () => destroyed };
}

function inbox(error = '') {
  return { items: [], nodes: [], last_poll: 1700000000, error, push_enabled: false, push_errors: 0 };
}

test('incomplete JSON gives useful diagnostics without exposing the response or token', async () => {
  const body = '{"secret":"' + token;
  const { api, destroyed } = setup(async () => ({ responseCode: 200, result: body }));
  await assert.rejects(api.request('/v1/inbox'), /数据不完整或格式错误/);
  assert.match(api.lastFailureDetails, /GET \/v1\/inbox/);
  assert.match(api.lastFailureDetails, /HTTP：200/);
  assert.match(api.lastFailureDetails, /阶段：解析 JSON/);
  assert.match(api.lastFailureDetails, new RegExp(body.length + ' 个字符'));
  assert.ok(!api.lastFailureDetails.includes(token));
  assert.equal(destroyed(), 1);
});

test('HTTP errors and network errors retain their category without leaking credentials', async () => {
  const rejected = setup(async () => ({ responseCode: 401, result: JSON.stringify({ error: 'invalid ' + token }) }));
  await assert.rejects(rejected.api.request('/v1/inbox'), err => {
    assert.ok(!err.message.includes(token));
    return true;
  });
  assert.match(rejected.api.lastFailureDetails, /HTTP：401/);
  const offline = setup(async () => { throw Object.assign(new Error('private ' + token), { code: 2300006 }); });
  await assert.rejects(offline.api.request('/v1/inbox'), /无法连接主控/);
  assert.match(offline.api.lastFailureDetails, /网络错误码：2300006/);
  assert.ok(!offline.api.lastFailureDetails.includes(token));
});

test('successful polling clears an old server error, preserves operation result and diagnostic evidence', async () => {
  let broken = true;
  const { index } = setup(async () => ({ responseCode: 200,
    result: JSON.stringify(inbox(broken ? 'Unterminated string starting at: line 1 column 506 (char 505)' : '')) }));
  index.message = '验证通过，已继续采集';
  await index.refresh();
  assert.match(index.syncMessage, /主控状态数据解析失败/);
  assert.match(index.lastSyncFailure, /Unterminated string/);
  broken = false;
  await index.refresh();
  assert.equal(index.syncMessage, '');
  assert.equal(index.message, '验证通过，已继续采集');
  index.openDiagnostics();
  assert.match(index.diagnosticSnapshot, /当前状态：连接正常/);
  assert.match(index.diagnosticSnapshot, /当前无同步故障/);
  assert.match(index.diagnosticSnapshot, /Unterminated string/);
  assert.ok(!index.diagnosticSnapshot.includes(token));
});

test('a subsequent valid response clears a client parse failure', async () => {
  let broken = true;
  const { index } = setup(async () => ({ responseCode: 200,
    result: broken ? '{"nodes":[' : JSON.stringify(inbox()) }));
  await index.refresh();
  assert.match(index.syncMessage, /数据不完整或格式错误/);
  broken = false;
  await index.refresh();
  assert.equal(index.syncMessage, '');
  assert.match(index.lastSyncFailure, /阶段：解析 JSON/);
});
