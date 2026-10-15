import { http } from '@kit.NetworkKit';
import { ApiError, Credentials } from './Models';

export class Api {
  credentials: Credentials;

  constructor(credentials: Credentials) {
    this.credentials = credentials;
  }

  async request<T>(path: string, method: http.RequestMethod = http.RequestMethod.GET,
    body: string = ''): Promise<T> {
    const client = http.createHttp();
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
      if (response.responseCode < 200 || response.responseCode >= 300) {
        let message = '主控请求失败：HTTP ' + response.responseCode;
        try {
          const error = JSON.parse(response.result as string) as ApiError;
          if (error.error) { message = error.error; }
        } catch (_) {}
        throw new Error(message);
      }
      return JSON.parse(response.result as string) as T;
    } finally {
      client.destroy();
    }
  }

  post<T>(path: string, body: string = '{}'): Promise<T> {
    return this.request<T>(path, http.RequestMethod.POST, body);
  }
}
