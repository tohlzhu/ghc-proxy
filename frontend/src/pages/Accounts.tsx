import { useEffect, useState } from "react";
import { api, AccountRow, DeviceFlowStart } from "../api";
import { Badge, ErrorLine, Modal, fmtTime } from "../components";
import { buildReloginEmail } from "../email";

export default function Accounts() {
  const [rows, setRows] = useState<AccountRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [device, setDevice] = useState<DeviceFlowStart | null>(null);
  const [pollMsg, setPollMsg] = useState<string | null>(null);
  const [copyMsg, setCopyMsg] = useState<string | null>(null);

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
    setCopyMsg(null);
    try {
      setDevice(await api.startLogin(login));
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not start device flow");
    }
  };

  const copyEmail = async (text: string) => {
    try {
      await navigator.clipboard?.writeText(text);
      setCopyMsg("Email copied to clipboard.");
    } catch {
      setCopyMsg("Copy failed — select the text above and copy manually.");
    }
  };

  const poll = async (login: string) => {
    setPollMsg("Polling…");
    try {
      const res = await api.pollLogin(login);
      if (res.status === "pending") {
        const hint = res.interval ? ` Try again in about ${res.interval}s.` : "";
        const reason = res.reason ? ` (${res.reason})` : "";
        setPollMsg(`Still pending${reason}.${hint}`);
        return;
      }
      setPollMsg("Authorized - account is back online.");
      await load();
    } catch (e) {
      setPollMsg(
        e instanceof Error ? `Polling failed: ${e.message}` : "polling failed",
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

      {device && (() => {
        const email = buildReloginEmail(device);
        return (
          <Modal onClose={() => setDevice(null)}>
            <h3>Device Flow login — {device.login}</h3>
            <p className="muted">
              Device Flow started. Copy the email below and send it to the user so they can
              complete the GitHub authorization in their browser. Then click Poll until the
              account comes back online.
            </p>

            <div className="field">
              <label>Email to send to the user</label>
              <div className="keybox mono email-preview">
                <div style={{ fontWeight: 600 }}>Subject: {email.subject}</div>
                <pre style={{ margin: "8px 0 0", whiteSpace: "pre-wrap", fontFamily: "inherit" }}>
                  {email.body}
                </pre>
              </div>
            </div>

            <div className="row">
              <button onClick={() => copyEmail(email.asPlainText())}>Copy email</button>
              <button onClick={() => poll(device.login)}>Poll</button>
              <button className="secondary" onClick={() => setDevice(null)}>
                Close
              </button>
            </div>
            {copyMsg && <p className="muted">{copyMsg}</p>}

            <div className="field" style={{ marginTop: 16 }}>
              <label>Quick reference (also embedded in the email)</label>
              <div className="keybox mono">
                <a href={device.verification_uri} target="_blank" rel="noreferrer">
                  {device.verification_uri}
                </a>
                {"  —  code "}
                <span style={{ letterSpacing: 2 }}>{device.user_code}</span>
              </div>
            </div>

            {pollMsg && <p className="muted">{pollMsg}</p>}
          </Modal>
        );
      })()}
    </div>
  );
}
