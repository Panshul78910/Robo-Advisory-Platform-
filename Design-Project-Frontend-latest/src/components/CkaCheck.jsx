import { useState } from "react";
import { api } from "../api";
import { ErrorNote } from "./Notice";

// A.3: CKA-style check gates portfolios above the non-CKA cap and any satellite ETF.
const CKA = [
  { q: "Which is generally expected to move up and down the most over a few months?", o: ["A cash fund", "A short-term bond fund", "A global equity index fund"], a: 2 },
  { q: "What does rebalancing do when a portfolio has drifted from its targets?", o: ["Sells holdings that have grown above target and buys those that fell below", "Moves everything into cash", "Buys more of whatever went up the most"], a: 0 },
  { q: "The main purpose of diversification is to…", o: ["Guarantee a minimum return", "Reduce the damage if any single holding does badly", "Remove all investment risk"], a: 1 },
  { q: "Past performance of an investment is…", o: ["A reliable guide to future returns", "A guarantee of future returns", "Not a guarantee of future results"], a: 2 },
  { q: "A satellite (thematic or sector) ETF usually carries…", o: ["Higher concentration risk than a broad index fund", "Lower risk than cash", "No market risk"], a: 0 },
];
const PASS_MARK = 4;

export default function CkaCheck({ onPassed }) {
  const [picked, setPicked] = useState({});
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    const score = CKA.filter((q, i) => picked[i] === q.a).length;
    setError(null);
    if (score < PASS_MARK) return setResult({ passed: false, score });
    setBusy(true);
    try {
      await api.passCka();
      setResult({ passed: true, score });
      onPassed?.();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <h3 style={{ marginBottom: 8 }}>CKA-style knowledge check</h3>
      <p style={{ color: "var(--ink-soft)" }}>
        Pass this check (at least {PASS_MARK} of {CKA.length}) to raise the highest portfolio available and to allow satellite ETF exposure.
        Your risk category itself does not change.
      </p>
      {CKA.map((item, i) => (
        <fieldset key={i} className="quiz-q">
          <legend>{i + 1}. {item.q}</legend>
          {item.o.map((opt, j) => (
            <label key={j} className="quiz-opt">
              <input type="radio" name={`cka${i}`} checked={picked[i] === j} onChange={() => setPicked((p) => ({ ...p, [i]: j }))} /> {opt}
            </label>
          ))}
        </fieldset>
      ))}
      <ErrorNote error={error} />
      {result && !result.passed && <div className="callout warn">You got {result.score} of {CKA.length}. Have another look and try again.</div>}
      {result?.passed && <div className="callout good">Passed. Higher-risk portfolios and satellite ETFs are now in scope, subject to your category cap.</div>}
      <button className="btn" onClick={submit} disabled={busy || Object.keys(picked).length < CKA.length}>
        {busy ? "Submitting…" : "Submit CKA answers"}
      </button>
    </div>
  );
}
