/**
 * Refresher Worker —— refresher 角色进程运行。
 *
 * 职责：周期扫描 refresh_at <= now 的账号，提前刷新其 copilot token，
 * 保证「用户长期不访问也不掉线」。多副本部署时靠每账号锁做到单写者。
 *
 * 失败为登录过期时：把账号置 quarantined 并发审计/告警事件（由 Router 负责改路由）。
 */
import type { Redis } from "ioredis";
import { config } from "../common/config.js";
import { exchange, LoginExpiredError } from "./tokenExchange.js";

export interface AccountRepo {
  /** 取到期需刷新、且 status 健康的账号。 */
  listDueForRefresh(now: number, limit: number): Promise<Array<{ id: string }>>;
  /** 解密读出 gho_ token。 */
  loadGhoToken(accountId: string): Promise<string>;
  /** 写回下次刷新时间与 api_base。 */
  markRefreshed(accountId: string, refreshAt: number, apiBase: string): Promise<void>;
  /** 标记登录失效，待操作人员重新登录。 */
  quarantine(accountId: string, reason: string): Promise<void>;
}

export function startRefresher(redis: Redis, repo: AccountRepo, onAudit: (e: object) => void) {
  let stopped = false;

  async function tick() {
    const now = Math.floor(Date.now() / 1000);
    const due = await repo.listDueForRefresh(now, 50);
    for (const acct of due) {
      const lockKey = `lock:account:${acct.id}`;
      const got = await redis.set(lockKey, "refresher", "EX", config.refresh.lockTtlSeconds, "NX");
      if (!got) continue; // 别的副本在处理
      try {
        const tok = await exchange(await repo.loadGhoToken(acct.id));
        const ttl = Math.max(
          30,
          tok.expires_at - Math.floor(Date.now() / 1000) - config.refresh.skewSeconds,
        );
        await redis.set(`copilot_token:${acct.id}`, JSON.stringify(tok), "EX", ttl);
        const nextRefresh = Math.floor(Date.now() / 1000) + tok.refresh_in;
        await repo.markRefreshed(acct.id, nextRefresh, tok.apiBase);
      } catch (err) {
        if (err instanceof LoginExpiredError) {
          await repo.quarantine(acct.id, "login_expired");
          onAudit({ type: "account.quarantined", accountId: acct.id, reason: "login_expired" });
        } else {
          onAudit({ type: "account.refresh_failed", accountId: acct.id, error: String(err) });
        }
      } finally {
        await redis.del(lockKey);
      }
    }
  }

  const timer = setInterval(() => {
    if (!stopped) void tick().catch(() => {});
  }, config.refresh.scanIntervalMs);

  return () => {
    stopped = true;
    clearInterval(timer);
  };
}
