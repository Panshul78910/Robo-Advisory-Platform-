import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useLoad } from "../hooks";
import { fmtTime, human } from "../lib/format";
import { ErrorNote, Loading } from "../components/Notice";

const OPTIONS = {
  investmentObjective: ["preservation", "income", "balanced", "growth", "aggressive_growth"],
  lossTolerance: ["zero", "low", "medium", "high"],
  liquidityNeeds: ["low", "medium", "high"],
  incomeStability: ["stable", "variable", "unstable"],
  investmentExperience: ["none", "limited", "moderate", "extensive"],
};

const FIELDS = [
  { key: "fullName", label: "Full name", type: "text" },
  { key: "dateOfBirth", label: "Date of birth", type: "date" },
  { key: "email", label: "Email", type: "email" },
  { key: "monthlySalary", label: "Monthly salary ($)", type: "number", min: 0, step: "0.01" },
  { key: "monthlyCommitments", label: "Monthly financial commitments ($)", type: "number", min: 0, step: "0.01" },
  { key: "dependants", label: "Number of dependants", type: "number", min: 0, max: 20, step: "1" },
  { key: "investmentObjective", label: "Investment objective", type: "select" },
  { key: "investmentHorizonYrs", label: "Investment horizon (years)", type: "number", min: 0, max: 60, step: "1" },
  { key: "lossTolerance", label: "Loss tolerance", type: "select" },
  { key: "liquidityNeeds", label: "Liquidity needs", type: "select" },
  { key: "incomeStability", label: "Income stability", type: "select" },
  { key: "investmentExperience", label: "Investment experience", type: "select" },
];
const NUMERIC = new Set(["monthlySalary", "monthlyCommitments", "dependants", "investmentHorizonYrs"]);
const MAX_UPLOAD = 10 * 1024 * 1024;
const ALLOWED = ["application/pdf", "image/png", "image/jpeg"];

export default function ProfilePage() {
  const profile = useLoad(() => api.getProfile(), []);
  const docs = useLoad(() => api.listDocuments(), []);
  const [form, setForm] = useState({});
  const [saved, setSaved] = useState(null); // latest server copy (for missing/inconsistent notes)
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [ok, setOk] = useState("");
  const fileRef = useRef(null);
  const [upMsg, setUpMsg] = useState("");
  const [upErr, setUpErr] = useState(null);
  const [uploading, setUploading] = useState(false);

  useEffect(() => {
    if (!profile.data) return;
    const f = {};
    FIELDS.forEach(({ key }) => (f[key] = profile.data[key] ?? ""));
    setForm(f);
    setSaved(profile.data);
  }, [profile.data]);

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  async function save(e) {
    e.preventDefault();
    setError(null);
    setOk("");
    const body = {};
    FIELDS.forEach(({ key }) => {
      const v = form[key];
      if (v === "" || v === null || v === undefined) return;
      body[key] = NUMERIC.has(key) ? Number(v) : v;
    });
    setSaving(true);
    try {
      const res = await api.updateProfile(body);
      setSaved(res);
      setOk("Profile saved.");
    } catch (err) {
      setError(err);
    } finally {
      setSaving(false);
    }
  }

  async function upload(e) {
    e.preventDefault();
    const file = fileRef.current?.files?.[0];
    setUpMsg("");
    setUpErr(null);
    if (!file) return setUpErr({ message: "Choose a file first." });
    if (!ALLOWED.includes(file.type)) return setUpErr({ message: "Only PDF, PNG or JPEG files are accepted." });
    if (file.size > MAX_UPLOAD) return setUpErr({ message: "That file is larger than the 10 MB limit." });
    setUploading(true);
    try {
      const stored = await api.uploadDocument(file);
      const docId = stored?.data?.documentId;
      const removed = stored?.data?.instructionsRemoved;
      setUpMsg(
        `Stored “${file.name}”${docId ? ` (document ID ${docId})` : ""}. ` +
          (file.type === "application/pdf"
            ? `Financial lines can be used as data. Instructions inside the PDF are ignored${removed ? ` (${removed} section${removed === 1 ? "" : "s"} removed)` : ""}.`
            : "Images are stored only and are not read.")
      );
      fileRef.current.value = "";
      docs.reload();
    } catch (err) {
      setUpErr(err);
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="main">
      <div className="page-header">
        <h1>My profile</h1>
        <p>These details feed your risk conversation, so the assistant won't need to ask for them again.</p>
      </div>

      <section className="RoboAdvisor">
        <div className="RoboAdvisor-head"><h2>Client profile</h2></div>
        <div className="RoboAdvisor-body">
          {profile.loading && <Loading what="Loading profile" />}
          {profile.error && <ErrorNote error={profile.error} onRetry={profile.reload} />}
          {profile.data && (
            <form onSubmit={save}>
              <div className="form-grid">
                {FIELDS.map((f) => (
                  <div className="field" key={f.key}>
                    <label htmlFor={f.key}>{f.label}</label>
                    {f.type === "select" ? (
                      <select id={f.key} value={form[f.key] ?? ""} onChange={(e) => set(f.key, e.target.value)}>
                        <option value="">Select…</option>
                        {OPTIONS[f.key].map((o) => <option key={o} value={o}>{human(o)}</option>)}
                      </select>
                    ) : (
                      <input
                        id={f.key} type={f.type} value={form[f.key] ?? ""} min={f.min} max={f.max} step={f.step}
                        onChange={(e) => set(f.key, e.target.value)}
                      />
                    )}
                  </div>
                ))}
              </div>

              <ErrorNote error={error} />
              {ok && (
                <div className="callout good">
                  {ok} Stored against client <strong>{saved?.clientCode || profile.data.clientCode}</strong>
                  {saved?.monthlySalary != null && <> · monthly salary ${Number(saved.monthlySalary).toLocaleString()}</>}.
                </div>
              )}
              {saved && (
                <div className="callout" style={{ marginTop: 12 }}>
                  <strong>Stored record in the database</strong>
                  <div className="hint" style={{ marginBottom: 8 }}>
                    client {saved.clientCode} · {saved.fullName} · {saved.email || "no email"}
                  </div>
                  <table className="RoboAdvisor-table">
                    <tbody>
                      <tr><td>Client code</td><td><code>{saved.clientCode}</code></td></tr>
                      {FIELDS.map((f) => (
                        <tr key={f.key}>
                          <td>{f.label}</td>
                          <td>{saved[f.key] == null || saved[f.key] === "" ? "—" : String(saved[f.key])}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {saved?.missingFields?.length > 0 && (
                <div className="callout">
                  Still to fill in: {saved.missingFields.map(human).join(", ")}. The risk conversation can collect these for you.
                </div>
              )}
              {saved?.inconsistencies?.length > 0 && (
                <div className="callout warn">
                  <strong>Some answers pull in opposite directions</strong>
                  {saved.inconsistencies.map((i) => <div key={i.code} style={{ marginTop: 4 }}>{i.question}</div>)}
                </div>
              )}
              <button className="btn" type="submit" disabled={saving}>{saving ? "Saving…" : "Save profile"}</button>
              <span className="hint" style={{ marginLeft: 12 }}>Client ID {profile.data.clientCode}</span>
            </form>
          )}
        </div>
      </section>

      <section className="RoboAdvisor">
        <div className="RoboAdvisor-head"><h2>Supporting documents</h2></div>
        <div className="RoboAdvisor-body">
          <p style={{ color: "var(--ink-soft)" }}>Upload a PDF statement, or a PNG or JPEG (up to 10 MB). A PDF's financial lines can be used as data. Instructions inside the file are ignored. Images are stored and not read.</p>
          <form onSubmit={upload} className="chat-input-row" style={{ marginTop: 0 }}>
            <input ref={fileRef} type="file" accept=".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg" aria-label="Document" />
            <button className="btn" type="submit" disabled={uploading}>{uploading ? "Uploading…" : "Upload"}</button>
          </form>
          <ErrorNote error={upErr} />
          {upMsg && <div className="callout good">{upMsg}</div>}
          {docs.data && (
            <table className="RoboAdvisor-table" style={{ marginTop: 16 }}>
              <thead><tr><th>Document ID</th><th>File</th><th>Type</th><th className="num">Size</th><th>Uploaded</th></tr></thead>
              <tbody>
                {docs.data.length === 0 && <tr><td colSpan={5} className="hint">No documents uploaded yet.</td></tr>}
                {docs.data.map((d) => (
                  <tr key={d.id}>
                    <td><code>{d.id}</code></td>
                    <td>{d.filename}</td><td>{d.mimeType}</td>
                    <td className="num">{Math.max(1, Math.round(d.sizeBytes / 1024))} KB</td><td>{fmtTime(d.uploadedAt)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>
    </div>
  );
}
