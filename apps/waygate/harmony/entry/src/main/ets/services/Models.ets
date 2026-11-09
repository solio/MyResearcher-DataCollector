export interface Incident {
  id: string;
  node: string;
  name: string;
  stock: string;
  kind: string;
  title: string;
  created: number;
  seen: number;
  snoozed: number;
  online: boolean;
  can_verify: boolean;
}
export interface StockState { stock: string; state: string; window: string; posts: string; pending: string; }
export interface NodeState { id: string; name: string; online: boolean; state: string; stocks: StockState[]; }
export interface Inbox { items: Incident[]; nodes: NodeState[]; last_poll: number; error: string; push_enabled: boolean; push_errors: number; }
export interface SessionResult { message?: string; exit_ip?: string; expires_at?: number; request_id?: number; outcome?: string; }
export interface Session { id: string; phase: string; result: SessionResult; }
export interface Link { url: string; }
export interface ApiError { error?: string; }
export interface Credentials { hub: string; token: string; deviceId: string; push: boolean; pendingClaim: string; }
export interface EmptyReply { ok?: boolean; }

// ---- phone-side direct SSH verification -------------------------------------

// Host, port and signature of the worker-visible HTTP proxy for the blocked task.
export interface RouteInfo {
  host: string;
  port: number;
  endpoint: string;
  mode: string;
  signature: string;
}

// One blocked task. The descriptor only carries identity; url/route come from audit.
export interface DirectTarget {
  instance_id: string | number;
  revision: string | number;
  job: string | number;
  stock: string | number;
  task: string | number;
  request_id: string | number;
  url?: string;
  route?: RouteInfo;
  exit_ip?: string;
  detached?: boolean;
}

export interface VerificationDescriptor {
  protocol: string;
  node_id: string;
  ssh_host_hint: string | null;
  target: DirectTarget;
  worker_command: string;
  source_domains: string[];
  session_ttl_seconds: number;
}

export interface AuditStock { job: string | number; stock: string | number; state: string; }
export interface AuditView {
  state: string;
  instance_id: string | number;
  revision: string | number;
  targets: DirectTarget[];
  stocks: AuditStock[];
  inflight?: boolean;
  probe_pending?: boolean;
}

export interface PrepareValue { paused_stocks: string[]; }
export interface ExitValue { route: RouteInfo; exit_ip: string; }
export interface ProbeValue { before_request_id: number; }
export interface ProbeRow { id: number; outcome: string; }
export interface ResultValue { done: boolean; request?: ProbeRow | null; }
export interface ResumeValue { started: boolean; }

// The worker always answers {"ok":true,"value":...} or {"ok":false,"error":"..."}.
export interface Envelope<T> { ok: boolean; value?: T; error?: string; }

// Durable record written before the one-shot probe leaves the phone.
export interface DirectClaim {
  incident: string;
  node: string;
  target: string;
  worker_command: string;
  before_request_id: number;
  at: number;
}

// SSH login material for one node, stored in the phone Asset Store only.
export interface NodeProfile {
  nodeId: string;
  host: string;
  port: number;
  user: string;
  authType: string;
  secret: string;
  passphrase: string;
  knownHostBase64: string;
  knownHostType: string;
  fingerprint: string;
}

// Packaged channel configuration: endpoint and pinned host key. The credential
// itself lives in a git-ignored file next to this config and is bundled into the
// build only when that file is present locally.
export interface BuiltinNode {
  id: string;
  label: string;
  host: string;
  port: number;
  user: string;
  // 'key' uses the private key in secretFile; 'password' uses it as the password.
  authType: string;
  // Every fingerprint the server may legitimately present. The negotiated type
  // depends on libssh2's algorithm order, so all of them are pinned.
  hostKeys: string[];
  secretFile: string;
  // The hub identifies a node by node_id, which is also the key a stored profile is
  // saved under, so it is the most stable link between a packaged channel and a待办.
  nodeIds?: string[];
  // Other host strings the hub may advertise for the same machine (its own domain, or a
  // LAN address the phone cannot dial). Any of them selects this packaged channel; the
  // connection itself still always uses host.
  aliases?: string[];
}
export interface BuiltinConfig { nodes: BuiltinNode[]; }

// ---- boundary validation -----------------------------------------------------
// JSON from the hub is untyped at runtime. Anything missing here used to surface as a render
// error (undefined.length) deep in the UI, which reads as a crash. Validate once, at the edge,
// and fail with a message the diagnostics panel can show.

function field(raw: Record<string, Object>, key: string): Object | undefined {
  return raw[key];
}

function text(raw: Record<string, Object>, key: string, fallback: string): string {
  const value: Object | undefined = raw[key];
  return typeof value === 'string' ? value as string : fallback;
}

function num(raw: Record<string, Object>, key: string, fallback: number): number {
  const value: Object | undefined = raw[key];
  return typeof value === 'number' && isFinite(value as number) ? value as number : fallback;
}

function flag(raw: Record<string, Object>, key: string, fallback: boolean): boolean {
  const value: Object | undefined = raw[key];
  return typeof value === 'boolean' ? value as boolean : fallback;
}

export function readIncident(value: Object, index: number): Incident {
  if (typeof value !== 'object' || value === null) {
    throw new Error('收件箱第 ' + String(index + 1) + ' 条不是对象');
  }
  const raw = value as Record<string, Object>;
  const id = text(raw, 'id', '');
  if (!/^[a-f0-9]{32}$/.test(id)) {
    throw new Error('收件箱第 ' + String(index + 1) + ' 条缺少合法 id');
  }
  const stock = text(raw, 'stock', '');
  if (!/^[0-9]{6}$/.test(stock)) {
    throw new Error('待办 ' + id.slice(0, 8) + ' 的股票代码无效');
  }
  return {
    id: id,
    node: text(raw, 'node', ''),
    name: text(raw, 'name', ''),
    stock: stock,
    kind: text(raw, 'kind', ''),
    title: text(raw, 'title', ''),
    created: num(raw, 'created', 0),
    seen: num(raw, 'seen', 0),
    snoozed: num(raw, 'snoozed', 0),
    online: flag(raw, 'online', false),
    can_verify: flag(raw, 'can_verify', false)
  };
}

export function readNode(value: Object, index: number): NodeState {
  if (typeof value !== 'object' || value === null) {
    throw new Error('节点列表第 ' + String(index + 1) + ' 项不是对象');
  }
  const raw = value as Record<string, Object>;
  const id = text(raw, 'id', '');
  if (id.length === 0) {
    throw new Error('节点列表第 ' + String(index + 1) + ' 项缺少 id');
  }
  const stocks: StockState[] = [];
  const list: Object | undefined = raw['stocks'];
  if (Array.isArray(list)) {
    for (const item of list as Object[]) {
      if (typeof item !== 'object' || item === null) {
        continue;
      }
      const s = item as Record<string, Object>;
      stocks.push({
        stock: text(s, 'stock', ''),
        state: text(s, 'state', ''),
        window: text(s, 'window', ''),
        posts: text(s, 'posts', ''),
        pending: text(s, 'pending', '')
      });
    }
  }
  return {
    id: id,
    name: text(raw, 'name', id),
    online: flag(raw, 'online', false),
    state: text(raw, 'state', ''),
    stocks: stocks
  };
}

export function readInbox(value: Object): Inbox {
  if (typeof value !== 'object' || value === null) {
    throw new Error('主控返回的收件箱不是对象');
  }
  const raw = value as Record<string, Object>;
  const items: Object | undefined = raw['items'];
  const nodes: Object | undefined = raw['nodes'];
  if (!Array.isArray(items)) {
    throw new Error('主控返回缺少 items 数组');
  }
  if (!Array.isArray(nodes)) {
    throw new Error('主控返回缺少 nodes 数组');
  }
  const safeItems: Incident[] = [];
  let i = 0;
  for (const item of items as Object[]) {
    safeItems.push(readIncident(item, i));
    i += 1;
  }
  const safeNodes: NodeState[] = [];
  i = 0;
  for (const node of nodes as Object[]) {
    safeNodes.push(readNode(node, i));
    i += 1;
  }
  return {
    items: safeItems,
    nodes: safeNodes,
    last_poll: num(raw, 'last_poll', 0),
    error: text(raw, 'error', ''),
    push_enabled: flag(raw, 'push_enabled', false),
    push_errors: num(raw, 'push_errors', 0)
  };
}
