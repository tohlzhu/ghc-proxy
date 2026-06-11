import { useEffect, useState } from "react";
import { api, BindingRow } from "../api";
import { Badge, ErrorLine, fmtTime } from "../components";

export default function Bindings() {
  const [rows, setRows] = useState<BindingRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = async () => {
    setError(null);
    try {
      setRows(await api.listBindings());
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load bindings");
    }
  };

  useEffect(() => {
    load();
  }, []);

  const release = async (userId: string) => {
    if (!confirm(`Release binding for user ${userId}? The account returns to the idle pool.`))
      return;
    setBusy(userId);
    try {
      await api.releaseBinding(userId);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "release failed");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div>
      <h2>User ↔ Account Bindings</h2>
      <ErrorLine error={error} />
      <div className="card">
        <table>
          <thead>
            <tr>
              <th>User</th>
              <th>Account</th>
              <th>Status</th>
              <th>Bound at</th>
              <th>Last active</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((b) => (
              <tr key={b.user_id}>
                <td>
                  {b.external_id || b.user_id}
                  <div className="muted mono" style={{ fontSize: 11 }}>
                    {b.user_id}
                  </div>
                </td>
                <td>
                  {b.login || b.account_id}
                  <div className="muted mono" style={{ fontSize: 11 }}>
                    {b.account_id}
                  </div>
                </td>
                <td>
                  <Badge value={b.status} />
                </td>
                <td className="muted">{fmtTime(b.bound_at)}</td>
                <td className="muted">{fmtTime(b.last_active_at)}</td>
                <td>
                  <button
                    className="warn"
                    disabled={busy === b.user_id}
                    onClick={() => release(b.user_id)}
                  >
                    Release
                  </button>
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={6} className="muted">
                  No active bindings.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
