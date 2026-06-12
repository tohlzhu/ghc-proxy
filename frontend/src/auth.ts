// Operator admin token, kept in sessionStorage (cleared when the tab closes).
// The console reuses the existing static X-Admin-Token mechanism — there is no
// operator account system.
const KEY = "ghcproxy_admin_token";
export const AUTH_CHANGED = "ghcproxy_auth_changed";

function notifyAuthChanged(): void {
  window.dispatchEvent(new Event(AUTH_CHANGED));
}

export function getToken(): string | null {
  return sessionStorage.getItem(KEY);
}

export function setToken(token: string): void {
  sessionStorage.setItem(KEY, token);
}

export function clearToken(): void {
  sessionStorage.removeItem(KEY);
  notifyAuthChanged();
}

export function isAuthed(): boolean {
  return !!getToken();
}
