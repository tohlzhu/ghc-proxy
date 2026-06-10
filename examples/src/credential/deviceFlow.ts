/**
 * GitHub Device Flow 登录 —— 由操作人员（或前端真人）在浏览器完成授权。
 *
 * 时序：
 *   1) requestDeviceCode(): 取 user_code + verification_uri，吐给前端展示
 *      （"访问 https://github.com/login/device 输入 XXXX-XXXX"）。
 *   2) pollForToken(): 按 interval 轮询，授权完成后返回 gho_ access_token。
 *
 * 返回的 gho_ token 由调用方加密后存入 accounts.oauth_token_enc。
 */
import { config } from "../common/config.js";

export interface DeviceCode {
  device_code: string;
  user_code: string;        // 形如 XXXX-XXXX，展示给真人
  verification_uri: string; // https://github.com/login/device
  expires_in: number;
  interval: number;         // 轮询间隔（秒）
}

const JSON_HEADERS = { Accept: "application/json", "Content-Type": "application/json" };

/** 第 1 步：申请 device code。 */
export async function requestDeviceCode(): Promise<DeviceCode> {
  const res = await fetch(config.github.deviceCodeUrl, {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ client_id: config.github.clientId, scope: config.github.scope }),
  });
  if (!res.ok) throw new Error(`device/code 失败: ${res.status}`);
  return (await res.json()) as DeviceCode;
}

/** 第 2 步：轮询直到用户完成授权，返回 gho_ token。 */
export async function pollForToken(dc: DeviceCode): Promise<string> {
  const deadline = Date.now() + dc.expires_in * 1000;
  let intervalMs = dc.interval * 1000;

  while (Date.now() < deadline) {
    await sleep(intervalMs);
    const res = await fetch(config.github.accessTokenUrl, {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({
        client_id: config.github.clientId,
        device_code: dc.device_code,
        grant_type: "urn:ietf:params:oauth:grant-type:device_code",
      }),
    });
    const data = (await res.json()) as {
      access_token?: string;
      error?: string;
    };

    if (data.access_token) return data.access_token; // gho_ ...
    switch (data.error) {
      case "authorization_pending":
        break;                       // 用户尚未完成，继续轮询
      case "slow_down":
        intervalMs += 5_000;         // GitHub 要求放慢
        break;
      case "expired_token":
        throw new Error("device code 已过期，请重新发起登录。");
      case "access_denied":
        throw new Error("用户拒绝了授权。");
      default:
        // 其它瞬时错误：继续等待下一轮
        break;
    }
  }
  throw new Error("Device Flow 授权超时。");
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
