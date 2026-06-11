import { useEffect, useState } from "react";
import { api, AccountRow, DeviceFlowStart } from "../api";
import { Badge, ErrorLine, Modal, fmtTime } from "../components";

export default function Accounts() {
  const [rows, setRows] = useState<AccountRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [device, setDevice] = useState<DeviceFlowStart | null>(null);
  const [pollMsg, setPollMsg] = useState<string | null>(null);

  const load = async () => {
    setError(null);
    try {
      setRows(await api.listAccounts());
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load accounts");
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

  const setStatus = (id: string, status: string) =>
    guard(() => api.setAccountStatus(id, status).then(() => undefined));

  const startLogin = async (login: string) => {
    setError(null);
    setPollMsg(null);
    try {
      setDevice(await api.startLogin(login));
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not start device flow");
    }
  };

  const poll = async (login: string) => {
    setPollMsg("Polling…");
    try {
      await api.pollLogin(login);
      setPollMsg("Authorized — account is back online.");
      await load();
    } catch (e) {
      // 202 (pending) is surfaced by the wrapper as a non-ok ApiError too;
      // give the operator a clear hint to keep waiting.
      setPollMsg(
        e instanceof Error ? `Still pending or failed: ${e.message}` : "still pending",
      );
    }
  };

  return (
    <div>
      <h2>Backend GHC Accounts</h2>
      <ErrorLine error={error} />
      <div className="card">
        <table>
          <thead>
            <tr>
              <th>Login</th>
              <th>Plan</th>
              <th>API base</th>
              <th>Status</th>
              <th>Last error</th>
              <th>Last seen</th>
              <th>Next refresh</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((a) => (
              <tr key={a.id}>
                <td>{a.login}</td>
                <td className="muted">{a.plan || "—"}</td>
                <td className="muted mono" style={{ fontSize: 11 }}>
                  {a.api_base || "—"}
                </td>
                <td>
                  <Badge value={a.status} />
                </td>
                <td className="muted">{a.last_error || "—"}</td>
                <td className="muted">{fmtTime(a.last_seen_at)}</td>
                <td className="muted">{fmtTime(a.refresh_at)}</td>
                <td>
                  <div className="row">
                    {a.status === "quarantined" && (
                      <button onClick={() => setStatus(a.id, "idle")}>Un-quarantine</button>
                    )}
                    {a.status !== "disabled" ? (
                      <button className="warn" onClick={() => setStatus(a.id, "disabled")}>
                        Disable
                      </button>
                    ) : (
                      <button onClick={() => setStatus(a.id, "idle")}>Enable</button>
                    )}
                    <button className="secondary" onClick={() => startLogin(a.login)}>
                      Re-login
                    </button>
                  </div>
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={8} className="muted">
                  No accounts. Import one via the admin API.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {device && (
        <Modal onClose={() => setDevice(null)}>
          <h3>Device Flow login — {device.login}</h3>
          <p className="muted">
            Open the verification URL in a browser, sign in to the GHC account, and enter the
            code. Then click Poll until the account comes back online.
          </p>
          <div className="field">
            <label>Verification URL</label>
            <div className="keybox mono">
              <a href={device.verification_uri} target="_blank" rel="noreferrer">
                {device.verification_uri}
              </a>
            </div>
          </div>
          <div className="field">
            <label>User code</label>
            <div className="keybox mono" style={{ fontSize: 20, letterSpacing: 2 }}>
              {device.user_code}
            </div>
          </div>
          {pollMsg && <p className="muted">{pollMsg}</p>}
          <div className="row">
            <button onClick={() => poll(device.login)}>Poll</button>
            <button
              className="secondary"
              onClick={() => navigator.clipboard?.writeText(device.user_code)}
            >
              Copy code
            </button>
            <button className="secondary" onClick={() => setDevice(null)}>
              Close
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
