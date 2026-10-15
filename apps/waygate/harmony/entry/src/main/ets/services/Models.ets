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
  hostKey: string;
  secretFile: string;
}
export interface BuiltinConfig { nodes: BuiltinNode[]; }
