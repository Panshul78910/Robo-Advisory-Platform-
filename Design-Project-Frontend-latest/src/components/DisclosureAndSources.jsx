import { useState } from "react";

// Mirrors the backend's policy/disclosure.md (Appendix G). The backend also appends
// this block to every recommendation it generates; this copy is for the dashboard.
export const DISCLOSURE_CLAUSES = [
  {
    label: "Key risks",
    text: "The value of investments can fall as well as rise, and you may get back less than you invested. Past performance does not indicate future results. Portfolios holding equities, emerging-market or satellite instruments can move sharply over short periods.",
  },
  {
    label: "Assumptions and limitations",
    text: "This is a simulated advisory environment. Valuations use the session date's published prices, not live market data, and assume trades execute in full at those prices with no fees, spreads, taxes or slippage. Recommendations are based solely on the profile information you have given us; if your circumstances differ from what is on file, the recommendation may not be appropriate.",
  },
  {
    label: "No guarantee",
    text: "Nothing here is a guarantee of any return, income or outcome, and nothing here is a personal recommendation to buy or sell any specific security. This information does not constitute tax or legal advice.",
  },
];

// A.4(ii) / C.3: the disclosure must not be suppressible. The "Hide" button exists
// on purpose and only explains why it can't hide anything.
export function DisclosureBox() {
  const [toast, setToast] = useState(false);

  function handleHideClick() {
    setToast(true);
    setTimeout(() => setToast(false), 3200);
  }

  return (
    <>
      <div className="disclosure-box">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 8 }}>
          <strong style={{ margin: 0 }}>Important information</strong>
          <button className="btn secondary" style={{ padding: "3px 10px", fontSize: 12 }} onClick={handleHideClick}>
            Hide
          </button>
        </div>
        {DISCLOSURE_CLAUSES.map((c) => (
          <div className="clause" key={c.label}>
            <strong>{c.label}.</strong> {c.text}
          </div>
        ))}
      </div>
      {toast && <div className="toast">This disclosure cannot be hidden — it is required on every recommendation.</div>}
    </>
  );
}

// C.2: each citation is collapsed by default; expanding shows the exact retrieved
// passage with its provenance (document, section, page, retrieval score).
export function SourceList({ sources }) {
  if (!sources || sources.length === 0) return null;
  return (
    <div className="source-list">
      <div style={{ fontWeight: 500, marginBottom: 6 }}>
        {sources.length} source{sources.length > 1 ? "s" : ""} used — click one to read the exact passage
      </div>
      {sources.map((s, i) => (
        <details className="source-item" key={s.chunkRef || i}>
          <summary className="source-meta" style={{ cursor: "pointer" }}>
            [{i + 1}] {s.title || s.docId}
            {s.section ? ` — ${s.section}` : ""}
            {s.page ? ` (p.${s.page})` : ""}
          </summary>
          <div className="source-text" style={{ marginTop: 8 }}>
            {s.text}
          </div>
          <div style={{ marginTop: 8, fontSize: 12, color: "var(--ink-soft)", fontFamily: "var(--font-mono)" }}>
            doc {s.docId}
            {s.chunkRef ? ` · chunk ${s.chunkRef}` : ""}
            {s.score !== null && s.score !== undefined ? ` · relevance ${Number(s.score).toFixed(2)}` : ""}
          </div>
        </details>
      ))}
    </div>
  );
}
