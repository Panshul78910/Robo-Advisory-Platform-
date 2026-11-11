export function money(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "—";
  return `$${Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function num(n, dp = 4) {
  if (n === null || n === undefined) return "—";
  return Number(n).toLocaleString(undefined, { maximumFractionDigits: dp });
}

export function fmtTime(ts) {
  if (!ts) return "—";
  if (typeof ts === "object") {
    if (typeof ts.timestamp === "number") return fmtTime(ts.timestamp);
    if (typeof ts.toISOString === "function") return fmtTime(ts.toISOString());
  }
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return String(ts);
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// "moderately_aggressive" -> "Moderately aggressive"
export function human(s) {
  if (!s) return "—";
  const t = String(s).replace(/_/g, " ");
  return t.charAt(0).toUpperCase() + t.slice(1);
}

// One-line preview of model text: drops the **bold** / bullet markers SafeText
// would render, so list previews don't show raw markdown.
export function plainPreview(s) {
  return String(s ?? "")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/^\s*[-*]\s+/gm, "")
    .replace(/^-{3,}\s*$/gm, "")
    .replace(/\s+/g, " ")
    .trim();
}

// Round axis ticks (e.g. 49,800 … 50,200) instead of whatever the data min/max happen to be.
export function niceTicks(values, target = 4) {
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return undefined;
  const rough = (hi - lo || Math.abs(hi) || 1) / target;
  const mag = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= rough);
  const ticks = [];
  for (let t = Math.floor(lo / step) * step; t <= Math.ceil(hi / step) * step + step / 2; t += step) ticks.push(t);
  return ticks;
}

// Names as seeded by the backend (scripts/_fixtures.py). Codes are the source of truth;
// if the instructor's Appendix E renames a portfolio, only this list needs updating.
export const PORTFOLIOS = [
  { code: "P1", name: "Capital Preservation" },
  { code: "P2", name: "Conservative Income" },
  { code: "P3", name: "Moderately Conservative" },
  { code: "P4", name: "Balanced" },
  { code: "P5", name: "Moderately Aggressive" },
  { code: "P6", name: "Growth" },
  { code: "P7", name: "Aggressive Growth" },
  { code: "P8", name: "Maximum Growth" },
];
export const portfolioName = (code) => PORTFOLIOS.find((p) => p.code === code)?.name || code || "—";
