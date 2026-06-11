-- GHC Proxy — PostgreSQL schema. Placeholders only; no real secrets/tokens.
-- See ghc-proxy-design.md §5.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";  -- gen_random_uuid()

-- Backend GHC account pool ---------------------------------------------------
CREATE TABLE IF NOT EXISTS accounts (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  login           TEXT NOT NULL,
  host            TEXT NOT NULL DEFAULT 'https://github.com',
  oauth_token_enc BYTEA,                          -- gho_ token, AEAD-encrypted
  plan            TEXT,                           -- individual|business|enterprise
  api_base        TEXT NOT NULL DEFAULT 'https://api.enterprise.githubcopilot.com',
  status          TEXT NOT NULL DEFAULT 'logging_in',
                  -- logging_in|idle|bound|quarantined|disabled
  refresh_at      TIMESTAMPTZ,                    -- next proactive liveness check
  last_error      TEXT,
  last_seen_at    TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (host, login)
);
CREATE INDEX IF NOT EXISTS idx_accounts_idle    ON accounts (status) WHERE status = 'idle';
CREATE INDEX IF NOT EXISTS idx_accounts_refresh ON accounts (refresh_at)
       WHERE status IN ('idle','bound');

-- Front-end users ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  external_id  TEXT UNIQUE,
  display_name TEXT,
  status       TEXT NOT NULL DEFAULT 'active',
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Front-end API keys (hash only) --------------------------------------------
CREATE TABLE IF NOT EXISTS api_keys (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  key_hash     BYTEA NOT NULL UNIQUE,             -- sha256(key)
  name         TEXT,
  scopes       TEXT[] NOT NULL DEFAULT '{}',
  rate_limit   INT,                               -- requests/min (NULL=default)
  status       TEXT NOT NULL DEFAULT 'active',
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys (user_id);

-- Strict 1:1 sticky binding (double UNIQUE) ---------------------------------
CREATE TABLE IF NOT EXISTS bindings (
  user_id        UUID NOT NULL UNIQUE REFERENCES users(id)    ON DELETE CASCADE,
  account_id     UUID NOT NULL UNIQUE REFERENCES accounts(id) ON DELETE CASCADE,
  bound_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_active_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  status         TEXT NOT NULL DEFAULT 'active',  -- active|released
  PRIMARY KEY (user_id)
);

-- In-progress device-flow sessions ------------------------------------------
CREATE TABLE IF NOT EXISTS device_sessions (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id       UUID REFERENCES accounts(id) ON DELETE CASCADE,
  device_code_enc  BYTEA,
  user_code        TEXT,
  verification_uri TEXT,
  interval_s       INT,
  expires_at       TIMESTAMPTZ,
  status           TEXT NOT NULL DEFAULT 'pending', -- pending|authorized|expired|denied
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Usage rollup (detail goes to Kafka) ---------------------------------------
CREATE TABLE IF NOT EXISTS usage_rollup (
  user_id           UUID NOT NULL,
  account_id        UUID,
  day               DATE NOT NULL,
  model             TEXT NOT NULL,
  prompt_tokens     BIGINT NOT NULL DEFAULT 0,
  completion_tokens BIGINT NOT NULL DEFAULT 0,
  requests          BIGINT NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, day, model)
);
