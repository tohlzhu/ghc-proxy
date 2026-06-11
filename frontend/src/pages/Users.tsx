import { FormEvent, useEffect, useState } from "react";
import { api, UserRow } from "../api";
import { Badge, ErrorLine, KeyDialog, fmtTime } from "../components";

export default function Users() {
  const [users, setUsers] = useState<UserRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [issuedKey, setIssuedKey] = useState<string | null>(null);

  // create-user form
  const [newExternalId, setNewExternalId] = useState("");
  const [newDisplayName, setNewDisplayName] = useState("");

  // issue-key form (per user)
  const [keyForUser, setKeyForUser] = useState<string | null>(null);
  const [keyName, setKeyName] = useState("");

  const load = async () => {
    setError(null);
    try {
      setUsers(await api.listUsers());
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load users");
    }
  };

  useEffect(() => {
    load();
  }, []);

  const guard = async (fn: () => Promise<void>) => {
    try {
      await fn();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "action failed");
    }
  };

  const createUser = async (e: FormEvent) => {
    e.preventDefault();
    await guard(async () => {
      const res = await api.createUser(newExternalId.trim(), newDisplayName.trim() || undefined);
      setIssuedKey(res.api_key);
      setNewExternalId("");
      setNewDisplayName("");
    });
  };

  const issueKey = async (userId: string) => {
    await guard(async () => {
      const res = await api.issueKey(userId, { name: keyName.trim() || undefined });
      setIssuedKey(res.api_key);
      setKeyForUser(null);
      setKeyName("");
    });
  };

  const rotate = async (keyId: string) =>
    guard(async () => {
      const res = await api.rotateKey(keyId);
      setIssuedKey(res.api_key);
    });

  const revoke = async (keyId: string) => {
    if (!confirm("Revoke this key? It will stop working immediately.")) return;
    await guard(() => api.revokeKey(keyId).then(() => undefined));
  };

  const toggleUser = async (userId: string, status: string) =>
    guard(() => api.setUserStatus(userId, status).then(() => undefined));

  return (
    <div>
      <h2>Front-end Users &amp; Keys</h2>
      <ErrorLine error={error} />

      <div className="card">
        <h3>Create user (issues a default key)</h3>
        <form className="row" onSubmit={createUser}>
          <div className="field" style={{ margin: 0 }}>
            <label>External ID</label>
            <input value={newExternalId} onChange={(e) => setNewExternalId(e.target.value)} required />
          </div>
          <div className="field" style={{ margin: 0 }}>
            <label>Display name (optional)</label>
            <input value={newDisplayName} onChange={(e) => setNewDisplayName(e.target.value)} />
          </div>
          <button type="submit" disabled={!newExternalId.trim()}>
            Create
          </button>
        </form>
      </div>

      {users.map((u) => (
        <div className="card" key={u.id}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <div>
              <h3 style={{ marginBottom: 4 }}>
                {u.display_name || u.external_id || u.id} <Badge value={u.status} />
              </h3>
              <div className="muted mono" style={{ fontSize: 11 }}>
                {u.id} · {u.external_id || "—"}
              </div>
            </div>
            <div className="row">
              {u.status === "active" ? (
                <button className="warn" onClick={() => toggleUser(u.id, "disabled")}>
                  Disable
                </button>
              ) : (
                <button onClick={() => toggleUser(u.id, "active")}>Enable</button>
              )}
              <button className="secondary" onClick={() => setKeyForUser(u.id)}>
                + New key
              </button>
            </div>
          </div>

          <table style={{ marginTop: 12 }}>
            <thead>
              <tr>
                <th>Key name</th>
                <th>Scopes</th>
                <th>Status</th>
                <th>Rate limit</th>
                <th>Created</th>
                <th>Last used</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {u.keys.map((k) => (
                <tr key={k.id}>
                  <td>{k.name || "—"}</td>
                  <td className="muted">{k.scopes.length ? k.scopes.join(", ") : "—"}</td>
                  <td>
                    <Badge value={k.status} />
                  </td>
                  <td className="muted">{k.rate_limit ?? "default"}</td>
                  <td className="muted">{fmtTime(k.created_at)}</td>
                  <td className="muted">{fmtTime(k.last_used_at)}</td>
                  <td>
                    <div className="row">
                      <button
                        className="secondary"
                        disabled={k.status !== "active"}
                        onClick={() => rotate(k.id)}
                      >
                        Rotate
                      </button>
                      <button
                        className="danger"
                        disabled={k.status !== "active"}
                        onClick={() => revoke(k.id)}
                      >
                        Revoke
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              {u.keys.length === 0 && (
                <tr>
                  <td colSpan={7} className="muted">
                    No keys.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      ))}

      {keyForUser && (
        <div className="modal-backdrop" onClick={() => setKeyForUser(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>Issue new key</h3>
            <div className="field">
              <label>Key name (optional)</label>
              <input
                value={keyName}
                autoFocus
                onChange={(e) => setKeyName(e.target.value)}
                style={{ width: "100%" }}
              />
            </div>
            <div className="row">
              <button onClick={() => issueKey(keyForUser)}>Issue</button>
              <button className="secondary" onClick={() => setKeyForUser(null)}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {issuedKey && <KeyDialog apiKey={issuedKey} onClose={() => setIssuedKey(null)} />}
    </div>
  );
}
