import { useState } from "react";
import { api } from "../api";

// A.3: what the deterministic suitability engine decided, and why. The fired
// rules are already written to the audit log by the backend -- this surfaces
// the same records to the client.
export default function Eligibility({ eligibility, error, onRefresh }) {
  const [expanded, setExpanded] = useState(false);
  const [ckaBusy, setCkaBusy] = useState(false);
  const [ckaError, setCkaError] = useState("");

  async function takeCka() {
    setCkaBusy(true);
    setCkaError("");
    try {
      // Returns the refreshed EligibilityDto itself, but re-reading keeps one
      // source of truth in the parent.
      await api.passCka();
      await onRefresh?.();
    } catch (err) {
      setCkaError(err.message);
    } finally {
      setCkaBusy(false);
    }
  }

  if (error) {
    return (
      <section className="RoboAdvisor">
        <div className="RoboAdvisor-head">
          <h2>Suitability</h2>
        </div>
        <div className="RoboAdvisor-body tight">
          <div className="error-text">{error}</div>
        </div>
      </section>
    );
  }

  if (!eligibility) {
    return (
      <section className="RoboAdvisor">
        <div className="RoboAdvisor-head">
          <h2>Suitability</h2>
        </div>
        <div className="RoboAdvisor-body tight">
          <div className="empty-state">Loading…</div>
        </div>
      </section>
    );
  }

  const { riskCategory, eligiblePortfolios, maxPortfolio, ckaPassed, satelliteCapPct, firedRules } =
    eligibility;

  return (
    <section className="RoboAdvisor" data-testid="eligibility">
      <div className="RoboAdvisor-head">
        <h2>Suitability &amp; eligibility</h2>
      </div>
      <div className="RoboAdvisor-body">
        <div className="stat-row" style={{ borderBottom: "none" }}>
          <div className="stat">
            <div className="stat-label">Risk category</div>
            <div className="stat-value" style={{ fontSize: 18 }} data-testid="elig-category">
              {riskCategory ? riskCategory.replace(/_/g, " ") : "not assessed yet"}
            </div>
          </div>
          <div className="stat">
            <div className="stat-label">Highest permitted</div>
            <div className="stat-value" style={{ fontSize: 18 }} data-testid="elig-max">
              {maxPortfolio ?? "—"}
            </div>
          </div>
          <div className="stat">
            <div className="stat-label">Satellite cap</div>
            <div className="stat-value" style={{ fontSize: 18 }}>
              {satelliteCapPct}%
            </div>
          </div>
          <div className="stat">
            <div className="stat-label">Knowledge check</div>
            <div className="stat-value" style={{ fontSize: 18 }} data-testid="elig-cka">
              {ckaPassed ? "passed" : "not passed"}
            </div>
          </div>
        </div>

        <p style={{ marginTop: 4 }}>
          <strong>Portfolios available to you:</strong>{" "}
          <span data-testid="elig-portfolios">
            {eligiblePortfolios?.length ? eligiblePortfolios.join(", ") : "none yet"}
          </span>
        </p>

        {!ckaPassed && (
          <div className="disclosure-box" style={{ marginTop: 12 }}>
            <p style={{ margin: "0 0 10px" }}>
              Some portfolios are gated behind a knowledge check. Completing it may open
              more of them to you.
            </p>
            <button className="btn" onClick={takeCka} disabled={ckaBusy} data-testid="cka-button">
              {ckaBusy ? "Recording…" : "Complete knowledge check"}
            </button>
            {ckaError && <div className="error-text">{ckaError}</div>}
          </div>
        )}

        {firedRules?.length > 0 && (
          <div style={{ marginTop: 16 }}>
            <button
              className="btn"
              type="button"
              onClick={() => setExpanded((v) => !v)}
              data-testid="rules-toggle"
            >
              {expanded ? "Hide" : "Show"} rules applied ({firedRules.length})
            </button>
            {expanded && (
              <table className="RoboAdvisor-table" style={{ marginTop: 12 }} data-testid="fired-rules">
                <thead>
                  <tr>
                    <th>Code</th>
                    <th>Kind</th>
                    <th>Cap</th>
                    <th>Explanation</th>
                  </tr>
                </thead>
                <tbody>
                  {firedRules.map((r) => (
                    <tr key={r.code}>
                      <td>
                        <code style={{ fontSize: 11 }}>{r.code}</code>
                      </td>
                      <td>{r.kind}</td>
                      <td>{r.cap ?? "—"}</td>
                      <td>{r.explanation}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
