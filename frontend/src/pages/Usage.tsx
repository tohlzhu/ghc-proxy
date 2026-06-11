import { useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  api,
  ByAccountRow,
  ByModelRow,
  ByUserRow,
  TimeseriesRow,
} from "../api";
import { ErrorLine, fmtNum } from "../components";

const PIE_COLORS = ["#4f86f7", "#34d399", "#fbbf24", "#f87171", "#a78bfa", "#22d3ee", "#fb923c"];

function defaultFrom(): string {
  const d = new Date();
  d.setDate(d.getDate() - 29);
  return d.toISOString().slice(0, 10);
}
function today(): string {
  return new Date().toISOString().slice(0, 10);
}

export default function Usage() {
  const [from, setFrom] = useState(defaultFrom());
  const [to, setTo] = useState(today());
  const [ts, setTs] = useState<TimeseriesRow[]>([]);
  const [byUser, setByUser] = useState<ByUserRow[]>([]);
  const [byAccount, setByAccount] = useState<ByAccountRow[]>([]);
  const [byModel, setByModel] = useState<ByModelRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setError(null);
    setLoading(true);
    const w = { from, to };
    try {
      const [t, u, a, m] = await Promise.all([
        api.usageTimeseries(w),
        api.usageByUser(w),
        api.usageByAccount(w),
        api.usageByModel(w),
      ]);
      setTs(t);
      setByUser(u);
      setByAccount(a);
      setByModel(m);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load usage");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const modelPie = byModel.map((m) => ({
    name: m.model,
    value: m.prompt_tokens + m.completion_tokens,
  }));

  return (
    <div>
      <h2>Token Usage</h2>
      <div className="toolbar">
        <div className="field" style={{ margin: 0 }}>
          <label>From</label>
          <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
        </div>
        <div className="field" style={{ margin: 0 }}>
          <label>To</label>
          <input type="date" value={to} onChange={(e) => setTo(e.target.value)} />
        </div>
        <button onClick={load} disabled={loading}>
          {loading ? "Loading…" : "Apply"}
        </button>
      </div>
      <ErrorLine error={error} />

      <div className="card">
        <h3>Daily trend — tokens &amp; requests</h3>
        <ResponsiveContainer width="100%" height={280}>
          <LineChart data={ts}>
            <CartesianGrid stroke="#2d3342" strokeDasharray="3 3" />
            <XAxis dataKey="day" stroke="#8b93a7" />
            <YAxis stroke="#8b93a7" />
            <Tooltip contentStyle={{ background: "#1a1d27", border: "1px solid #2d3342" }} />
            <Legend />
            <Line type="monotone" dataKey="prompt_tokens" stroke="#4f86f7" name="Prompt" />
            <Line type="monotone" dataKey="completion_tokens" stroke="#34d399" name="Completion" />
            <Line type="monotone" dataKey="requests" stroke="#fbbf24" name="Requests" />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <div className="grid2">
        <div className="card">
          <h3>By model — token share</h3>
          <ResponsiveContainer width="100%" height={260}>
            <PieChart>
              <Pie data={modelPie} dataKey="value" nameKey="name" outerRadius={90} label>
                {modelPie.map((_, i) => (
                  <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                ))}
              </Pie>
              <Tooltip contentStyle={{ background: "#1a1d27", border: "1px solid #2d3342" }} />
            </PieChart>
          </ResponsiveContainer>
        </div>

        <div className="card">
          <h3>By backend account — traffic</h3>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={byAccount}>
              <CartesianGrid stroke="#2d3342" strokeDasharray="3 3" />
              <XAxis dataKey="login" stroke="#8b93a7" />
              <YAxis stroke="#8b93a7" />
              <Tooltip contentStyle={{ background: "#1a1d27", border: "1px solid #2d3342" }} />
              <Legend />
              <Bar dataKey="prompt_tokens" fill="#4f86f7" name="Prompt" />
              <Bar dataKey="completion_tokens" fill="#34d399" name="Completion" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="card">
        <h3>By front-end user — ranking</h3>
        <table>
          <thead>
            <tr>
              <th>User</th>
              <th>External ID</th>
              <th className="num">Prompt</th>
              <th className="num">Completion</th>
              <th className="num">Requests</th>
            </tr>
          </thead>
          <tbody>
            {byUser.map((u) => (
              <tr key={u.user_id}>
                <td>{u.display_name || u.user_id}</td>
                <td className="muted">{u.external_id || "—"}</td>
                <td className="num">{fmtNum(u.prompt_tokens)}</td>
                <td className="num">{fmtNum(u.completion_tokens)}</td>
                <td className="num">{fmtNum(u.requests)}</td>
              </tr>
            ))}
            {byUser.length === 0 && (
              <tr>
                <td colSpan={5} className="muted">
                  No usage in this window.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="card">
        <h3>By model — detail</h3>
        <table>
          <thead>
            <tr>
              <th>Model</th>
              <th className="num">Prompt</th>
              <th className="num">Completion</th>
              <th className="num">Requests</th>
            </tr>
          </thead>
          <tbody>
            {byModel.map((m) => (
              <tr key={m.model}>
                <td>{m.model}</td>
                <td className="num">{fmtNum(m.prompt_tokens)}</td>
                <td className="num">{fmtNum(m.completion_tokens)}</td>
                <td className="num">{fmtNum(m.requests)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
