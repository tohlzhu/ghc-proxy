import { ReactNode } from "react";

export function Badge({ value }: { value: string | null }) {
  const v = value || "—";
  return <span className={`badge ${value || ""}`}>{v}</span>;
}

export function fmtNum(n: number): string {
  return n.toLocaleString();
}

export function fmtTime(s: string | null): string {
  if (!s) return "—";
  const d = new Date(s);
  if (isNaN(d.getTime())) return s;
  return d.toLocaleString();
}

export function Modal({ children, onClose }: { children: ReactNode; onClose: () => void }) {
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        {children}
      </div>
    </div>
  );
}

// Copy-once dialog: the plaintext key is shown exactly once after create/rotate.
// It cannot be retrieved again — the backend only stores the hash.
export function KeyDialog({ apiKey, onClose }: { apiKey: string; onClose: () => void }) {
  return (
    <Modal onClose={onClose}>
      <h3>API key issued</h3>
      <p className="muted">
        Copy this key now — it is shown only once and cannot be retrieved later.
      </p>
      <div className="keybox mono">{apiKey}</div>
      <div className="row">
        <button onClick={() => navigator.clipboard?.writeText(apiKey)}>Copy</button>
        <button className="secondary" onClick={onClose}>
          Done
        </button>
      </div>
    </Modal>
  );
}

export function ErrorLine({ error }: { error: string | null }) {
  if (!error) return null;
  return <div className="error">{error}</div>;
}
