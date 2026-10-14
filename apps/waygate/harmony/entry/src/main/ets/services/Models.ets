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
export interface Credentials { hub: string; token: string; deviceId: string; push: boolean; pendingSession: string; }
export interface EmptyReply { ok?: boolean; }
