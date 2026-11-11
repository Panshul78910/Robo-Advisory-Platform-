// In-memory mock data standing in for the real backend (DB + RAG store +
// audit service). Swap these functions for real API calls once your
// backend exists — components consume them through AuthContext/hooks,
// so the UI layer doesn't need to change.

export const RISK_CATEGORIES = [
  "Capital Preservation",
  "Conservative",
  "Moderately Conservative",
  "Balanced",
  "Moderate",
  "Moderately Aggressive",
  "Aggressive",
  "Very Aggressive",
];

export const PORTFOLIOS = {
  "Capital Preservation": { name: "Capital Shield Fund", alloc: { Cash: 60, Bonds: 35, Equities: 5 } },
  Conservative: { name: "Steady Income Portfolio", alloc: { Cash: 40, Bonds: 45, Equities: 15 } },
  "Moderately Conservative": { name: "Balanced Income Plus", alloc: { Cash: 25, Bonds: 45, Equities: 30 } },
  Balanced: { name: "Core Balanced Portfolio", alloc: { Cash: 15, Bonds: 35, Equities: 50 } },
  Moderate: { name: "Growth & Income Blend", alloc: { Cash: 10, Bonds: 25, Equities: 65 } },
  "Moderately Aggressive": { name: "Growth Tilt Portfolio", alloc: { Cash: 5, Bonds: 15, Equities: 80 } },
  Aggressive: { name: "High Growth Equity Portfolio", alloc: { Cash: 5, Bonds: 5, Equities: 90 } },
  "Very Aggressive": { name: "Satellite Growth Max", alloc: { Cash: 0, Bonds: 0, Equities: 100 } },
};

export const DISCLOSURE_CLAUSES = [
  {
    label: "Key risks",
    text: "Investment values can fall as well as rise; you may not get back the amount invested. Model portfolio allocations are indicative and may drift with market movements.",
  },
  {
    label: "Assumptions & limitations",
    text: "This recommendation is generated from the information you supplied and simulated market data; it does not account for assets held outside this platform.",
  },
  {
    label: "No guarantee",
    text: "Past or simulated performance is not indicative of future results. This is not a guarantee of investment outcome and does not constitute independent financial advice.",
  },
];

export const POLICY_CORPUS = [
  {
    docId: "POL-001",
    title: "Company Suitability Policy",
    section: "3.2 Risk Category Definitions",
    page: 4,
    text: "Clients categorised as 'Balanced' are considered to have a medium risk tolerance and a medium-to-long investment horizon (typically 5-10 years), and are suited to portfolios with an approximately even split between growth and defensive assets.",
  },
  {
    docId: "POL-001",
    title: "Company Suitability Policy",
    section: "4.1 Loss Tolerance Overrides",
    page: 6,
    text: "Where a client indicates zero tolerance for capital loss, the assigned portfolio must be capped at 'Conservative' regardless of the computed composite risk score.",
  },
  {
    docId: "POL-001",
    title: "Company Suitability Policy",
    section: "5.3 CKA Gating for Satellite Exposure",
    page: 9,
    text: "Clients who have not completed the Client Knowledge Assessment (CKA) are restricted from satellite ETF exposure and from any portfolio above the 'Moderate' category.",
  },
  {
    docId: "POL-002",
    title: "Rebalancing Methodology Guide",
    section: "2.1 Trigger Conditions",
    page: 2,
    text: "A portfolio is considered drifted when any asset class weight deviates from its target weight by more than 5 percentage points, at which point a rebalance restores target weights by buying underweight assets and trimming overweight ones.",
  },
  {
    docId: "POL-003",
    title: "Client Disclosure Handbook",
    section: "1.4 Mandatory Disclosures",
    page: 1,
    text: "Every investment recommendation shown to a client must be accompanied by the standard disclosure block covering key risks, assumptions/limitations, and a no-guarantee statement, and this disclosure may not be hidden or suppressed at the client's request.",
  },
];

// Names and CKA flags mirror the backend seed (scripts/seed.py), so the demo
// numbers on this page line up with whoever actually logged in. The monetary
// figures below are still illustrative, not from /api/clients/me/holdings.
export const CLIENTS = {
  C001: { name: "Aaron Tan", riskCategory: "Capital Preservation", ckaPassed: false, lossTolerance: "Zero", cash: 12500.0 },
  C002: { name: "Bala Krishnan", riskCategory: "Balanced", ckaPassed: true, lossTolerance: "Medium", cash: 30500.0 },
  C003: { name: "Chloe Lim", riskCategory: "Aggressive", ckaPassed: true, lossTolerance: "High", cash: 4200.0 },
  C004: { name: "Devi Ramasamy", riskCategory: "Conservative", ckaPassed: false, lossTolerance: "Low", cash: 18750.0 },
  C005: { name: "Ethan Wong", riskCategory: "Moderately Aggressive", ckaPassed: false, lossTolerance: "Medium", cash: 9100.0 },
};

// Small seeded PRNG so each reload shows stable, believable mock numbers
// per client instead of true randomness.
function seededRandom(seed) {
  let s = seed % 2147483647;
  if (s <= 0) s += 2147483646;
  return function () {
    s = (s * 16807) % 2147483647;
    return (s - 1) / 2147483646;
  };
}

function hashCode(str) {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    hash = (hash << 5) - hash + str.charCodeAt(i);
    hash |= 0;
  }
  return Math.abs(hash);
}

export function generateHoldings(clientId) {
  const rand = seededRandom(hashCode(clientId) + 1);
  const category = CLIENTS[clientId].riskCategory;
  const targetAlloc = PORTFOLIOS[category].alloc;
  const basePrices = { Cash: 1.0, Bonds: 100.0, Equities: 50.0 };

  const rows = Object.entries(targetAlloc).map(([asset, targetW]) => {
    const drift = (rand() - 0.5) * 12;
    const actualW = Math.max(0, targetW + drift);
    const price = basePrices[asset] * (0.95 + rand() * 0.13);
    const units = ((actualW / 100) * 50000) / price;
    return {
      asset,
      units: Number(units.toFixed(2)),
      price: Number(price.toFixed(2)),
      value: Number((units * price).toFixed(2)),
      targetWeight: targetW,
      actualWeight: actualW,
    };
  });

  const total = rows.reduce((sum, r) => sum + r.value, 0);
  return rows.map((r) => ({
    ...r,
    actualWeight: total > 0 ? Number(((r.value / total) * 100).toFixed(1)) : 0,
  }));
}

export function generatePerformance(clientId, days = 90) {
  const rand = seededRandom(hashCode(clientId) + 2);
  const today = new Date();
  let value = 50000;
  const rows = [];
  for (let i = days; i >= 0; i--) {
    value *= 1 + (rand() - 0.46) * 0.028;
    const date = new Date(today);
    date.setDate(date.getDate() - i);
    rows.push({
      date: date.toISOString().slice(5, 10), // MM-DD for compact axis labels
      value: Number(value.toFixed(2)),
    });
  }
  return rows;
}

export function seedAuditLog() {
  const now = Date.now();
  return [
    {
      decisionId: "D-1001",
      timestamp: now - 6 * 86400000 - 3 * 3600000,
      clientId: "C001",
      clientName: "Aaron Tan",
      decisionType: "Risk Category Assignment",
      input: "Conversational risk-profiling answers (salary $8,000, horizon 7 yrs, medium loss tolerance)",
      output: "Risk category: Balanced -> Core Balanced Portfolio",
      confidence: 0.92,
      modelVersion: "risk-scorer-v3 / gpt-orch-1.4",
      retrievedSources: ["POL-001 §3.2"],
      humanOverride: null,
      status: "Completed",
    },
    {
      decisionId: "D-1002",
      timestamp: now - 2 * 86400000 - 5 * 3600000,
      clientId: "C002",
      clientName: "Bala Krishnan",
      decisionType: "Suitability Gate",
      input: "Client requested 'Aggressive' portfolio despite zero loss tolerance flag",
      output: "Request refused; capped at Conservative per override policy",
      confidence: 0.97,
      modelVersion: "suitability-rules-v2",
      retrievedSources: ["POL-001 §4.1"],
      humanOverride: null,
      status: "Completed",
    },
  ];
}
