// Operator admin token, kept in sessionStorage (cleared when the tab closes).
// The console reuses the existing static X-Admin-Token mechanism — there is no
// operator account system.
const KEY = "ghcproxy_admin_token";

export function getToken(): string | null {
  return sessionStorage.getItem(KEY);
}

export function setToken(token: string): void {
  sessionStorage.setItem(KEY, token);
}

export function clearToken(): void {
  sessionStorage.removeItem(KEY);
}

export function isAuthed(): boolean {
  return !!getToken();
}
