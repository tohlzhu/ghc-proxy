import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { setToken } from "../auth";
import { api } from "../api";
import { ErrorLine } from "../components";

export default function Login({ onLogin }: { onLogin: () => void }) {
  const [token, setTok] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    // Validate by calling a cheap admin endpoint with the token set.
    setToken(token.trim());
    try {
      await api.listAccounts();
      onLogin();
      navigate("/usage");
    } catch {
      setError("Invalid admin token.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-wrap">
      <form className="card login-card" onSubmit={submit}>
        <h2>Admin Console</h2>
        <p className="muted">Enter the operator admin token (GHCPROXY_ADMIN_TOKEN).</p>
        <div className="field">
          <label>Admin token</label>
          <input
            type="password"
            autoFocus
            value={token}
            onChange={(e) => setTok(e.target.value)}
            style={{ width: "100%" }}
          />
        </div>
        <ErrorLine error={error} />
        <button type="submit" disabled={busy || !token.trim()}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
