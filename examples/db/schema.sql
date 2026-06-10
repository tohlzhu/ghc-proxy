-- GHC Proxy —— PostgreSQL DDL（示例，占位符，无真实数据）。
-- 设计要点见 ghc-proxy-design.md 第 5 节。

CREATE EXTENSION IF NOT EXISTS "pgcrypto";  -- gen_random_uuid()

-- 后端 GHC 账号池 -----------------------------------------------------------
CREATE TABLE accounts (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  login           TEXT NOT NULL,                 -- GHC 登录名（占位，非敏感）
  host            TEXT NOT NULL DEFAULT 'https://github.com',
  oauth_token_enc BYTEA,                         -- gho_ token：AEAD 信封加密后的二进制；明文绝不入库
  plan            TEXT,                          -- individual|business|enterprise
  api_base        TEXT,                          -- endpoints.api，登录/刷新时写入
  status          TEXT NOT NULL DEFAULT 'logging_in',
                  -- logging_in|idle|bound|quarantined|disabled
  refresh_at      TIMESTAMPTZ,                   -- 下次主动刷新时间（now + refresh_in）
  last_error      TEXT,
  last_seen_at    TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (host, login)
);
CREATE INDEX idx_accounts_idle    ON accounts (status) WHERE status = 'idle';
CREATE INDEX idx_accounts_refresh ON accounts (refresh_at)
       WHERE status IN ('idle','bound');

-- 前端用户 ------------------------------------------------------------------
CREATE TABLE users (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  external_id TEXT UNIQUE,                       -- 企业内部用户标识（占位）
  display_name TEXT,
  status      TEXT NOT NULL DEFAULT 'active',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 前端 API Key（仅存哈希）---------------------------------------------------
CREATE TABLE api_keys (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  key_hash     BYTEA NOT NULL UNIQUE,            -- 例如 SHA-256(key)；明文 key 只在签发时返回一次
  name         TEXT,
  scopes       TEXT[] NOT NULL DEFAULT '{}',
  rate_limit   INT,                              -- 每分钟请求上限（NULL=默认）
  status       TEXT NOT NULL DEFAULT 'active',
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at TIMESTAMPTZ
);
CREATE INDEX idx_api_keys_user ON api_keys (user_id);

-- 1:1 粘性绑定（双 UNIQUE = 严格 1:1）---------------------------------------
CREATE TABLE bindings (
  user_id      UUID NOT NULL UNIQUE REFERENCES users(id)    ON DELETE CASCADE,
  account_id   UUID NOT NULL UNIQUE REFERENCES accounts(id) ON DELETE CASCADE,
  bound_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_active_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  status       TEXT NOT NULL DEFAULT 'active',  -- active|released
  PRIMARY KEY (user_id)
);

-- 进行中的 Device Flow 会话 -------------------------------------------------
CREATE TABLE device_sessions (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id       UUID REFERENCES accounts(id) ON DELETE CASCADE,
  device_code_enc  BYTEA,                        -- device_code 加密存储
  user_code        TEXT,                         -- XXXX-XXXX，可展示给真人
  verification_uri TEXT,
  interval_s       INT,
  expires_at       TIMESTAMPTZ,
  status           TEXT NOT NULL DEFAULT 'pending', -- pending|authorized|expired|denied
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 用量聚合（明细经 Kafka 落数仓；这里只存回写聚合）---------------------------
CREATE TABLE usage_rollup (
  user_id          UUID NOT NULL,
  account_id       UUID,
  day              DATE NOT NULL,
  model            TEXT NOT NULL,
  prompt_tokens    BIGINT NOT NULL DEFAULT 0,
  completion_tokens BIGINT NOT NULL DEFAULT 0,
  requests         BIGINT NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, day, model)
);
