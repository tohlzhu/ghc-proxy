/**
 * refresher 角色的进程入口（k8s refresher-deployment 的 entrypoint）。
 *
 * 与 proxy 角色同一镜像、不同入口：装配 Redis / PG / Kafka 客户端，
 * 实现 AccountRepo，然后启动周期刷新 worker。
 *
 * 这里 AccountRepo 的 SQL 以骨架给出，落地时补全查询与加解密细节。
 */
import { Redis } from "ioredis";
import { Pool } from "pg";
import { config } from "../common/config.js";
import { decryptToken } from "../common/crypto.js";
import { startRefresher, type AccountRepo } from "./refresher.js";

function buildRepo(pool: Pool): AccountRepo {
  return {
    async listDueForRefresh(now, limit) {
      const r = await pool.query(
        `SELECT id FROM accounts
          WHERE status IN ('idle','bound')
            AND refresh_at IS NOT NULL
            AND refresh_at <= to_timestamp($1)
          ORDER BY refresh_at
          LIMIT $2`,
        [now, limit],
      );
      return r.rows.map((row) => ({ id: row.id as string }));
    },
    async loadGhoToken(accountId) {
      const r = await pool.query(`SELECT oauth_token_enc FROM accounts WHERE id = $1`, [accountId]);
      const blob = r.rows[0]?.oauth_token_enc as Buffer | undefined;
      if (!blob) throw new Error(`账号 ${accountId} 无 oauth_token`);
      return decryptToken(blob);
    },
    async markRefreshed(accountId, refreshAt, apiBase) {
      await pool.query(
        `UPDATE accounts
            SET refresh_at = to_timestamp($2), api_base = $3,
                last_seen_at = now(), last_error = NULL, updated_at = now()
          WHERE id = $1`,
        [accountId, refreshAt, apiBase],
      );
    },
    async quarantine(accountId, reason) {
      await pool.query(
        `UPDATE accounts SET status = 'quarantined', last_error = $2, updated_at = now()
          WHERE id = $1`,
        [accountId, reason],
      );
    },
  };
}

function main() {
  const pool = new Pool({ connectionString: config.postgres.url });
  const redis = new Redis(config.redis.url);
  const repo = buildRepo(pool);

  // 审计/告警事件：此处仅打印；落地时投递到 Kafka config.kafka.topics.audit。
  const onAudit = (e: object) => console.log(JSON.stringify({ topic: config.kafka.topics.audit, ...e }));

  const stop = startRefresher(redis, repo, onAudit);

  const shutdown = async () => {
    stop();
    await Promise.allSettled([redis.quit(), pool.end()]);
    process.exit(0);
  };
  process.on("SIGTERM", shutdown);
  process.on("SIGINT", shutdown);
}

main();
