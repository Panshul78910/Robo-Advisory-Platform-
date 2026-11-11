import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useSession } from "../context/SessionContext";
import { useAppData } from "../context/AppDataContext";
import { useLoad } from "../hooks";
import { PORTFOLIOS, fmtTime, human, money, num, portfolioName } from "../lib/format";
import AnswerCard from "../components/AnswerCard";
import CkaCheck from "../components/CkaCheck";
import { StatusPill } from "../components/ConfidenceBadge";
import ShowMore from "../components/ShowMore";
import { DisclosureBox } from "../components/DisclosureAndSources";
import { ErrorNote, Loading } from "../components/Notice";

function TradeTable({ trades }) {
  if (!trades?.length) return null;
  return (
    <div className="tbl-scroll" style={{ marginTop: 12 }}>
      <table className="RoboAdvisor-table">
        <thead>
          <tr>
            <th>Side</th>
            <th>Instrument</th>
            <th className="num">Units</th>
            <th className="num">Price</th>
            <th className="num">Amount</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t, i) => (
            <tr key={i}>
              <td>{t.side}</td>
              <td>{t.symbol || t.instrumentId}{t.name ? ` — ${t.name}` : ""}</td>
              <td className="num">{num(t.units, 6)}</td>
              <td className="num">{num(t.price, 4)}</td>
              <td className="num">{money(t.grossAmount)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DriftTable({ rows }) {
  return (
    <div className="tbl-scroll">
      <table className="RoboAdvisor-table">
        <thead>
          <tr>
            <th>Instrument</th>
            <th className="num">Target %</th>
            <th className="num">Actual %</th>
            <th className="num">Drift (pts)</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((d) => (
            <tr key={d.symbol}>
              <td>{d.symbol === "CASH" ? "Cash (uninvested)" : d.symbol}</td>
              <td className="num">{num(d.targetWeight, 2)}</td>
              <td className="num">{num(d.actualWeight, 2)}</td>
              <td className="num">{num(d.driftPct, 2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function PortfolioPage() {
  const { sessionDate } = useSession();
  const { ask, pending } = useAppData();
  const [tick, setTick] = useState(0);
  const refresh = () => setTick((t) => t + 1);

  const holdings = useLoad(() => api.getHoldings(), [sessionDate, tick]);
  const prices = useLoad(() => api.getPrices(sessionDate), [sessionDate, tick]);
  const elig = useLoad(() => api.getEligibility(), [tick]);
  const events = useLoad(() => api.getEvents(), [tick]);
  const audit = useLoad(() => api.myAudit(50), [tick]);

  const [investing, setInvesting] = useState(null);
  const [investResult, setInvestResult] = useState(null);
  const [investError, setInvestError] = useState(null);

  const [preview, setPreview] = useState(null);
  const [previewKind, setPreviewKind] = useState(null); // rebalance | switch | revert
  const [previewing, setPreviewing] = useState(false);
  const [rbError, setRbError] = useState(null);
  const [rbResult, setRbResult] = useState(null);
  const [rbBusy, setRbBusy] = useState(false);
  const [explained, setExplained] = useState(null);
  const [staleNotice, setStaleNotice] = useState(false);

  const h = holdings.data;
  const e = elig.data;
  const eligible = new Set(e?.eligiblePortfolios || []);
  const holdingCode = h?.portfolioCode || null;
  const hasHoldings = Boolean(holdingCode) || (h?.byInstrument?.length > 0);

  useEffect(() => {
    setPreview(null);
    setPreviewKind(null);
    setStaleNotice(false);
  }, [sessionDate]);

  useEffect(() => {
    if (rbError) setRbResult(null);
  }, [rbError]);

  async function handleStale(err, reload) {
    if (err?.code !== "preview_stale") {
      setRbError(err);
      return;
    }
    setStaleNotice(true);
    setRbError(err);
    try {
      setPreview(await reload());
    } catch (reloadErr) {
      setPreview(null);
      setRbError(reloadErr);
    }
  }

  async function invest(code) {
    setInvesting(code);
    setInvestError(null);
    setInvestResult(null);
    try {
      const r = await api.invest(code);
      setInvestResult({ ...r, code, kind: "invest" });
      refresh();
    } catch (err) {
      setInvestError(err);
      refresh();
    } finally {
      setInvesting(null);
    }
  }

  async function loadSwitchPreview(code) {
    setPreviewing(true);
    setRbError(null);
    setRbResult(null);
    setExplained(null);
    setStaleNotice(false);
    setInvestError(null);
    try {
      const data = await api.switchPreview(code);
      setPreview(data);
      setPreviewKind("switch");
    } catch (err) {
      setPreview(null);
      setPreviewKind(null);
      setInvestError(err);
    } finally {
      setPreviewing(false);
    }
  }

  async function loadPreview() {
    setPreviewing(true);
    setRbError(null);
    setRbResult(null);
    setExplained(null);
    setStaleNotice(false);
    try {
      setPreview(await api.rebalancePreview());
      setPreviewKind("rebalance");
    } catch (err) {
      setPreview(null);
      setPreviewKind(null);
      setRbError(err);
    } finally {
      setPreviewing(false);
    }
  }

  async function loadRevertPreview() {
    setPreviewing(true);
    setRbError(null);
    setRbResult(null);
    setExplained(null);
    setStaleNotice(false);
    try {
      setPreview(await api.revertPreview());
      setPreviewKind("revert");
    } catch (err) {
      setPreview(null);
      setPreviewKind(null);
      setRbError(err);
    } finally {
      setPreviewing(false);
    }
  }

  async function doRebalance() {
    setRbBusy(true);
    setRbError(null);
    setStaleNotice(false);
    try {
      const r = await api.rebalance(preview.previewToken);
      setRbResult({ kind: "rebalance", ...r });
      setPreview(null);
      setPreviewKind(null);
      refresh();
    } catch (err) {
      await handleStale(err, () => api.rebalancePreview());
      if (err?.code === "preview_stale") setPreviewKind("rebalance");
    } finally {
      setRbBusy(false);
    }
  }

  async function doSwitch() {
    setRbBusy(true);
    setRbError(null);
    setInvestError(null);
    setStaleNotice(false);
    try {
      const r = await api.switchTo(preview.portfolioCode, preview.previewToken);
      setInvestResult({ ...r, code: preview.portfolioCode, kind: "switch" });
      setPreview(null);
      setPreviewKind(null);
      refresh();
    } catch (err) {
      await handleStale(err, () => api.switchPreview(preview.portfolioCode));
      if (err?.code === "preview_stale") setPreviewKind("switch");
      else setInvestError(err);
    } finally {
      setRbBusy(false);
    }
  }

  async function doRevert() {
    setRbBusy(true);
    setRbError(null);
    try {
      const r = await api.revertRebalance();
      setRbResult({ kind: "revert", ...r });
      setPreview(null);
      setPreviewKind(null);
      refresh();
    } catch (err) {
      setRbError(err);
    } finally {
      setRbBusy(false);
    }
  }

  async function explainRebalance() {
    const turn = await ask("Explain the rebalance I just performed and why those trades restore my target allocation.");
    setExplained(turn);
  }

  const cardAction = (p) => {
    if (!holdingCode) {
      return {
        label: investing === p.code ? "Placing order…" : eligible.has(p.code) ? "Invest cash here" : "Request anyway",
        onClick: () => invest(p.code),
      };
    }
    if (holdingCode === p.code) {
      return { label: "Current portfolio", onClick: null, disabled: true };
    }
    return {
      label: previewing && preview?.portfolioCode === p.code ? "Calculating…" : eligible.has(p.code) ? "Switch to this portfolio" : "Request switch anyway",
      onClick: () => loadSwitchPreview(p.code),
    };
  };

  return (
    <div className="main">
      <div className="page-header">
        <h1>Portfolio & trading</h1>
        <p>
          Session date {sessionDate}. All trades are simulated at that date&apos;s prices.
          Change the session date in the sidebar to revalue at a different day&apos;s prices.
        </p>
      </div>

      <section className="RoboAdvisor">
        <div className="RoboAdvisor-head"><h2>What you&apos;re eligible for</h2></div>
        <div className="RoboAdvisor-body">
          {elig.loading && <Loading what="Checking eligibility" />}
          <ErrorNote error={elig.error} onRetry={elig.reload} />
          {e && !e.riskCategory && (
            <div className="callout">Complete your <Link to="/profiling">risk conversation</Link> first — your category decides which portfolios you can use.</div>
          )}
          {e?.riskCategory && (
            <>
              <p>
                Risk category <strong>{human(e.riskCategory)}</strong> · highest portfolio <strong>{e.maxPortfolio} {portfolioName(e.maxPortfolio)}</strong> ·
                knowledge check <strong>{e.ckaPassed ? "passed" : "not passed"}</strong> · policy {e.policyVersion}
              </p>
              {e.firedRules.length > 0 && (
                <div className="callout">
                  <strong>Rules applied to you</strong> (also written to the audit log)
                  {e.firedRules.map((r) => <div key={r.code} style={{ marginTop: 4 }}><code>{r.code}</code> · cap {r.cap} — {r.explanation}</div>)}
                </div>
              )}
              {!e.ckaPassed && (
                <div className="callout warn">
                  Knowledge check not passed. Satellite ETFs are blocked (cap 0%) and portfolios above the policy&apos;s non-CKA cap (<strong>P5</strong>) are closed until you pass the CKA-style check. Other overrides may cap you even lower.
                </div>
              )}
              {!e.ckaPassed && <CkaCheck onPassed={refresh} />}
            </>
          )}
        </div>
      </section>

      <section className="RoboAdvisor">
        <div className="RoboAdvisor-head"><h2>Session-date prices</h2></div>
        <div className="RoboAdvisor-body">
          {prices.loading && <Loading what="Loading prices" />}
          <ErrorNote error={prices.error} onRetry={prices.reload} />
          {prices.data && (
            <>
              <p>
                {prices.data.prices?.length ?? 0} instruments on {prices.data.sessionDate}
              </p>
              <div className="tbl-scroll">
                <table className="RoboAdvisor-table">
                  <thead>
                    <tr>
                      <th>Date</th>
                      <th>Symbol</th>
                      <th>Name</th>
                      <th className="num">Price</th>
                      <th>Asset class</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(prices.data.prices || []).map((p) => (
                      <tr key={p.symbol}>
                        <td>{p.priceDate}</td>
                        <td>{p.symbol}</td>
                        <td>{p.name}{p.isSatellite ? " (satellite)" : ""}</td>
                        <td className="num">{num(p.price, 4)}</td>
                        <td>{human(p.assetClass)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      </section>

      <section className="RoboAdvisor">
        <div className="RoboAdvisor-head"><h2>{holdingCode ? "Switch portfolio" : "Invest"}</h2></div>
        <div className="RoboAdvisor-body">
          {holdings.loading && <Loading what="Loading balance" />}
          <ErrorNote error={holdings.error} onRetry={holdings.reload} />
          {h && (
            <p>
              Cash available: <strong>{money(h.cash)}</strong>
              {h.portfolioCode ? <> · currently holding <strong>{h.portfolioCode} {h.portfolioName}</strong></> : " · nothing invested yet"}
            </p>
          )}
          <p style={{ color: "var(--ink-soft)", fontSize: 13.5 }}>
            {holdingCode
              ? "Switching sells what you hold and buys the destination mix at this session date's prices. You will preview the trades before anything executes. Portfolios above your category are still shown; requesting one is refused and sent to an officer."
              : "Portfolios above your category are shown so you can see why they're closed. If you request one, it will be refused with an explanation and sent to an officer."}
          </p>

          <div className="p-grid">
            {PORTFOLIOS.map((p) => {
              const ok = eligible.has(p.code);
              const action = cardAction(p);
              return (
                <div key={p.code} className={`p-card ${ok ? "" : "closed"}`}>
                  <div className="p-code">{p.code}</div>
                  <div className="p-name">{p.name}</div>
                  <span className={`pill ${ok ? "good" : "neutral"}`}>{holdingCode === p.code ? "Held" : ok ? "Eligible" : "Not eligible"}</span>
                  <button
                    className={`btn ${ok && !action.disabled ? "" : "secondary"}`}
                    style={{ marginTop: 10, width: "100%" }}
                    disabled={action.disabled || investing !== null || previewing || rbBusy || !e?.riskCategory}
                    onClick={action.onClick || undefined}
                  >
                    {action.label}
                  </button>
                </div>
              );
            })}
          </div>

          {investError && (
            <div className={`callout ${investError.code === "suitability_refused" ? "warn" : "bad"}`} role="alert">
              <strong>{investError.code === "suitability_refused" ? "Request refused" : "Order not placed"}</strong>
              <div>{investError.message}</div>
              {investError.code === "suitability_refused" && (
                <div className="hint">This request has been recorded and sent to an officer for review.</div>
              )}
              {investError.correlationId && <div className="hint">Reference: {investError.correlationId}</div>}
            </div>
          )}
          {investResult && (
            <div className="callout good">
              {investResult.kind === "switch" ? "Switched to" : "Order executed in"}{" "}
              <strong>{investResult.code} {portfolioName(investResult.code)}</strong> at {investResult.sessionDate} prices.
              {" "}{investResult.trades?.length ?? 0} trades · cash left {money(investResult.cash)} · decision {investResult.decisionId}
              <TradeTable trades={investResult.trades} />
            </div>
          )}

          {previewKind === "switch" && preview && (
            <div style={{ marginTop: 20 }}>
              {staleNotice && (
                <div className="callout warn">The previous preview was out of date. Review these updated trades and confirm again.</div>
              )}
              <p><strong>Switch to {preview.portfolioCode} {preview.portfolioName}</strong> · total value {money(preview.totalValue)} · cash {money(preview.cash)}</p>
              <div className="callout">{preview.explanation}</div>
              <h3 style={{ margin: "16px 0 8px" }}>Drift from {preview.portfolioCode} targets</h3>
              <DriftTable rows={preview.drift} />
              <h3 style={{ margin: "16px 0 8px" }}>Proposed trades</h3>
              {preview.trades.length === 0 ? (
                <div className="hint">No trades needed — you&apos;re already on that allocation.</div>
              ) : (
                <TradeTable trades={preview.trades} />
              )}
              <button className="btn" style={{ marginTop: 14 }} onClick={doSwitch} disabled={rbBusy || preview.trades.length === 0}>
                {rbBusy ? "Switching…" : `Confirm switch to ${preview.portfolioCode}`}
              </button>
            </div>
          )}

          {h && h.byInstrument?.length > 0 && (
            <>
              <h3 style={{ margin: "20px 0 8px" }}>Positions</h3>
              <div className="tbl-scroll">
                <table className="RoboAdvisor-table">
                  <thead><tr><th>Instrument</th><th className="num">Units</th><th className="num">Price</th><th className="num">Units × price</th><th className="num">Stored value</th><th className="num">Target %</th><th className="num">Actual %</th></tr></thead>
                  <tbody>
                    {h.byInstrument.map((r) => {
                      const product = Number(r.units) * Number(r.price);
                      const matches = Math.abs(product - Number(r.value)) < 0.02;
                      return (
                      <tr key={r.instrumentId}>
                        <td>{r.symbol} — {r.name}{r.isSatellite ? " (satellite)" : ""}</td>
                        <td className="num">{num(r.units, 6)}</td><td className="num">{num(r.price, 4)}</td>
                        <td className="num">{money(product)}{matches ? " ✓" : ""}</td>
                        <td className="num">{money(r.value)}</td><td className="num">{num(r.targetWeight, 2)}</td><td className="num">{num(r.actualWeight, 2)}</td>
                      </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </>
          )}
          <div style={{ marginTop: 20 }}><DisclosureBox /></div>
        </div>
      </section>

      <section className="RoboAdvisor">
        <div className="RoboAdvisor-head"><h2>Rebalance</h2></div>
        <div className="RoboAdvisor-body">
          <p style={{ color: "var(--ink-soft)" }}>
            Preview the trades needed to bring your portfolio back to its target weights at the {sessionDate} prices. Nothing
            happens until you confirm.
          </p>
          {!hasHoldings && (
            <div className="hint" style={{ marginBottom: 12 }}>Invest first — there is nothing to rebalance or revert yet.</div>
          )}
          <button className="btn" onClick={loadPreview} disabled={!hasHoldings || previewing || rbBusy}>{previewing && previewKind === "rebalance" ? "Calculating…" : "Preview rebalance"}</button>{" "}
          <button className="btn secondary" onClick={loadRevertPreview} disabled={!hasHoldings || rbBusy || previewing}>{previewing && previewKind === "revert" ? "Loading…" : "Preview revert"}</button>
          <ErrorNote error={rbError} />
          {staleNotice && previewKind === "rebalance" && (
            <div className="callout warn">The session date (or your position) changed. Review the updated preview and confirm again.</div>
          )}

          {previewKind === "rebalance" && preview && (
            <div style={{ marginTop: 20 }}>
              <p><strong>{preview.portfolioCode} {preview.portfolioName}</strong> · total value {money(preview.totalValue)} · cash {money(preview.cash)}</p>
              <div className="callout">{preview.explanation}</div>
              <h3 style={{ margin: "16px 0 8px" }}>Drift from target</h3>
              <DriftTable rows={preview.drift} />
              <h3 style={{ margin: "16px 0 8px" }}>Proposed trades</h3>
              {preview.trades.length === 0 ? (
                <div className="hint">No trades needed — you&apos;re already on target.</div>
              ) : (
                <TradeTable trades={preview.trades} />
              )}
              <button className="btn" style={{ marginTop: 14 }} onClick={doRebalance} disabled={rbBusy || preview.trades.length === 0}>
                {rbBusy ? "Executing…" : "Confirm and rebalance"}
              </button>
            </div>
          )}

          {previewKind === "revert" && preview && (
            <div style={{ marginTop: 20 }}>
              <div className="callout">{preview.explanation}</div>
              <p>
                Restored portfolio {preview.portfolioCode ? <strong>{preview.portfolioCode} {preview.portfolioName}</strong> : "—"}
                {" "}· cash {money(preview.cash)} · total value {money(preview.totalValue)} at {preview.sessionDate} prices
              </p>
              <div className="tbl-scroll">
                <table className="RoboAdvisor-table">
                  <thead>
                    <tr>
                      <th>Instrument</th>
                      <th className="num">Units</th>
                      <th className="num">Price</th>
                      <th className="num">Value</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(preview.positions || []).map((p) => (
                      <tr key={p.symbol}>
                        <td>{p.symbol}{p.name ? ` — ${p.name}` : ""}</td>
                        <td className="num">{num(p.units, 6)}</td>
                        <td className="num">{num(p.price, 4)}</td>
                        <td className="num">{money(p.value)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <button className="btn" style={{ marginTop: 14 }} onClick={doRevert} disabled={rbBusy}>
                {rbBusy ? "Reverting…" : "Confirm revert"}
              </button>
            </div>
          )}

          {rbResult && (
            <div style={{ marginTop: 20 }}>
              <div className="callout good">
                {rbResult.kind === "rebalance"
                  ? rbResult.alreadyOnTarget
                    ? `Already on target at ${rbResult.sessionDate} prices — no trades were placed.`
                    : `Rebalance executed at ${rbResult.sessionDate} prices (${rbResult.trades?.length ?? 0} trades). Decision ${rbResult.decisionId}.`
                  : `Last rebalance reverted — holdings restored to how they were just before it. Decision ${rbResult.decisionId}.`}
              </div>
              {rbResult.kind === "rebalance" && !rbResult.alreadyOnTarget && (
                <>
                  <button className="btn secondary" onClick={explainRebalance} disabled={pending}>
                    {pending ? "Asking…" : "Explain this rebalance to me"}
                  </button>
                  {explained && <div style={{ marginTop: 12 }}><AnswerCard turn={explained} /></div>}
                </>
              )}
            </div>
          )}
        </div>
      </section>

      <section className="RoboAdvisor">
        <div className="RoboAdvisor-head"><h2>My activity & decision history</h2></div>
        <div className="RoboAdvisor-body">
          <h3 style={{ marginBottom: 8 }}>Portfolio events</h3>
          {events.data && (
            <div className="tbl-scroll">
              <table className="RoboAdvisor-table">
                <thead><tr><th className="num">#</th><th>Event</th><th>Session date</th><th>Decision</th><th>Recorded</th></tr></thead>
                <tbody>
                  {events.data.length === 0 && <tr><td colSpan={5} className="hint">No portfolio events yet.</td></tr>}
                  {events.data.map((ev) => (
                    <tr key={ev.eventId}>
                      <td className="num">{ev.seq}</td><td>{human(ev.eventType)}{ev.revertsEventId ? " (reverts an earlier event)" : ""}</td>
                      <td>{ev.sessionDate}</td><td>{ev.decisionId ? "linked" : "—"}</td><td>{fmtTime(ev.createdAt)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <h3 style={{ margin: "20px 0 8px" }}>Decisions about you</h3>
          <ErrorNote error={audit.error} onRetry={audit.reload} />
          {audit.data && audit.data.length === 0 && <div className="hint">Nothing recorded yet.</div>}
          <ShowMore items={audit.data} initial={10}>
            {(d) => (
              <details key={d.decisionId} className="source-item">
                <summary style={{ cursor: "pointer" }}>
                  <strong>{d.decisionId}</strong> · {human(d.decisionType)} · <StatusPill status={d.status} /> · {fmtTime(d.timestamp)}
                </summary>
                <div style={{ marginTop: 8 }}>
                  <div><strong>Input:</strong> {d.input || "—"}</div>
                  <div style={{ marginTop: 6 }}><strong>Outcome:</strong> {d.output || "—"}</div>
                  {d.humanOverride && <div style={{ marginTop: 6 }}><strong>Reviewed by an officer:</strong> {human(d.humanOverride.action)} — {d.humanOverride.finalOutput}</div>}
                  <div className="hint">Policy {d.policyVersion || "—"} · session date {d.sessionDate || "—"}</div>
                </div>
              </details>
            )}
          </ShowMore>
        </div>
      </section>
    </div>
  );
}
