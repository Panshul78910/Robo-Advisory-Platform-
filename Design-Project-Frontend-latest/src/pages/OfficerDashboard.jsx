import { useMemo, useState } from "react";
import { api } from "../api";
import { useAuth } from "../context/AuthContext";
import { useLoad } from "../hooks";
import { fmtTime, human, num } from "../lib/format";
import { StatusPill } from "../components/ConfidenceBadge";
import { SourceList } from "../components/DisclosureAndSources";
import { ErrorNote, Loading } from "../components/Notice";

const TABS = ["Client overview", "Escalation & review queue", "Audit log & provenance", "System & vector store"];

// Plain-language names for the AI-safety refusal categories (reasonCode "ai_safety:<category>").
const REFUSAL_LABELS = {
  prompt_injection: "Prompt injection",
  jailbreak: "Jailbreak attempt",
  prompt_leak: "System-prompt leak attempt",
  confidential_data: "Confidential logic / data request",
  other_client_data: "Other client's data / PII",
  toxic_hateful: "Toxic or hateful content",
  illegal_request: "Illegal / crime-facilitating request",
  out_of_scope: "Out of scope (not financial advice)",
  leak: "System-prompt leak attempt",
};
const isRefusal = (e) => e.refused || e.decisionStatus === "refused" || e.status === "refused" || String(e.reasonCode || "").startsWith("ai_safety");
const refusalCategory = (e) => {
  const raw = String(e.reasonCode || "").replace(/^ai_safety:?/, "");
  return raw || "other";
};
const refusalLabel = (c) => REFUSAL_LABELS[c] || human(c);

export default function OfficerDashboard() {
  const { user } = useAuth();
  const [tab, setTab] = useState(TABS[0]);
  return (
    <div className="main">
      <div className="page-header">
        <h1>Officer console — {user.displayName}</h1>
        <p>Explainability & human oversight console — all five Appendix D clients and the escalation queue</p>
      </div>
      <div className="tabs">
        {TABS.map((t) => (
          <button key={t} className={`tab-btn ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>{t}</button>
        ))}
      </div>
      {tab === TABS[0] && <ClientOverview />}
      {tab === TABS[1] && <ReviewQueue reviewer={user.displayName} />}
      {tab === TABS[2] && <AuditLog />}
      {tab === TABS[3] && <SystemPanel />}
    </div>
  );
}

// ---------------------------------------------------------------- A.1(vi)
function ClientOverview() {
  const clients = useLoad(() => api.adminClients(), []);
  const [selected, setSelected] = useState(null);
  const detail = useLoad(() => (selected ? api.adminClient(selected) : Promise.resolve(null)), [selected]);

  return (
    <section className="RoboAdvisor">
      <div className="RoboAdvisor-head"><h2>All clients</h2></div>
      <div className="RoboAdvisor-body tight">
        {clients.loading && <Loading what="Loading clients" />}
        <ErrorNote error={clients.error} onRetry={clients.reload} />
        {clients.data && (
          <div className="callout" style={{ marginBottom: 12 }}>
            {clients.data.length} client record{clients.data.length === 1 ? "" : "s"} in the database.
            {clients.data.filter((c) => /^C00[1-5]$/.test(c.clientCode)).length === 5
              ? " All five Appendix D synthetic clients (C001–C005) are present. Open the Escalation & review queue tab for pending reviews and AI-safety refusals."
              : " Seeded Appendix D logins are C001–C005."}
          </div>
        )}
        {clients.data && (
          <div className="tbl-scroll">
            <table className="RoboAdvisor-table">
              <thead>
                <tr><th>Client</th><th>Name</th><th>Email</th><th>Risk category</th><th>CKA</th><th className="num">Pending escalations</th><th className="num">Refusals</th></tr>
              </thead>
              <tbody>
                {clients.data.map((c) => (
                  <tr key={c.clientId} onClick={() => setSelected(c.clientId)} style={{ cursor: "pointer", background: c.clientId === selected ? "var(--paper)" : "transparent" }}>
                    <td>{c.clientCode}</td><td>{c.fullName}</td><td>{c.email}</td>
                    <td>{c.riskCategory ? human(c.riskCategory) : "Not assessed"}</td>
                    <td>{c.ckaPassed ? "Passed" : "No"}</td><td className="num">{c.pendingEscalations}</td><td className="num">{c.refusals ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {selected && (
        <>
          <div className="RoboAdvisor-head" style={{ borderTop: "1px solid var(--rule)" }}><h3 style={{ margin: 0 }}>Stored record</h3></div>
          <div className="RoboAdvisor-body">
            {detail.loading && <Loading what="Loading record" />}
            <ErrorNote error={detail.error} onRetry={detail.reload} />
            {detail.data && <KeyValues data={detail.data} />}
          </div>
        </>
      )}
    </section>
  );
}

function BlockText({ children }) {
  const text = children == null ? "—" : String(children);
  return <div className="block-text">{text}</div>;
}

function displayValue(v) {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (typeof v === "number") {
    if (v > 1e12) return fmtTime(v);
    return num(v);
  }
  if (typeof v === "object") {
    return <pre className="pre">{JSON.stringify(v, null, 2)}</pre>;
  }
  const s = String(v);
  if (s.length > 160) return <BlockText>{s}</BlockText>;
  return s;
}

function KeyValues({ data }) {
  const skip = new Set(["id", "client_id", "clientId", "score", "risk_score", "riskScore"]);
  return (
    <div className="tbl-scroll">
      <table className="RoboAdvisor-table" style={{ maxWidth: 720, tableLayout: "fixed" }}>
        <tbody>
          {Object.entries(data)
            .filter(([k, v]) => !skip.has(k) && v !== null && v !== undefined)
            .map(([k, v]) => (
              <tr key={k}>
                <td style={{ width: 220 }}>{human(k.replace(/([A-Z])/g, "_$1").toLowerCase())}</td>
                <td>{displayValue(/At$/.test(k) && typeof v === "number" ? fmtTime(v) : v)}</td>
              </tr>
            ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------- C.5
const ACTIONS = [
  ["approve", "Approve as-is"],
  ["edit", "Edit output"],
  ["reject", "Override / reject"],
];

function ReviewQueue({ reviewer }) {
  const [status, setStatus] = useState("pending");
  const [catFilter, setCatFilter] = useState("");
  const queue = useLoad(() => api.adminEscalations(status), [status]);
  // Counts for the three views, so the officer can see at a glance what needs attention.
  const counts = useLoad(async () => {
    const [p, r, d] = await Promise.all([api.adminEscalations("pending"), api.adminEscalations("refused"), api.adminEscalations("resolved")]);
    return { pending: p.filter((e) => !isRefusal(e)).length, refused: r.length, resolved: d.filter((e) => !isRefusal(e)).length, refusals: r };
  }, [status]);
  const [drafts, setDrafts] = useState({});
  const [busy, setBusy] = useState(null);
  const [notice, setNotice] = useState(null);
  const [error, setError] = useState(null);

  const setDraft = (id, patch) => setDrafts((p) => ({ ...p, [id]: { action: "approve", ...p[id], ...patch } }));

  // Refusals never appear under Pending or Resolved: they have their own view.
  const rows = (queue.data || []).filter((e) => (status === "refused" ? isRefusal(e) : !isRefusal(e)));
  const categories = [...new Set((counts.data?.refusals || []).map(refusalCategory))].sort();
  const shown = status === "refused" && catFilter ? rows.filter((e) => refusalCategory(e) === catFilter) : rows;
  const byCategory = categories.map((c) => [c, (counts.data?.refusals || []).filter((e) => refusalCategory(e) === c).length]);

  async function submit(item) {
    const d = { action: "approve", ...drafts[item.decisionId] };
    setError(null);
    setNotice(null);
    if (d.action === "edit" && !(d.finalOutput ?? item.aiOutput)?.trim()) return setError({ message: "An edit needs the corrected output." });
    setBusy(item.decisionId);
    try {
      const r = await api.adminReview(item.decisionId, {
        action: d.action,
        ...(d.action === "edit" ? { finalOutput: d.finalOutput ?? item.aiOutput } : {}),
        ...(d.reason ? { reason: d.reason } : {}),
      });
      setNotice(`${r.detail || "Decision recorded."} The human decision is now the final result.`);
      queue.reload();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  }

  return (
    <section>
      <h2>{status === "refused" ? "Queries refused by AI safety" : status === "resolved" ? "Reviewed decisions" : "Decisions awaiting human review"}</h2>
      <div className="filter-row">
        <div className="field">
          <label>Show</label>
          <select value={status} onChange={(e) => { setStatus(e.target.value); setCatFilter(""); }}>
            <option value="pending">Pending{counts.data ? ` (${counts.data.pending})` : ""}</option>
            <option value="refused">Refused by AI safety{counts.data ? ` (${counts.data.refused})` : ""}</option>
            <option value="resolved">Resolved{counts.data ? ` (${counts.data.resolved})` : ""}</option>
          </select>
        </div>
        {status === "refused" && (
          <div className="field">
            <label>Refusal type</label>
            <select value={catFilter} onChange={(e) => setCatFilter(e.target.value)}>
              <option value="">All types</option>
              {categories.map((c) => <option key={c} value={c}>{refusalLabel(c)}</option>)}
            </select>
          </div>
        )}
      </div>
      {status === "refused" && (
        <div className="callout" style={{ marginBottom: 12 }}>
          These are client messages the AI safety layer blocked before they reached the model (prompt injection, jailbreaks, prompt or data leaks,
          toxic or hateful content, illegal requests, and off-topic requests). Nothing here needs approval; it is a record for oversight and for the Checklist D evidence.
          {byCategory.length > 0 && (
            <div className="chip-row" style={{ marginTop: 8 }}>
              {byCategory.map(([c, n]) => (
                <button key={c} className="chip" onClick={() => setCatFilter(catFilter === c ? "" : c)} aria-pressed={catFilter === c}>
                  {refusalLabel(c)}: {n}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
      {queue.loading && <Loading what="Loading queue" />}
      <ErrorNote error={queue.error} onRetry={queue.reload} />
      <ErrorNote error={error} />
      {notice && <div className="callout good">{notice}</div>}
      {!queue.loading && shown.length === 0 && <div className="empty-state">Nothing {status === "refused" ? "has been refused" : status} right now.</div>}
      {shown.map((e) => {
        const d = { action: "approve", ...drafts[e.decisionId] };
        const pending = e.status === "pending";
        const safety = isRefusal(e);
        const cat = safety ? refusalCategory(e) : null;
        return (
          <div className="escalation-item" key={e.escalationId}>
            <div className="meta">
              <strong>{e.decisionId}</strong> — {e.clientName} ({e.clientCode}) — {human(e.decisionType)} · {human(e.reasonCode)} · {fmtTime(e.createdAt)}
            </div>
            {safety && (
              <div className="callout warn" style={{ margin: "8px 0 12px" }}>
                <strong>{refusalLabel(cat)}.</strong> AI safety blocked this client query before it reached the model. Shown: who asked, what they typed, and what the client was told instead.
              </div>
            )}
            <p><strong>Request / input:</strong></p>
            <BlockText>{e.input}</BlockText>
            <p><strong>AI output (proposed):</strong></p>
            <BlockText>{e.aiOutput}</BlockText>
            <p>
              <strong>Confidence:</strong> {e.confidence === null || e.confidence === undefined ? "n/a" : `${Math.round(e.confidence * 100)}%`}
              {" · "}{safety ? "view" : "queue"} <StatusPill status={safety ? "refused" : e.status} />
              {e.decisionStatus && <> · decision <StatusPill status={e.decisionStatus} /></>}
            </p>

            {pending && (
              <>
                <div className="radio-row">
                  {ACTIONS.map(([value, label]) => (
                    <label key={value}>
                      <input type="radio" name={`a_${e.escalationId}`} checked={d.action === value} onChange={() => setDraft(e.decisionId, { action: value })} />
                      {label}
                    </label>
                  ))}
                </div>
                {d.action === "edit" && (
                  <div className="field">
                    <label>Edited output</label>
                    <textarea value={d.finalOutput ?? e.aiOutput ?? ""} onChange={(ev) => setDraft(e.decisionId, { finalOutput: ev.target.value })} />
                  </div>
                )}
                {d.action !== "approve" && (
                  <div className="field">
                    <label>Reason (recorded in the audit log)</label>
                    <input value={d.reason || ""} onChange={(ev) => setDraft(e.decisionId, { reason: ev.target.value })} />
                  </div>
                )}
                <button className="btn" onClick={() => submit(e)} disabled={busy === e.decisionId}>
                  {busy === e.decisionId ? "Submitting…" : `Submit decision as ${reviewer}`}
                </button>
              </>
            )}
          </div>
        );
      })}
    </section>
  );
}

// ---------------------------------------------------------------- C.6
function AuditLog() {
  const log = useLoad(() => api.adminAudit(300), []);
  const [search, setSearch] = useState("");
  const [clientFilter, setClientFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [selectedId, setSelectedId] = useState(null);

  const rows = log.data || [];
  const uniq = (f) => [...new Set(rows.map(f).filter(Boolean))].sort();
  const clients = useMemo(() => uniq((e) => e.clientName), [rows]); // eslint-disable-line react-hooks/exhaustive-deps
  const types = useMemo(() => uniq((e) => e.decisionType), [rows]); // eslint-disable-line react-hooks/exhaustive-deps
  const statuses = useMemo(() => uniq((e) => e.status), [rows]); // eslint-disable-line react-hooks/exhaustive-deps

  const filtered = rows.filter(
    (e) =>
      (!search || e.decisionId.toLowerCase().includes(search.trim().toLowerCase())) &&
      (!clientFilter || e.clientName === clientFilter) &&
      (!typeFilter || e.decisionType === typeFilter) &&
      (!statusFilter || e.status === statusFilter)
  );
  const chosen = filtered.find((e) => e.decisionId === selectedId) || filtered[0] || null;
  const Sel = ({ label, value, set, options }) => (
    <div className="field">
      <label>{label}</label>
      <select value={value} onChange={(e) => set(e.target.value)}>
        <option value="">All</option>
        {options.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    </div>
  );

  return (
    <section>
      <h2>Decision audit log</h2>
      <div className="filter-row">
        <div className="field"><label>Find decision ID</label><input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="e.g. D-1004" /></div>
        <Sel label="Client" value={clientFilter} set={setClientFilter} options={clients} />
        <Sel label="Decision type" value={typeFilter} set={setTypeFilter} options={types} />
        <Sel label="Status" value={statusFilter} set={setStatusFilter} options={statuses} />
      </div>
      {log.loading && <Loading what="Loading audit log" />}
      <ErrorNote error={log.error} onRetry={log.reload} />

      <div className="RoboAdvisor">
        <div className="RoboAdvisor-body tight tbl-scroll">
          <table className="RoboAdvisor-table">
            <thead><tr><th>Decision ID</th><th>Timestamp</th><th>Client</th><th>Type</th><th className="num">Confidence</th><th>Status</th><th>Overridden?</th></tr></thead>
            <tbody>
              {filtered.map((e) => (
                <tr key={e.decisionId} onClick={() => setSelectedId(e.decisionId)} style={{ cursor: "pointer", background: chosen?.decisionId === e.decisionId ? "var(--paper)" : "transparent" }}>
                  <td>{e.decisionId}</td><td>{fmtTime(e.timestamp)}</td><td>{e.clientName || "—"}</td><td>{human(e.decisionType)}</td>
                  <td className="num">{e.confidence === null || e.confidence === undefined ? "—" : `${Math.round(e.confidence * 100)}%`}</td>
                  <td><StatusPill status={e.status} /></td><td>{e.humanOverride ? "Yes" : "No"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {chosen && (
        <section className="RoboAdvisor">
          <div className="RoboAdvisor-head"><h2>{chosen.decisionId} — {human(chosen.decisionType)}</h2></div>
          <div className="RoboAdvisor-body">
            <p><strong>Client:</strong> {chosen.clientName || "—"} &nbsp;|&nbsp; <strong>Timestamp:</strong> {fmtTime(chosen.timestamp)} &nbsp;|&nbsp; <strong>Session date:</strong> {chosen.sessionDate || "—"}</p>
            <p><strong>Model version:</strong> <code>{chosen.modelVersion || "—"}</code> &nbsp;|&nbsp; <strong>Prompt version:</strong> <code>{chosen.promptVersion || "—"}</code> &nbsp;|&nbsp; <strong>Policy version:</strong> <code>{chosen.policyVersion || "—"}</code></p>
            <p><strong>Confidence at time of decision:</strong> {chosen.confidence === null || chosen.confidence === undefined ? "n/a" : `${Math.round(chosen.confidence * 100)}%`} &nbsp;|&nbsp; <strong>Correlation ID:</strong> <code>{chosen.correlationId || "—"}</code></p>
            <p><strong>Input:</strong></p>
            <div className="source-item"><div className="source-text">{chosen.input || "—"}</div></div>
            <p style={{ marginTop: 16 }}><strong>Output:</strong></p>
            <div className="source-item"><div className="source-text">{chosen.output || "—"}</div></div>
            <p style={{ marginTop: 16 }}><strong>Retrieved sources:</strong> {chosen.retrievedSources?.length ? chosen.retrievedSources.join(", ") : "—"}</p>
            <SourceList sources={chosen.sources} />
            <p style={{ marginTop: 16 }}><strong>Human override:</strong></p>
            {chosen.humanOverride ? (
              <table className="RoboAdvisor-table" style={{ maxWidth: 640 }}>
                <tbody>
                  <tr><td>Action</td><td>{human(chosen.humanOverride.action)}</td></tr>
                  <tr><td>Reason</td><td>{chosen.humanOverride.reason || "—"}</td></tr>
                  <tr><td>AI output before review</td><td><BlockText>{chosen.humanOverride.preOverrideOutput}</BlockText></td></tr>
                  <tr><td>Final output (human decision)</td><td><BlockText>{chosen.humanOverride.finalOutput}</BlockText></td></tr>
                  <tr><td>Reviewed at</td><td>{fmtTime(chosen.humanOverride.reviewedAt)}</td></tr>
                </tbody>
              </table>
            ) : (
              <p style={{ color: "var(--ink-soft)" }}>None — no human override on this decision.</p>
            )}
          </div>
        </section>
      )}
    </section>
  );
}

// ---------------------------------------------------------------- B.4 / B.5 / B.6 evidence
function SystemPanel() {
  const stats = useLoad(() => api.ragStats(), []);
  const [q, setQ] = useState("");
  const [topK, setTopK] = useState(5);
  const [audience, setAudience] = useState("");
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [ing, setIng] = useState({ source: "docs/corpus", corpusLabel: "corpus", audience: "client", reset: false });
  const [msg, setMsg] = useState("");
  const payload = useLoad(() => api.lastPayload(), []);

  async function run(fn, after) {
    setBusy(true);
    setError(null);
    setMsg("");
    try {
      const r = await fn();
      after?.(r);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <h2>Vector store (B.3 / B.4)</h2>
      <div className="RoboAdvisor"><div className="RoboAdvisor-body">
        <h3 style={{ marginBottom: 8 }}>Collection stats</h3>
        <ErrorNote error={stats.error} onRetry={stats.reload} />
        {stats.data && <pre className="pre">{JSON.stringify(stats.data, null, 2)}</pre>}

        <h3 style={{ margin: "20px 0 8px" }}>Test a semantic query</h3>
        <form className="chat-input-row" style={{ marginTop: 0 }} onSubmit={(e) => { e.preventDefault(); run(() => api.ragQuery(q, Number(topK) || 5, audience), setResult); }}>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. who can hold satellite ETFs?" aria-label="Query" />
          <input type="number" min={1} max={50} value={topK} onChange={(e) => setTopK(e.target.value)} style={{ maxWidth: 80 }} aria-label="Top K" />
          <select value={audience} onChange={(e) => setAudience(e.target.value)} aria-label="Audience">
            <option value="">any audience</option><option value="client">client</option><option value="admin">admin</option>
          </select>
          <button className="btn" type="submit" disabled={busy || !q.trim()}>Search</button>
        </form>
        {result && <pre className="pre">{JSON.stringify(result, null, 2)}</pre>}

        <h3 style={{ margin: "20px 0 8px" }}>Update the store</h3>
        <div className="form-grid">
          <div className="field"><label>Folder on the server</label><input value={ing.source} onChange={(e) => setIng({ ...ing, source: e.target.value })} /></div>
          <div className="field"><label>Corpus label</label><input value={ing.corpusLabel} onChange={(e) => setIng({ ...ing, corpusLabel: e.target.value })} /></div>
          <div className="field"><label>Audience</label>
            <select value={ing.audience} onChange={(e) => setIng({ ...ing, audience: e.target.value })}><option value="client">client</option><option value="admin">admin</option></select></div>
          <div className="field"><label><input type="checkbox" checked={ing.reset} onChange={(e) => setIng({ ...ing, reset: e.target.checked })} /> Clear first</label></div>
        </div>
        <button className="btn" disabled={busy} onClick={() => run(() => api.ragIngest(ing), (r) => { setMsg(r.detail || "Ingested."); stats.reload(); })}>Ingest</button>{" "}
        <button className="btn secondary" disabled={busy} onClick={() => { if (window.confirm("Clear the entire vector store?")) run(() => api.ragReset(), (r) => { setMsg(r.detail || "Cleared."); stats.reload(); }); }}>Reset store</button>
        <ErrorNote error={error} />
        {msg && <div className="callout good">{msg}</div>}
      </div></div>

      <h2 style={{ marginTop: 32 }}>Last LLM call (B.5 / B.6 / B.7 evidence)</h2>
      <div className="RoboAdvisor"><div className="RoboAdvisor-body">
        <button className="btn secondary" onClick={payload.reload}>Refresh</button>
        <ErrorNote error={payload.error} />
        {payload.data && (
          <>
            <p style={{ marginTop: 12 }}>
              Status <strong>{payload.data.status}</strong> · attempt {payload.data.attempt} · {payload.data.totalTokens} / {payload.data.contextLimit} tokens · {payload.data.latencyMs} ms · {fmtTime(payload.data.createdAt)} · correlation <code>{payload.data.correlationId}</code>
            </p>
            <h3 style={{ margin: "12px 0 6px" }}>Tokens per block</h3>
            <pre className="pre">{JSON.stringify(payload.data.blockTokens, null, 2)}</pre>
            <details><summary style={{ cursor: "pointer" }}>Assembled payload sent to the model</summary><pre className="pre">{JSON.stringify(payload.data.payload, null, 2)}</pre></details>
            <details><summary style={{ cursor: "pointer" }}>Model response</summary><pre className="pre">{payload.data.responseText}</pre></details>
          </>
        )}
      </div></div>
    </section>
  );
}
