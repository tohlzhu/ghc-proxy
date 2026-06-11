import { NavLink, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { useState } from "react";
import { clearToken, isAuthed } from "./auth";
import Login from "./pages/Login";
import Bindings from "./pages/Bindings";
import Usage from "./pages/Usage";
import Users from "./pages/Users";
import Accounts from "./pages/Accounts";

function Shell() {
  const navigate = useNavigate();
  const logout = () => {
    clearToken();
    navigate("/login");
  };
  return (
    <div className="layout">
      <aside className="sidebar">
        <h1>GHC Proxy</h1>
        <nav>
          <NavLink to="/usage">Usage</NavLink>
          <NavLink to="/bindings">Bindings</NavLink>
          <NavLink to="/users">Users &amp; Keys</NavLink>
          <NavLink to="/accounts">Accounts</NavLink>
        </nav>
        <div className="logout">
          <button className="secondary" onClick={logout}>
            Log out
          </button>
        </div>
      </aside>
      <main className="main">
        <Routes>
          <Route path="/usage" element={<Usage />} />
          <Route path="/bindings" element={<Bindings />} />
          <Route path="/users" element={<Users />} />
          <Route path="/accounts" element={<Accounts />} />
          <Route path="*" element={<Navigate to="/usage" replace />} />
        </Routes>
      </main>
    </div>
  );
}

export default function App() {
  // re-render on login/logout via a tick bumped by the Login page
  const [, setTick] = useState(0);
  const authed = isAuthed();
  return (
    <Routes>
      <Route
        path="/login"
        element={
          authed ? (
            <Navigate to="/usage" replace />
          ) : (
            <Login onLogin={() => setTick((t) => t + 1)} />
          )
        }
      />
      <Route path="/*" element={authed ? <Shell /> : <Navigate to="/login" replace />} />
    </Routes>
  );
}
