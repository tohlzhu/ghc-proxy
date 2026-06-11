// Thin fetch wrapper around the proxy's /admin JSON API. Injects the operator
// admin token as X-Admin-Token on every request. A 403 means the token is
// missing/invalid — we clear it and signal the caller to bounce to Login.
import { clearToken, getToken } from "./auth";

export class AuthError extends Error {}
export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

const BASE = "/admin";

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init.headers as Record<string, string> | undefined),
  };
  if (token) headers["X-Admin-Token"] = token;

  const resp = await fetch(`${BASE}${path}`, { ...init, headers });
  if (resp.status === 403) {
    clearToken();
    throw new AuthError("admin token required");
  }
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const body = await resp.json();
      if (body?.detail) detail = body.detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(resp.status, detail);
  }
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

const qs = (params: object): string => {
  const entries = Object.entries(params).filter(([, v]) => v) as [string, string][];
  return entries.length ? `?${new URLSearchParams(entries)}` : "";
};

// ---- types --------------------------------------------------------------

export interface UsageWindow {
  from?: string;
  to?: string;
}
export interface TimeseriesRow {
  day: string;
  prompt_tokens: number;
  completion_tokens: number;
  requests: number;
}
export interface ByUserRow {
  user_id: string;
  external_id: string | null;
  display_name: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  requests: number;
}
export interface ByAccountRow {
  account_id: string | null;
  login: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  requests: number;
}
export interface ByModelRow {
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  requests: number;
}
export interface KeyMeta {
  id: string;
  name: string | null;
  scopes: string[];
  status: string;
  rate_limit: number | null;
  created_at: string | null;
  last_used_at: string | null;
}
export interface UserRow {
  id: string;
  external_id: string | null;
  display_name: string | null;
  status: string;
  created_at: string | null;
  keys: KeyMeta[];
}
export interface AccountRow {
  id: string;
  login: string;
  plan: string | null;
  api_base: string | null;
  status: string;
  last_error: string | null;
  last_seen_at: string | null;
  refresh_at: string | null;
  updated_at: string | null;
}
export interface BindingRow {
  user_id: string;
  external_id: string | null;
  account_id: string;
  login: string | null;
  status: string;
  bound_at: string | null;
  last_active_at: string | null;
}
export interface IssuedKey {
  user_id: string;
  api_key_id: string;
  name?: string | null;
  api_key: string; // plaintext — shown once
}
export interface DeviceFlowStart {
  login: string;
  account_id: string;
  session_id: string;
  user_code: string;
  verification_uri: string;
  interval: number;
  expires_in: number;
}

// ---- endpoints ----------------------------------------------------------

export const api = {
  usageTimeseries: (w: UsageWindow) =>
    request<TimeseriesRow[]>(`/usage/timeseries${qs(w)}`),
  usageByUser: (w: UsageWindow) => request<ByUserRow[]>(`/usage/by-user${qs(w)}`),
  usageByAccount: (w: UsageWindow) =>
    request<ByAccountRow[]>(`/usage/by-account${qs(w)}`),
  usageByModel: (w: UsageWindow) => request<ByModelRow[]>(`/usage/by-model${qs(w)}`),

  listUsers: () => request<UserRow[]>("/users"),
  createUser: (external_id: string, display_name?: string) =>
    request<IssuedKey>("/users", {
      method: "POST",
      body: JSON.stringify({ external_id, display_name }),
    }),
  setUserStatus: (userId: string, status: string) =>
    request<unknown>(`/users/${userId}`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    }),
  issueKey: (
    userId: string,
    body: { name?: string; scopes?: string[]; rate_limit?: number },
  ) =>
    request<IssuedKey>(`/users/${userId}/keys`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  rotateKey: (keyId: string) =>
    request<IssuedKey>(`/keys/${keyId}/rotate`, { method: "POST" }),
  revokeKey: (keyId: string) =>
    request<unknown>(`/keys/${keyId}/revoke`, { method: "POST" }),

  listAccounts: () => request<AccountRow[]>("/accounts"),
  setAccountStatus: (accountId: string, status: string) =>
    request<unknown>(`/accounts/${accountId}/status`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    }),
  startLogin: (login: string) =>
    request<DeviceFlowStart>(`/accounts/${login}/login/start`, { method: "POST" }),
  pollLogin: (login: string) =>
    request<unknown>(`/accounts/${login}/login/poll`, { method: "POST" }),

  listBindings: () => request<BindingRow[]>("/bindings"),
  releaseBinding: (userId: string) =>
    request<unknown>(`/bindings/${userId}/release`, { method: "POST" }),
};
