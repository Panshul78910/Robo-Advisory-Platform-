import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";
import { api } from "../api";
import { useAuth } from "../context/AuthContext";
import { useSession } from "../context/SessionContext";
import { useLoad } from "../hooks";
import { human, money, niceTicks, num, plainPreview, portfolioName } from "../lib/format";
import CkaCheck from "../components/CkaCheck";
import ShowMore from "../components/ShowMore";
import { DisclosureBox } from "../components/DisclosureAndSources";
import { ErrorNote, Loading } from "../components/Notice";

const DRIVER_OPTIONS = {
  investmentObjective: ["preservation", "income", "balanced", "growth", "aggressive_growth"],
  lossTolerance: ["zero", "low", "medium", "high"],
  liquidityNeeds: ["low", "medium", "high"],
  incomeStability: ["stable", "variable", "unstable"],
  investmentExperience: ["none", "limited", "moderate", "extensive"],
};

const DRIVER_FIELDS = [
  { key: "investmentObjective", label: "Investment objective", type: "select" },
  { key: "investmentHorizonYrs", label: "Investment horizon (years)", type: "number", min: 0, max: 60 },
  { key: "lossTolerance", label: "Loss tolerance", type: "select" },
  { key: "liquidityNeeds", label: "Liquidity needs", type: "select" },
  { key: "incomeStability", label: "Income stability", type: "select" },
  { key: "investmentExperience", label: "Investment experience", type: "select" },
];

export default function ClientDashboard() {
  const { user } = useAuth();
  const { sessionDate } = useSession();

  const holdings = useLoad(() => api.getHoldings(), [sessionDate]);
  const perf = useLoad(() => api.getPerformance(90), [sessionDate]);
  const elig = useLoad(() => api.getEligibility(), []);
  const profile = useLoad(() => api.getProfile(), []);
  const convos = useLoad(() => api.listConversations(), []);

  const [drivers, setDrivers] = useState({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(null);
  const [saveOk, setSaveOk] = useState("");

  useEffect(() => {
    if (!profile.data) return;
    const next = {};
    DRIVER_FIELDS.forEach(({ key }) => (next[key] = profile.data[key] ?? ""));
    setDrivers(next);
  }, [profile.data]);

  const h = holdings.data;
  const e = elig.data;
  const invested = h ? h.totalValue - h.cash : 0;
  const perfTicks = perf.data?.length ? niceTicks(perf.data.map((p) => p.value)) : undefined;

  async function applyDrivers(ev) {
    ev.preventDefault();
    setSaving(true);
    setSaveError(null);
    setSaveOk("");
    const body = {};
    DRIVER_FIELDS.forEach(({ key, type }) => {
      const v = drivers[key];
      if (v === "" || v === null || v === undefined) return;
      body[key] = type === "number" ? Number(v) : v;
    });
    try {
      await api.updateProfile(body);
      await api.reassess();
      await Promise.all([elig.reload(), profile.reload()]);
      setSaveOk("Recommendation updated from the options you selected.");
    } catch (err) {
      setSaveError(err);
    } finally {
      setSaving(false);
    }
  }

  function refreshElig() {
    elig.reload();
    profile.reload();
  }

  return (
    <div className="main">
      <div className="page-header">
        <h1>Welcome back, {user.displayName}</h1>
        <p>
          Signed in as <strong>{user.displayName}</strong>
          {user.clientCode ? <> ({user.clientCode})</> : null}
          {" "}· session date {sessionDate}
        </p>
      </div>

      <section className="RoboAdvisor">
        <div className="RoboAdvisor-head"><h2>Holdings & performance</h2></div>
        {holdings.loading && <div className="RoboAdvisor-body"><Loading what="Loading holdings" /></div>}
        {holdings.error && <div className="RoboAdvisor-body"><ErrorNote error={holdings.error} onRetry={holdings.reload} /></div>}
        {h && (
          <>
            <div className="stat-row">
              <div className="stat"><div className="stat-label">Cash</div><div className="stat-value">{money(h.cash)}</div></div>
              <div className="stat"><div className="stat-label">Invested</div><div className="stat-value">{money(invested)}</div></div>
              <div className="stat"><div className="stat-label">Total value</div><div className="stat-value">{money(h.totalValue)}</div></div>
            </div>
            <div className="RoboAdvisor-body">
              {h.maxAbsDriftPct > 0.5 && (
                <div className="callout warn">
                  Actual weights have drifted from target (largest gap {num(h.maxAbsDriftPct, 2)} percentage points) at {sessionDate} prices. Rebalance from Portfolio &amp; trading if you want to restore the mix.
                </div>
              )}
              {!h.portfolioCode && (
                <div className="callout">
                  You haven't invested yet. Complete your <Link to="/profiling">risk conversation</Link>, then invest from{" "}
                  <Link to="/portfolio">Portfolio & trading</Link>.
                </div>
              )}
              <div className="RoboAdvisor-columns">
                <div>
                  <h3 style={{ marginBottom: 12 }}>
                    Current holdings{h.portfolioName ? ` — ${h.portfolioCode} ${h.portfolioName}` : ""}
                  </h3>
                  <div className="tbl-scroll">
                  <table className="RoboAdvisor-table">
                    <thead>
                      <tr>
                        <th>Asset class</th><th className="num">Units</th><th className="num">Price</th>
                        <th className="num">Value</th><th className="num">Target %</th><th className="num">Actual %</th>
                      </tr>
                    </thead>
                    <tbody>
                      {h.byAssetClass.map((r) => (
                        <tr key={r.asset}>
                          <td>{human(r.asset)}</td>
                          <td className="num">{num(r.units, 4)}</td>
                          <td className="num">{num(r.price, 4)}</td>
                          <td className="num">{money(r.value)}</td>
                          <td className="num">{num(r.targetWeight, 2)}</td>
                          <td className="num">{num(r.actualWeight, 2)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  </div>
                </div>
                <div>
                  <h3 style={{ marginBottom: 12 }}>Performance (90 days to {sessionDate})</h3>
                  {perf.error && <ErrorNote error={perf.error} onRetry={perf.reload} />}
                  {perf.data && (
                    <ResponsiveContainer width="100%" height={220}>
                      <AreaChart data={perf.data}>
                        <defs>
                          <linearGradient id="perf-fill" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="0%" stopColor="var(--status-good)" stopOpacity={0.18} />
                            <stop offset="100%" stopColor="var(--status-good)" stopOpacity={0} />
                          </linearGradient>
                        </defs>
                        <CartesianGrid stroke="var(--rule)" vertical={false} />
                        <XAxis dataKey="date" tick={{ fontSize: 11, fill: "var(--ink-soft)" }} interval={Math.max(0, Math.floor(perf.data.length / 6))} />
                        <YAxis
                          tick={{ fontSize: 11, fill: "var(--ink-soft)" }} width={64}
                          ticks={perfTicks} domain={perfTicks ? [perfTicks[0], perfTicks[perfTicks.length - 1]] : ["auto", "auto"]}
                          tickFormatter={(v) => `$${v.toLocaleString()}`}
                        />
                        <Tooltip formatter={(v) => money(v)} contentStyle={{ fontFamily: "var(--font-mono)", fontSize: 12 }} />
                        <Area type="monotone" dataKey="value" stroke="var(--status-good)" strokeWidth={2} fill="url(#perf-fill)" dot={false} />
                      </AreaChart>
                    </ResponsiveContainer>
                  )}
                </div>
              </div>
            </div>
          </>
        )}
      </section>

      <section className="RoboAdvisor">
        <div className="RoboAdvisor-head"><h2>My conversations</h2></div>
        <div className="RoboAdvisor-body">
          <p className="hint">Only you can see these conversations.</p>
          {convos.loading && <Loading what="Loading conversations" />}
          <ErrorNote error={convos.error} onRetry={convos.reload} />
          {convos.data?.length === 0 && <div className="empty-state">No conversations yet.</div>}
          <ShowMore items={convos.data} initial={5}>
            {(c) => (
              <div key={c.id} className="source-item" style={{ marginBottom: 8 }}>
                <strong>{human(c.kind)}</strong> · {c.messageCount} messages
                {c.lastMessage && <div className="convo-preview">{plainPreview(c.lastMessage)}</div>}
              </div>
            )}
          </ShowMore>
        </div>
      </section>

      <section className="RoboAdvisor">
        <div className="RoboAdvisor-head"><h2>Your risk profile & recommendation</h2></div>
        <div className="RoboAdvisor-body">
          {elig.loading && <Loading what="Loading your risk profile" />}
          {elig.error && <ErrorNote error={elig.error} onRetry={elig.reload} />}
          {e && !e.riskCategory && (
            <div className="callout">
              You don't have a risk category yet. Start the <Link to="/profiling">risk conversation</Link> — it uses the
              details already in your <Link to="/profile">profile</Link>.
            </div>
          )}
          {e?.riskCategory && (
            <>
              <div className="stat-row" style={{ borderBottom: "none", marginBottom: 20 }}>
                <div className="stat"><div className="stat-label">Risk category</div><div className="stat-value" style={{ fontSize: 18 }}>{human(e.riskCategory)}</div></div>
                <div className="stat">
                  <div className="stat-label">Highest portfolio available</div>
                  <div className="stat-value" style={{ fontSize: 18 }}>{e.maxPortfolio} · {portfolioName(e.maxPortfolio)}</div>
                </div>
                <div className="stat">
                  <div className="stat-label">CKA-style check</div>
                  <div className="stat-value" style={{ fontSize: 18 }}>{e.ckaPassed ? "Passed" : "Not passed"}</div>
                </div>
              </div>
              <p style={{ color: "var(--ink-soft)", fontSize: 13.5 }}>
                Eligible portfolios: {e.eligiblePortfolios.map((c) => `${c} ${portfolioName(c)}`).join(" · ") || "none"} ·
                Satellite exposure cap {num(e.satelliteCapPct, 0)}%
              </p>
              {e.firedRules.length > 0 && (
                <div className="callout">
                  <strong>Policy rules limiting your options</strong>
                  {e.firedRules.map((r) => <div key={r.code} style={{ marginTop: 4 }}>{r.explanation}</div>)}
                </div>
              )}
            </>
          )}

          <h3 style={{ margin: "20px 0 8px" }}>Options that change this recommendation</h3>
          <p style={{ color: "var(--ink-soft)", fontSize: 13.5 }}>
            Changing these profile drivers re-runs suitability for <strong>this</strong> login. Loss tolerance, horizon and liquidity can move the category and the highest portfolio. Passing the CKA-style check raises the non-CKA cap and unlocks satellite ETFs.
          </p>
          <form onSubmit={applyDrivers}>
            <div className="form-grid">
              {DRIVER_FIELDS.map((f) => (
                <div className="field" key={f.key}>
                  <label htmlFor={`dash-${f.key}`}>{f.label}</label>
                  {f.type === "select" ? (
                    <select id={`dash-${f.key}`} value={drivers[f.key] ?? ""} onChange={(ev) => setDrivers((d) => ({ ...d, [f.key]: ev.target.value }))}>
                      <option value="">Select…</option>
                      {DRIVER_OPTIONS[f.key].map((o) => <option key={o} value={o}>{human(o)}</option>)}
                    </select>
                  ) : (
                    <input
                      id={`dash-${f.key}`} type="number" min={f.min} max={f.max} value={drivers[f.key] ?? ""}
                      onChange={(ev) => setDrivers((d) => ({ ...d, [f.key]: ev.target.value }))}
                    />
                  )}
                </div>
              ))}
            </div>
            <ErrorNote error={saveError} />
            {saveOk && <div className="callout good">{saveOk}</div>}
            {e?.inconsistencies?.length > 0 && (
              <div className="callout warn">
                <strong>These answers conflict, so a category cannot be assigned until they are cleared</strong>
                {e.inconsistencies.map((i) => <div key={i.code} style={{ marginTop: 4 }}>{i.question}</div>)}
              </div>
            )}
            <button className="btn" type="submit" disabled={saving}>{saving ? "Updating…" : "Apply and update recommendation"}</button>
            <span className="hint" style={{ marginLeft: 12 }}>Or continue in the <Link to="/profiling">risk conversation</Link>.</span>
          </form>

          {e && !e.ckaPassed && (
            <div style={{ marginTop: 24 }}>
              <div className="callout warn">
                CKA-style check not passed. Satellite ETFs are blocked and portfolios above the policy non-CKA cap are closed.
              </div>
              <CkaCheck onPassed={refreshElig} />
            </div>
          )}
          {e?.ckaPassed && <div className="callout good" style={{ marginTop: 16 }}>CKA-style check passed for this login.</div>}

          <div style={{ marginTop: 20 }}><DisclosureBox /></div>
        </div>
      </section>
    </div>
  );
}
