import { socket, webSocket } from '@kit.NetworkKit';
import { BusinessError } from '@kit.BasicServicesKit';

// One bounded binary WSS stream per browser TCP connection. TLS remains between
// ArkWeb and the source. Neither this client nor the hub decrypts browser traffic.
class Pipe {
  private ws: webSocket.WebSocket = webSocket.createWebSocket();
  private opened: boolean = false;
  private closed: boolean = false;
  private pending: ArrayBuffer[] = [];
  private queuedBytes: number = 0;
  private downloadBytes: number = 0;
  private toHub: Promise<void> = Promise.resolve();
  private toBrowser: Promise<void> = Promise.resolve();
  private timer: number = -1;
  private client: socket.TCPSocketConnection;
  private onClose: (id: number) => void;

  constructor(client: socket.TCPSocketConnection, url: string, token: string,
    onClose: (id: number) => void) {
    this.client = client;
    this.onClose = onClose;
    client.on('message', (message: socket.SocketMessageInfo) => {
      if (this.closed) { return; }
      this.queuedBytes += message.message.byteLength;
      if (this.queuedBytes > 1024 * 1024) { this.close(); return; }
      if (!this.opened) { this.pending.push(message.message); }
      else { this.send(message.message); }
    });
    client.on('close', () => this.close());
    client.on('error', (_: BusinessError) => this.close());
    this.ws.on('open', (_: BusinessError, __: Object) => {
      if (this.closed) { return; }
      clearTimeout(this.timer);
      this.opened = true;
      this.pending.forEach((data: ArrayBuffer) => this.send(data));
      this.pending = [];
    });
    this.ws.on('message', (_: BusinessError, data: string | ArrayBuffer) => {
      if (typeof data === 'string') { this.close(); return; }
      const binary = data as ArrayBuffer;
      this.downloadBytes += binary.byteLength;
      if (this.downloadBytes > 1024 * 1024) { this.close(); return; }
      this.toBrowser = this.toBrowser.then(async () => {
        if (!this.closed) { await this.client.send({ data: binary }); }
        this.downloadBytes -= binary.byteLength;
      }).catch(() => this.close());
    });
    this.ws.on('close', () => this.close());
    this.ws.on('error', (_: BusinessError) => this.close());
    this.timer = setTimeout(() => this.close(), 12000);
    this.ws.connect(url, { header: { 'Authorization': 'Bearer ' + token } }).catch(() => this.close());
  }

  private send(data: ArrayBuffer): void {
    this.toHub = this.toHub.then(async () => {
      if (!this.closed) { await this.ws.send(data); }
      this.queuedBytes -= data.byteLength;
    }).catch(() => this.close());
  }

  close(): void {
    if (this.closed) { return; }
    this.closed = true;
    clearTimeout(this.timer);
    this.pending = [];
    this.ws.close().catch(() => {});
    this.client.close().catch(() => {});
    this.onClose(this.client.clientId);
  }
}

export class LocalProxy {
  private server: socket.TCPSocketServer | undefined = undefined;
  private pipes: Map<number, Pipe> = new Map();

  async start(hub: string, session: string, token: string): Promise<string> {
    await this.stop();
    const server = socket.constructTCPSocketServerInstance();
    this.server = server;
    try {
      await server.listen({ address: '127.0.0.1', family: 1, port: 0 });
      const local = await server.getLocalAddress();
      const wss = hub.replace(/^https:/, 'wss:') + '/v1/sessions/' + session + '/relay';
      server.on('connect', (client: socket.TCPSocketConnection) => {
        if (this.pipes.size >= 24 || !this.server) { client.close().catch(() => {}); return; }
        const pipe = new Pipe(client, wss, token, (id: number) => { this.pipes.delete(id); });
        this.pipes.set(client.clientId, pipe);
      });
      return 'http://127.0.0.1:' + local.port;
    } catch (_) {
      await this.stop();
      throw new Error('无法建立应用内验证通道');
    }
  }

  async stop(): Promise<void> {
    this.pipes.forEach((pipe: Pipe) => pipe.close());
    this.pipes.clear();
    const server = this.server;
    this.server = undefined;
    if (server) { await server.close().catch(() => {}); }
  }
}
