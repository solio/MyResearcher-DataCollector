import { http } from '@kit.NetworkKit';
import { BusinessError } from '@kit.BasicServicesKit';
import { ApiError, Credentials } from './Models';

export function readableError(message: string): string {
  if (/Unterminated string|JSONDecodeError|Unexpected end of JSON|Expecting .*delimiter/i.test(message)) {
    return '主控状态数据解析失败；详情见“诊断”';
  }
  return message;
}

export class Api {
  credentials: Credentials;
  lastFailureDetails: string = '';

  constructor(credentials: Credentials) {
    this.credentials = credentials;
  }

  async request<T>(path: string, method: http.RequestMethod = http.RequestMethod.GET,
    body: string = ''): Promise<T> {
    const client = http.createHttp();
    let status: number = 0;
    let characters: number = 0;
    let stage: string = '连接主控';
    try {
      const response = await client.request(this.credentials.hub + path, {
        method: method,
        header: { 'Authorization': 'Bearer ' + this.credentials.token, 'Content-Type': 'application/json' },
        extraData: body,
        connectTimeout: 12000,
        readTimeout: 35000,
        usingCache: false,
        expectDataType: http.HttpDataType.STRING
      });
      status = response.responseCode;
      const text = response.result as string;
      characters = text.length;
      stage = 'HTTP 响应';
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
      this.lastFailureDetails = '记录时间：' + new Date().toLocaleString() +
        '\n接口：' + method + ' ' + path.split('?')[0] +
        '\n阶段：' + stage + '\nHTTP：' + (status > 0 ? status.toString() : '未收到响应') +
        '\n响应长度：' + characters.toString() + ' 个字符' +
        (typeof code === 'number' ? '\n网络错误码：' + code.toString() : '');
      if (stage === '连接主控') { throw new Error('无法连接主控，请检查网络；详情见“诊断”'); }
      throw err;
    } finally {
      client.destroy();
    }
  }

  post<T>(path: string, body: string = '{}'): Promise<T> {
    return this.request<T>(path, http.RequestMethod.POST, body);
  }
}
