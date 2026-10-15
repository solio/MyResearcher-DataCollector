export interface SshHostKey {
  type: string;
  base64: string;
  fingerprint: string;
}

export interface SshSession {
  id: number;
  type: string;
  base64: string;
  fingerprint: string;
}

export interface SshConnectOptions {
  host: string;
  port?: number;
  user: string;
  password?: string;
  privateKey?: string;
  passphrase?: string;
  knownHostType?: string;
  knownHostBase64?: string;
}

export interface SshExecResult {
  stdout: string;
  stderr: string;
  exitStatus: number;
}

export function probeHostKey(options: { host: string; port?: number }): Promise<SshHostKey>;
export function connect(options: SshConnectOptions): Promise<SshSession>;
export function exec(id: number, command: string, stdin: string): Promise<SshExecResult>;
export function close(id: number): Promise<boolean>;
