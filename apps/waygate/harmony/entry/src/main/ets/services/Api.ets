import { http } from '@kit.NetworkKit';
import { BusinessError } from '@kit.BasicServicesKit';
import { ApiError, Credentials } from './Models';

// A weak link needs more patience than a good one: the same request that dies in 10 s on a
// bad link to a node may still succeed at 40 s. The timeouts therefore step up while
// requests keep dying before they reach the hub, and step back down once requests are
// healthy again, instead of being fixed at one value that is either too slow or too short.
const TIMEOUT_TIERS: number[][] = [[10000, 30000], [15000, 45000], [25000, 70000], [40000, 100000]];

export function readableError(message: string): string {
  if (/Unterminated string|JSONDecodeError|Unexpected end of JSON|Expecting .*delimiter/i.test(message)) {
    return '主控状态数据解析失败；详情见“诊断”';
  }
  return message;
}

export class Api {
  credentials: Credentials;
  lastFailureDetails: string = '';
  lastFailureStage: string = '';
  // Index into TIMEOUT_TIERS; raised by a failure that never reached the hub.
  tier: number = 0;

  constructor(credentials: Credentials) {
    this.credentials = credentials;
  }

  private connectTimeout(): number {
    return TIMEOUT_TIERS[this.tier][0];
  }

  private readTimeout(): number {
    return TIMEOUT_TIERS[this.tier][1];
  }

  // Repeating a request is only justified when it never reached the hub: a timeout or
  // a DNS/TCP failure on a weak link is transient, while a 4xx/5xx answer means the hub
  // is alive and answered, so repeating it would only waste the operator's time.
  async request<T>(path: string, method: http.RequestMethod = http.RequestMethod.GET,
    body: string = '', attempts: number = 1): Promise<T> {
    const total = attempts > 1 ? attempts : 1;
    let lastError: Error = new Error('主控请求未完成；详情见“诊断”');
    for (let index = 0; index < total; index++) {
      try {
        return await this.attempt<T>(path, method, body);
      } catch (err) {
        lastError = err as Error;
        if (this.lastFailureStage !== '连接主控') { throw lastError; }
        if (index + 1 < total) { await new Promise<void>((r) => setTimeout(r, 900 * (index + 1))); }
      }
    }
    throw lastError;
  }

  private async attempt<T>(path: string, method: http.RequestMethod, body: string): Promise<T> {
    const client = http.createHttp();
    let status: number = 0;
    let characters: number = 0;
    let stage: string = '连接主控';
    try {
      const response = await client.request(this.credentials.hub + path, {
        method: method,
        header: { 'Authorization': 'Bearer ' + this.credentials.token, 'Content-Type': 'application/json' },
        extraData: body,
        connectTimeout: this.connectTimeout(),
        readTimeout: this.readTimeout(),
        usingCache: false,
        expectDataType: http.HttpDataType.STRING
      });
      status = response.responseCode;
      const text = response.result as string;
      characters = text.length;
      stage = 'HTTP 响应';
      // The hub answered, so this link is healthy whatever the status code says.
      if (this.tier > 0) { this.tier--; }
      if (response.responseCode < 200 || response.responseCode >= 300) {
        let message = '主控请求失败：HTTP ' + response.responseCode;
        try {
          const error = JSON.parse(text) as ApiError;
          if (error && typeof error.error === 'string' && error.error) { message = readableError(error.error); }
        } catch (_) {}
        throw new Error(message.split(this.credentials.token).join('[已隐藏]').slice(0, 400));
      }
      stage = '解析 JSON';
      try {
        return JSON.parse(text) as T;
      } catch (_) {
        throw new Error('主控返回的数据不完整或格式错误；详情见“诊断”');
      }
    } catch (err) {
      const code = (err as BusinessError).code;
      this.lastFailureStage = stage;
      this.lastFailureDetails = '记录时间：' + new Date().toLocaleString() +
        '\n接口：' + method + ' ' + path.split('?')[0] +
        '\n阶段：' + stage + '\nHTTP：' + (status > 0 ? status.toString() : '未收到响应') +
        '\n响应长度：' + characters.toString() + ' 个字符' +
        (typeof code === 'number' ? '\n网络错误码：' + code.toString() : '');
      if (stage === '连接主控') {
        // Nothing arrived, so this link needs more patience next time.
        if (this.tier < TIMEOUT_TIERS.length - 1) { this.tier++; }
        throw new Error('无法连接主控，请检查网络；详情见“诊断”');
      }
      const failure = err as Error;
      throw new Error(failure.message || '主控请求未完成；详情见“诊断”');
    } finally {
      client.destroy();
    }
  }

  post<T>(path: string, body: string = '{}'): Promise<T> {
    return this.request<T>(path, http.RequestMethod.POST, body);
  }
}
