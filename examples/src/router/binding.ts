/**
 * 1:1 粘性绑定与空闲账号分配。
 *
 * 不变量（由 DB 双 UNIQUE 约束保证）：
 *   - 一个 user 至多绑定一个 account；
 *   - 一个 account 至多被一个 user 绑定。
 *
 * 分配用 SELECT ... FOR UPDATE SKIP LOCKED 原子地从 idle 池挑一个健康账号，
 * 配合唯一约束，天然防止并发把同一账号分给两个用户。
 */
import type { Pool, PoolClient } from "pg";

export class NoIdleAccountError extends Error {}

/** 取 user 当前绑定的健康账号；无绑定或账号不健康则分配一个新的。 */
export async function resolveAccountForUser(pool: Pool, userId: string): Promise<string> {
  const existing = await pool.query(
    `SELECT b.account_id
       FROM bindings b
       JOIN accounts a ON a.id = b.account_id
      WHERE b.user_id = $1 AND a.status IN ('idle','bound')`,
    [userId],
  );
  if (existing.rowCount && existing.rows[0].account_id) {
    return existing.rows[0].account_id as string;
  }
  return assignIdleAccount(pool, userId);
}

/** 在一个事务里：解除旧绑定（若有）→ 锁定一个 idle 账号 → 建立 1:1 绑定。 */
export async function assignIdleAccount(pool: Pool, userId: string): Promise<string> {
  const client: PoolClient = await pool.connect();
  try {
    await client.query("BEGIN");

    // 解除该 user 已失效的旧绑定，把旧账号交还（除非已隔离）。
    await client.query(`DELETE FROM bindings WHERE user_id = $1`, [userId]);

    // 原子挑选一个空闲健康账号（跳过被其它事务锁住的行）。
    const pick = await client.query(
      `SELECT id FROM accounts
        WHERE status = 'idle'
        ORDER BY last_seen_at NULLS FIRST
        FOR UPDATE SKIP LOCKED
        LIMIT 1`,
    );
    if (pick.rowCount === 0) {
      await client.query("ROLLBACK");
      throw new NoIdleAccountError("无空闲健康账号，请操作人员补充/恢复账号。");
    }
    const accountId = pick.rows[0].id as string;

    // 双 UNIQUE 约束确保 1:1；任何竞态都会在此因冲突回滚。
    await client.query(
      `INSERT INTO bindings (user_id, account_id, bound_at, last_active_at, status)
       VALUES ($1, $2, now(), now(), 'active')`,
      [userId, accountId],
    );
    await client.query(`UPDATE accounts SET status = 'bound' WHERE id = $1`, [accountId]);

    await client.query("COMMIT");
    return accountId;
  } catch (e) {
    await client.query("ROLLBACK").catch(() => {});
    throw e;
  } finally {
    client.release();
  }
}

/** 账号登录失效时：隔离账号并把 user 改路由到新的空闲账号。 */
export async function rerouteOnFailure(pool: Pool, userId: string, accountId: string): Promise<string> {
  await pool.query(
    `UPDATE accounts SET status = 'quarantined', last_error = 'login_expired' WHERE id = $1`,
    [accountId],
  );
  return assignIdleAccount(pool, userId);
}
