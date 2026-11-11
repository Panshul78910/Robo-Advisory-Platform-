import { human } from "../lib/format";

// C.4: every AI output carries a visible confidence / uncertainty indicator.
export function confidenceBadge({ confidence, label, abstained, intent }) {
  if (confidence === null || confidence === undefined) {
    if (String(intent || "").includes("scope")) return { text: "Out of scope", tone: "neutral" };
    return { text: abstained ? "Not sure — I abstained from answering" : "No confidence score", tone: "neutral" };
  }
  const pct = Math.round(Number(confidence) * 100);
  const band = label || (confidence >= 0.8 ? "high" : confidence >= 0.6 ? "medium" : "low");
  if (band === "high") return { text: `High confidence (${pct}%)`, tone: "good" };
  if (band === "medium") return { text: `Medium confidence (${pct}%)`, tone: "warn" };
  return { text: `Low confidence (${pct}%) — please verify`, tone: "bad" };
}

export default function ConfidenceBadge(props) {
  const { text, tone } = confidenceBadge(props);
  return <span className={`pill ${tone}`}>{text}</span>;
}

// Status pill for decision states. Backend statuses are free text, so match loosely.
export function StatusPill({ status }) {
  const s = String(status || "").toLowerCase();
  let tone = "neutral";
  if (/(pending|review|escalat)/.test(s)) tone = "warn";
  else if (/(refus|reject|overrid|fail|error)/.test(s)) tone = "bad";
  else if (/(complet|approv|execut|success|resolved)/.test(s)) tone = "good";
  return <span className={`pill ${tone}`}>{human(status)}</span>;
}
