# GHC Proxy Admin Console — Design

Date: 2026-06-11
Status: approved (decisions below confirmed by operator)

## Goal

Build an operator-facing Web Admin Console for the GHC Proxy service, per `todo.md`.
The current service is a pure backend API with only a handful of admin endpoints
(`GET/POST /accounts`, `POST /users`, device-flow start/poll) behind a static
`X-Admin-Token`. This adds the missing JSON endpoints and a standalone SPA so
operators can visualize bindings, token usage, manage users/keys, and manage
backend GHC accounts — all without touching the database directly.

Out of scope (per `todo.md`): prompt-log read/search (only in Kafka, not stored),
operator account system / SSO (static admin token kept), Kafka consumption / analytics.

## Confirmed decisions

- **SPA stack**: React 18 + Vite + TypeScript.
- **Charts**: Recharts.
- **Console hosting**: standalone Nginx container (front/back separated, independent build/deploy).
- **Azure test**: full-stack docker-compose deploy in `rg-dev2` (incl. console), exercise
  console→backend through the admin token with test-account data (no real GHC Device Flow auth).

## Architecture

Front/back separated:

```
Operator browser
   │  (X-Admin-Token in sessionStorage)
   ▼
[console] nginx container  ──/admin/* reverse-proxy──▶  [proxy] FastAPI  ──▶ PostgreSQL
   serves SPA (history fallback)                          /admin JSON API
```

The SPA is a static bundle served by nginx. nginx reverse-proxies `/admin/*` to the
proxy service so the browser talks same-origin (no CORS needed). In dev, Vite proxies
`/admin` to `localhost:8080`.

## Backend — new `/admin` JSON endpoints

All new endpoints continue to use the existing `require_admin` dependency (static
`X-Admin-Token`). No schema changes — all required tables already exist
(`usage_rollup`, `bindings`, `users`, `api_keys`, `accounts`).

### Usage (read-only over `usage_rollup`)

Query params on all four: `from` (date, inclusive), `to` (date, inclusive), both optional;
default window = last 30 days ending today. Aggregates `prompt_tokens`,
`completion_tokens`, `requests`.

- `GET /admin/usage/timeseries` → `[{day, prompt_tokens, completion_tokens, requests}]`, ascending by day.
- `GET /admin/usage/by-user` → `[{user_id, external_id, display_name, prompt_tokens, completion_tokens, requests}]`, descending by total tokens.
- `GET /admin/usage/by-account` → `[{account_id, login, prompt_tokens, completion_tokens, requests}]`, descending by total tokens. `account_id` may be null (usage recorded without an account) → labeled accordingly.
- `GET /admin/usage/by-model` → `[{model, prompt_tokens, completion_tokens, requests}]`, descending by total tokens.

### Users & keys

- `GET /admin/users` → `[{id, external_id, display_name, status, created_at, keys: [{id, name, scopes, status, rate_limit, created_at, last_used_at}]}]`. **Metadata only — never plaintext or hash.**
- `POST /admin/users` *(exists)* — create user + default key; plaintext key returned once.
- `PATCH /admin/users/{user_id}` `{status: active|disabled}` → enable/disable user.
- `POST /admin/users/{user_id}/keys` `{name, scopes?, rate_limit?}` → issue key; plaintext returned once.
- `POST /admin/keys/{key_id}/rotate` → revoke the key and issue a replacement carrying the same name/scopes/rate_limit; plaintext returned once.
- `POST /admin/keys/{key_id}/revoke` → set key `status=revoked`.

### Accounts & bindings

- `GET /admin/accounts` → **extended** to include `id, login, plan, api_base, status, last_error, last_seen_at, refresh_at, updated_at` (currently a subset).
- `PATCH /admin/accounts/{account_id}/status` `{status}` → operator status change. Allowed targets: `disabled`, `idle` (e.g. clear quarantine). Rejects invalid targets with 400. Does not force-unbind.
- `GET /admin/bindings` → `[{user_id, external_id, account_id, login, status, bound_at, last_active_at}]`, joining `bindings`+`users`+`accounts`.
- `POST /admin/bindings/{user_id}/release` → manual unbind (reuses `release_binding`; bound account returns to idle).
- account import + device-flow start/poll *(exist, reused as-is)*.

### New `PgRepo` methods

Read: `usage_timeseries`, `usage_by_user`, `usage_by_account`, `usage_by_model`,
`list_users_with_keys`, `list_bindings`. Extend `list_accounts` columns.
Mutations: `set_user_status`, `revoke_api_key`, `get_api_key_meta` (for rotate:
fetch user_id/name/scopes/rate_limit), and reuse `add_api_key` / `set_account_status` /
`release_binding`. All new methods get in-memory equivalents in `tests/fakes.py:FakeRepo`.

## Frontend — `frontend/` (new top-level dir)

React 18 + Vite + TS, Recharts, React Router. Structure:

```
frontend/
├── index.html
├── package.json / tsconfig.json / vite.config.ts
├── nginx.conf            # SPA history fallback + /admin reverse proxy
├── Dockerfile            # node build → nginx serve
└── src/
    ├── main.tsx, App.tsx (router + auth guard)
    ├── api.ts            # fetch wrapper, injects X-Admin-Token, 403 → logout
    ├── auth.ts           # token in sessionStorage
    └── pages/
        ├── Login.tsx
        ├── Bindings.tsx
        ├── Usage.tsx     # 4 Recharts views + tables
        ├── Users.tsx     # users + keys lifecycle, copy-once key dialog
        └── Accounts.tsx  # status mgmt + device-flow modal (user_code/verification_uri)
```

Auth: operator enters `GHCPROXY_ADMIN_TOKEN` on Login; stored in `sessionStorage`;
every request carries `X-Admin-Token`. A 403 clears it and returns to Login.
Plaintext keys appear only in a copy-once dialog from create/rotate responses.

## Deploy

- `frontend/Dockerfile` — multi-stage: `node:20` build → `nginx:alpine` serve `dist/`.
- `frontend/nginx.conf` — `try_files … /index.html` history fallback; `location /admin/ { proxy_pass http://proxy:8080; }`.
- `deploy/docker/docker-compose.yaml` — add `console` service (build `../../frontend`, port `8081:80`, depends on proxy).
- `deploy/k8s/40-console.yaml` — Deployment + Service for the console image.
- `.gitignore` — add `frontend/node_modules/`, `frontend/dist/`.

## Tests & verification

- TDD: new `tests/test_admin_console.py` (FastAPI `TestClient` + extended `FakeRepo`), one
  test per new endpoint: auth-required (403 without token), happy path, **key never leaked**
  (responses contain no plaintext/hash except the one-time create/rotate field), status-change
  validation, usage aggregation correctness, manual unbind returns account to idle.
- New repo query methods get focused tests against `FakeRepo` behavior via the API layer.
- Existing 73 tests stay green.
- `cd frontend && npm install && npm run build` must succeed.
- Azure: bring up docker-compose full stack (incl. console) in `rg-dev2`; seed test users/accounts/usage;
  confirm the console reaches each endpoint through the admin token.

## Docs

- `README.md` + `ghc-proxy-design.md`: add an Admin Console section (endpoints, SPA, auth).
- `deploy/k8s/README.md` + compose comments: console build & hosting steps.

## Key invariant

API responses expose key **metadata only**. Plaintext key material appears solely in the
one-time response body of create / new-key / rotate. The hash is never returned anywhere.
