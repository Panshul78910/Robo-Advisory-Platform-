import { useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useSession } from "../context/SessionContext";

const cls = ({ isActive }) => (isActive ? "active" : "");

export default function Sidebar() {
  const { user, logout } = useAuth();
  const { sessionDate, chooseDate } = useSession();
  const navigate = useNavigate();
  const [draft, setDraft] = useState("");
  const [msg, setMsg] = useState("");

  function handleLogout() {
    logout();
    navigate("/login");
  }

  // A.5(4): move to a later session date and watch holdings revalue.
  async function changeDate(e) {
    e.preventDefault();
    if (!draft) return;
    try {
      await chooseDate(draft);
      setMsg("");
      setDraft("");
    } catch (err) {
      setMsg(err.message);
    }
  }

  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-mark">RoboAdvisor</span>
        <span className="brand-rule">/</span>
        <span style={{ fontSize: 13, color: "rgba(238,240,234,0.6)" }}>oversight</span>
      </div>

      <nav className="sidebar-nav">
        {user?.role === "client" && (
          <>
            <NavLink to="/dashboard" className={cls}>My dashboard</NavLink>
            <NavLink to="/profile" className={cls}>My profile</NavLink>
            <NavLink to="/profiling" className={cls}>Risk conversation</NavLink>
            <NavLink to="/portfolio" className={cls}>Portfolio & trading</NavLink>
          </>
        )}
        {user?.role === "officer" && <NavLink to="/officer" className={cls}>Officer console</NavLink>}
      </nav>

      {user?.role === "client" && sessionDate && (
        <form className="sidebar-date" onSubmit={changeDate}>
          <div className="hint-light">Session date</div>
          <strong>{sessionDate}</strong>
          <input type="date" value={draft} onChange={(e) => setDraft(e.target.value)} aria-label="New session date" />
          <button type="submit" disabled={!draft}>Change date</button>
          {msg && <div className="hint-light" style={{ color: "#f3b3aa" }}>{msg}</div>}
        </form>
      )}

      {user && (
        <div className="sidebar-footer">
          <strong>{user.displayName}</strong>
          {user.clientCode && <div>{user.clientCode}</div>}
          {user.email && <div>{user.email}</div>}
          role: {user.role}
          <button onClick={handleLogout}>Log out</button>
        </div>
      )}
    </aside>
  );
}
