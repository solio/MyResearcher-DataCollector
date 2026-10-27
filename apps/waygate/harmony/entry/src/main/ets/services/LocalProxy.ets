import { socket } from '@kit.NetworkKit';
import { util } from '@kit.ArkTS';
import { BusinessError } from '@kit.BasicServicesKit';
import { hilog } from '@kit.PerformanceAnalysisKit';

// ArkWeb talks to this app-local HTTP proxy. Each browser CONNECT is validated
// against the same rules the hub relay used, then forwarded as raw bytes over the
// native SSH direct-tcpip listener to the worker's own proxied exit. Neither the
// hub nor the phone decrypts browser traffic.
const LOG_DOMAIN = 0x3200;
const LOG_TAG = 'waygate_proxy';
const HEAD_LIMIT = 16384;
const HEAD_TIMEOUT = 12000;
const OUTSTANDING_LIMIT = 1024 * 1024;
// A page loads its document, stylesheets, scripts and ads over many sockets, and keep-alive
// lets idle ones linger. 24 was low enough that a captcha page could exhaust the pool and have
// a main-frame request closed with no response at all, which the browser reports as
// ERR_CONNECTION_CLOSED — indistinguishable from a dead tunnel.
const MAX_CONNECTIONS = 64;
const IDLE_PIPE_MS = 45000;

export class ReadyFlag { value: boolean = false; }

function ascii(text: string): ArrayBuffer {
  const out = new Uint8Array(text.length);
  for (let index = 0; index < text.length; index++) { out[index] = text.charCodeAt(index) & 0xff; }
  return out.buffer as ArrayBuffer;
}

function joined(first: ArrayBuffer, second: ArrayBuffer): ArrayBuffer {
  if (first.byteLength === 0) { return second; }
  if (second.byteLength === 0) { return first; }
  const out = new Uint8Array(first.byteLength + second.byteLength);
  out.set(new Uint8Array(first), 0);
  out.set(new Uint8Array(second), first.byteLength);
  return out.buffer as ArrayBuffer;
}

function headEnd(data: ArrayBuffer): number {
  const bytes = new Uint8Array(data);
  for (let index = 0; index + 3 < bytes.length; index++) {
    if (bytes[index] === 13 && bytes[index + 1] === 10 && bytes[index + 2] === 13 && bytes[index + 3] === 10) {
      return index + 4;
    }
  }
  return -1;
}

// Exactly one HTTPS CONNECT; never forward an arbitrary HTTP proxy request.
function connectTarget(head: ArrayBuffer): string {
  const text = new util.TextDecoder().decodeToString(new Uint8Array(head));
  const parts = text.split('\r\n')[0].split(' ');
  if (parts.length !== 3 || parts[0] !== 'CONNECT') { return ''; }
  if (parts[2] !== 'HTTP/1.1' && parts[2] !== 'HTTP/1.0') { return ''; }
  const colon = parts[1].lastIndexOf(':');
  if (colon <= 0 || parts[1].substring(colon + 1) !== '443') { return ''; }
  const host = parts[1].substring(0, colon).toLowerCase();
  if (host.length === 0 || host.length > 253 || host.indexOf('..') >= 0) { return ''; }
  if (!/^[a-z0-9]+(?:[a-z0-9.-]*[a-z0-9])?$/.test(host)) { return ''; }
  if (/^[0-9]+(?:\.[0-9]+)*$/.test(host)) { return ''; }
  return host;
}

// The allowlist is the pre-check guard only: before the shared exit is confirmed
// nothing but the neutral probe host may pass. Once markReady() has run this channel
// is the same thing the desktop `ssh -L` path provides, and that path has no domain
// filter at all. Keeping one broke page rendering, because stylesheets and scripts
// come from hosts that are not in the descriptor's source domains (observed:
// gbfek.dfcfw.com, a dfcfw static host, refused with allowed=no while ready=yes,
// which left the operator looking at raw unstyled HTML).
function permitted(host: string, ready: boolean): boolean {
  if (host === 'api.ipify.org') { return true; }
  if (!ready) { return false; }
  return true;
}

class Pipe {
  private client: socket.TCPSocketConnection;
  private port: number;
  private domains: string[];
  private ready: ReadyFlag;
  private onClose: (id: number) => void;
  private upstream: socket.TCPSocket | undefined = undefined;
  private head: ArrayBuffer = new ArrayBuffer(0);
  private opened: boolean = false;
  private closed: boolean = false;
  private timer: number = -1;
  private lastActive: number = Date.now();
  private toUpstream: Promise<void> = Promise.resolve();
  private toClient: Promise<void> = Promise.resolve();
  private upOutstanding: number = 0;
  private downOutstanding: number = 0;

  constructor(client: socket.TCPSocketConnection, port: number, domains: string[], ready: ReadyFlag,
    onClose: (id: number) => void) {
    this.client = client;
    this.port = port;
    this.domains = domains;
    this.ready = ready;
    this.onClose = onClose;
    client.on('message', (message: socket.SocketMessageInfo) => this.fromClient(message.message));
    client.on('close', () => this.close());
    client.on('error', (_: BusinessError) => this.close());
    this.timer = setTimeout(() => this.close(), HEAD_TIMEOUT);
  }

  private fromClient(data: ArrayBuffer): void {
    if (this.closed) { return; }
    this.lastActive = Date.now();
    if (!this.opened) {
      this.head = joined(this.head, data);
      if (this.head.byteLength > HEAD_LIMIT) { this.close(); return; }
      const end = headEnd(this.head);
      if (end < 0) { return; }
      const host = connectTarget(this.head);
      const allowed = host.length > 0 && permitted(host, this.ready.value);
      hilog.info(LOG_DOMAIN, LOG_TAG,
        'CONNECT host=%{public}s allowed=%{public}s ready=%{public}s domains=%{public}d',
        host, allowed ? 'yes' : 'no', this.ready.value ? 'yes' : 'no', this.domains.length);
      if (!allowed) {
        this.refuse(host);
        return;
      }
      const rest = this.head.slice(end);
      this.opened = true;
      clearTimeout(this.timer);
      this.toUpstream = this.openUpstream(host, rest);
      return;
    }
    this.push(data);
  }

  // A readable refusal body, never an empty 403: the empty body rendered as a blank
  // page and made a policy refusal indistinguishable from a broken tunnel.
  private refuse(host: string): void {
    const reason = host.length === 0
      ? '无法识别的 CONNECT 目标（只接受 CONNECT host:443）'
      : '该域名在出口核对完成前不可通行：' + host +
        '（核对通过后不再限制域名；核对前仅放行出口测量用的 api.ipify.org）';
    const body = ascii(reason);
    const head = ascii('HTTP/1.1 403 Forbidden\r\nContent-Type: text/plain; charset=utf-8\r\nConnection: close' +
      '\r\nContent-Length: ' + body.byteLength.toString() + '\r\n\r\n');
    this.client.send({ data: joined(head, body) }).catch(() => {});
    this.close();
  }

  private async openUpstream(host: string, rest: ArrayBuffer): Promise<void> {
    const upstream = socket.constructTCPSocketInstance();
    this.upstream = upstream;
    upstream.on('message', (message: socket.SocketMessageInfo) => this.fromUpstream(message.message));
    upstream.on('close', () => this.close());
    upstream.on('error', (_: BusinessError) => this.close());
    try {
      await upstream.connect({ address: { address: '127.0.0.1', port: this.port, family: 1 }, timeout: HEAD_TIMEOUT });
      if (this.closed) { return; }
      await upstream.send({ data: ascii('CONNECT ' + host + ':443 HTTP/1.1\r\nHost: ' + host + ':443\r\n\r\n') });
      if (rest.byteLength > 0) { await upstream.send({ data: rest }); }
      hilog.info(LOG_DOMAIN, LOG_TAG, 'upstream tunnel ready host=%{public}s rest=%{public}d',
        host, rest.byteLength);
    } catch (_) {
      hilog.error(LOG_DOMAIN, LOG_TAG, 'upstream tunnel failed host=%{public}s', host);
      this.close();
    }
  }

  private push(data: ArrayBuffer): void {
    if (this.closed) { return; }
    this.upOutstanding += data.byteLength;
    if (this.upOutstanding > OUTSTANDING_LIMIT) { this.close(); return; }
    const base = this.toUpstream;
    this.toUpstream = base.then(async () => {
      if (!this.closed && this.upstream) { await this.upstream.send({ data: data }); }
      this.upOutstanding -= data.byteLength;
    }).catch(() => this.close());
  }

  private fromUpstream(data: ArrayBuffer): void {
    if (this.closed) { return; }
    this.lastActive = Date.now();
    this.downOutstanding += data.byteLength;
    if (this.downOutstanding > OUTSTANDING_LIMIT) { this.close(); return; }
    const base = this.toClient;
    this.toClient = base.then(async () => {
      if (!this.closed) { await this.client.send({ data: data }); }
      this.downOutstanding -= data.byteLength;
    }).catch(() => this.close());
  }

  // Keep-alive sockets the browser is done with still hold a slot; they are closed before the
  // pool is declared full, so a stale idle connection can never refuse a live request.
  idleFor(ms: number): boolean {
    return Date.now() - this.lastActive > ms;
  }

  close(): void {
    if (this.closed) { return; }
    this.closed = true;
    clearTimeout(this.timer);
    const upstream = this.upstream;
    this.upstream = undefined;
    if (upstream) { upstream.close().catch(() => {}); }
    this.client.close().catch(() => {});
    this.onClose(this.client.clientId);
  }
}

export class LocalProxy {
  private server: socket.TCPSocketServer | undefined = undefined;
  private pipes: Map<number, Pipe> = new Map();
  private ready: ReadyFlag = new ReadyFlag();

  async start(forwardPort: number, domains: string[]): Promise<string> {
    await this.stop();
    this.ready = new ReadyFlag();
    const server = socket.constructTCPSocketServerInstance();
    this.server = server;
    try {
      await server.listen({ address: '127.0.0.1', family: 1, port: 0 });
      const local = await server.getLocalAddress();
      server.on('connect', (client: socket.TCPSocketConnection) => {
        if (!this.server) { client.close().catch(() => {}); return; }
        if (this.pipes.size >= MAX_CONNECTIONS) { this.sweep(); }
        if (this.pipes.size >= MAX_CONNECTIONS) {
          hilog.error(LOG_DOMAIN, LOG_TAG, 'connection pool full size=%{public}d', this.pipes.size);
          client.close().catch(() => {});
          return;
        }
        const pipe = new Pipe(client, forwardPort, domains, this.ready,
          (id: number) => { this.pipes.delete(id); });
        this.pipes.set(client.clientId, pipe);
      });
      return 'http://127.0.0.1:' + local.port;
    } catch (_) {
      await this.stop();
      throw new Error('无法建立应用内验证通道');
    }
  }

  private sweep(): void {
    this.pipes.forEach((pipe: Pipe, id: number) => {
      if (pipe.idleFor(IDLE_PIPE_MS)) { pipe.close(); this.pipes.delete(id); }
    });
  }

  // Called only after the browser's neutral exit check matched the node exit.
  markReady(): void { this.ready.value = true; }

  async stop(): Promise<void> {
    this.pipes.forEach((pipe: Pipe) => pipe.close());
    this.pipes.clear();
    const server = this.server;
    this.server = undefined;
    if (server) { await server.close().catch(() => {}); }
  }
}
