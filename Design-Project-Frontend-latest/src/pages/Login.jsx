import { useEffect, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { api, MOCK_MODE } from "../api";
import { useAuth } from "../context/AuthContext";
import { useSession } from "../context/SessionContext";

// Seeded by the backend's `make reset` (development only).
const SEEDED = [
  ["Admin / officer", "admin@roboadvisor.local"],
  ["Lim Ah Hock", "c001@roboadvisor.local"],
  ["Priya Sharma", "c002@roboadvisor.local"],
  ["Marcus Chen", "c003@roboadvisor.local"],
  ["Siti Nurhaliza", "c004@roboadvisor.local"],
  ["Robert Teo", "c005@roboadvisor.local"],
];

export default function Login() {
  const { user, login, register, error, busy } = useAuth();
  const { sessionDate, chooseDate, range, seedRange } = useSession();
  const navigate = useNavigate();
  const location = useLocation();
  const next = location.state?.from && location.state.from !== "/login"
    ? location.state.from
    : null;
  const [mode, setMode] = useState("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [date, setDate] = useState("");
  const [dateError, setDateError] = useState("");
  const [dateBusy, setDateBusy] = useState(false);
  const [justRegistered, setJustRegistered] = useState(false);

  const needsDate = user?.role === "client" && !sessionDate;

  // Prefill with the date chosen last time, and -- more importantly -- learn the
  // selectable range. This call used to 422 when no date was set, so the catch
  // swallowed it and the input rendered with no min/max at all.
  useEffect(() => {
    if (!needsDate) return;
    api
      .getSessionDate()
      .then((r) => {
        if (r?.sessionDate) setDate(r.sessionDate);
        seedRange(r ?? {});
      })
      .catch(() => {});
  }, [needsDate]);

  if (user && !needsDate) {
    const dest = next || (user.role === "officer" ? "/officer" : "/dashboard");
    return <Navigate to={dest} replace />;
  }

  async function handleLogin(e) {
    e.preventDefault();
    const ok = mode === "register"
      ? await register(email, password, fullName)
      : await login(email, password);
    if (ok) {
      setPassword("");
      setJustRegistered(mode === "register");
    }
  }

  async function handleDate(e) {
    e.preventDefault();
    setDateError("");
    setDateBusy(true);
    try {
      await chooseDate(date);
      navigate(justRegistered ? "/profile" : (next || "/dashboard"));
    } catch (err) {
      setDateError(err.message);
    } finally {
      setDateBusy(false);
    }
  }

  return (
    <div className="login-screen">
      <div className="login-panel">
        <div className="brand-mark">RoboAdvisor</div>
        <p className="tagline">Explainability & human oversight console</p>
        {MOCK_MODE && (
          <div className="callout warn" style={{ marginTop: 16 }}>
            <strong>Mock mode</strong> — no backend is used. Data is in memory and resets on refresh. Session dates: 2026-01-02 to 2026-06-30.
          </div>
        )}

        {!needsDate && (
          <form onSubmit={handleLogin} style={{ marginTop: 32 }}>
            {error && <div className="error-text" role="alert">{error}</div>}
            <div className="tabs" style={{ marginBottom: 16 }}>
              <button type="button" className={`tab-btn ${mode === "signin" ? "active" : ""}`} onClick={() => setMode("signin")}>Sign in</button>
              <button type="button" className={`tab-btn ${mode === "register" ? "active" : ""}`} onClick={() => setMode("register")}>Create account</button>
            </div>
            {mode === "register" && (
              <div className="field">
                <label htmlFor="fullName">Full name</label>
                <input id="fullName" type="text" value={fullName} onChange={(e) => setFullName(e.target.value)} autoComplete="name" />
              </div>
            )}
            <div className="field">
              <label htmlFor="email">Email</label>
              <input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoFocus autoComplete="username" />
            </div>
            <div className="field">
              <label htmlFor="password">Password</label>
              <input id="password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete={mode === "register" ? "new-password" : "current-password"} />
            </div>
            <button className="btn" type="submit" style={{ width: "100%" }} disabled={busy || !email || !password || (mode === "register" && !fullName.trim())}>
              {busy ? (mode === "register" ? "Creating account…" : "Signing in…") : (mode === "register" ? "Create account" : "Sign in")}
            </button>
            {mode === "register" && (
              <p className="hint" style={{ marginTop: 12 }}>A new client record is written to the database with a $50,000 starting cash balance. Complete the profile form next so the stored record is filled in.</p>
            )}
          </form>
        )}

        {needsDate && (
          <form onSubmit={handleDate} style={{ marginTop: 32 }}>
            <p style={{ color: "var(--ink-soft)" }}>
              Welcome, {user.displayName}. Choose the <strong>session date</strong> for this simulation. The system
              loads the prices for that date.
            </p>
            {dateError && <div className="error-text" role="alert">{dateError}</div>}
            <div className="field">
              <label htmlFor="sdate">Session date</label>
              <input id="sdate" type="date" value={date} min={range.from || undefined} max={range.to || undefined} onChange={(e) => setDate(e.target.value)} autoFocus />
            </div>
            <button className="btn" type="submit" style={{ width: "100%" }} disabled={dateBusy || !date}>
              {dateBusy ? "Loading prices…" : "Continue"}
            </button>
          </form>
        )}

        {!user && (
          <div className="demo-creds">
            Seeded development logins (password <code>password123</code>) — remove before any real deployment
            <table>
              <tbody>
                {SEEDED.map(([label, mail]) => (
                  <tr key={mail}>
                    <td>{label}</td>
                    <td>
                      <button type="button" className="linklike" onClick={() => setEmail(mail)}>{mail}</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
