/**
 * gho_ OAuth token  →  短效 Copilot token 的交换 + Redis 缓存。
 *
 * 核心接口：
 *   GET {github.apiBase}/copilot_internal/v2/token
 *   Authorization: token <gho_...>
 * 返回 { token, expires_at, refresh_in, endpoints: { api } }。
 *
 * 缓存键 copilot_token:{accountId}，TTL = expires_at - now - skew。
 * 用每账号锁避免并发重复交换（"惊群"）。
 */
import type { Redis } from "ioredis";
import { config } from "../common/config.js";

export interface CopilotToken {
  token: string;        // 实际用于模型 API 的 Bearer（含 tid/exp/refresh_in 等内嵌字段）
  expires_at: number;   // epoch 秒
  refresh_in: number;   // 秒
  apiBase: string;      // endpoints.api，例如 https://api.<plan>.githubcopilot.com
}

const cacheKey = (accountId: string) => `copilot_token:${accountId}`;
const lockKey = (accountId: string) => `lock:account:${accountId}`;

/** 调 GitHub 接口换取短效 token（无缓存）。 */
export async function exchange(ghoToken: string): Promise<CopilotToken> {
  const res = await fetch(config.github.apiBase + config.github.tokenExchangePath, {
    headers: {
      Authorization: `token ${ghoToken}`,
      Accept: "application/json",
      "User-Agent": config.upstreamHeaders["User-Agent"],
    },
  });
  if (res.status === 401) throw new LoginExpiredError("gho_ token 失效，需要重新登录。");
  if (!res.ok) throw new Error(`token 交换失败: ${res.status}`);

  const data = (await res.json()) as {
    token: string;
    expires_at: number;
    refresh_in: number;
    endpoints?: { api?: string };
  };
  return {
    token: data.token,
    expires_at: data.expires_at,
    refresh_in: data.refresh_in,
    apiBase: data.endpoints?.api ?? "https://api.individual.githubcopilot.com",
  };
}

/** 读缓存；miss 时抢锁交换并写缓存。供热路径调用。 */
export async function getCopilotToken(
  redis: Redis,
  accountId: string,
  loadGhoToken: () => Promise<string>,
): Promise<CopilotToken> {
  const cached = await redis.get(cacheKey(accountId));
  if (cached) return JSON.parse(cached) as CopilotToken;

  // 抢每账号锁，避免并发重复交换。
  const locked = await redis.set(lockKey(accountId), "1", "EX", config.refresh.lockTtlSeconds, "NX");
  if (!locked) {
    // 别人正在刷新：短暂退避后读缓存。
    await sleep(150);
    const again = await redis.get(cacheKey(accountId));
    if (again) return JSON.parse(again) as CopilotToken;
  }

  try {
    const tok = await exchange(await loadGhoToken());
    const ttl = Math.max(30, tok.expires_at - Math.floor(Date.now() / 1000) - config.refresh.skewSeconds);
    await redis.set(cacheKey(accountId), JSON.stringify(tok), "EX", ttl);
    return tok;
  } finally {
    await redis.del(lockKey(accountId));
  }
}

export class LoginExpiredError extends Error {}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
