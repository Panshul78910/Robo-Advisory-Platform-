// Mock stand-in for the real LLM + RAG pipeline. Swap `answerQuestion()`
// for a call to your backend (which does retrieval + LLM orchestration) —
// keep the return shape stable, since that's what the UI renders.
//
// Return shape:
// {
//   text: string,
//   confidence: number | null,   // null => out of scope / abstained
//   sources: PolicyChunk[],
//   abstained: boolean,
//   refusedFormula: boolean,
// }

import { POLICY_CORPUS } from "./mockData";

const OUT_OF_SCOPE_HINTS = ["itinerary", "holiday", "recipe", "weather", "joke", "trip to", "restaurant"];
const FORMULA_HINTS = ["formula", "exact calculation", "source code", "algorithm you use", "how do you compute the score"];

function findSources(keywords) {
  return POLICY_CORPUS.filter((chunk) =>
    keywords.some((k) => chunk.text.toLowerCase().includes(k.toLowerCase()))
  ).slice(0, 2);
}

export function answerQuestion(question, riskCategory) {
  const q = question.toLowerCase().trim();

  if (OUT_OF_SCOPE_HINTS.some((h) => q.includes(h))) {
    return {
      text: "That's outside what I can help with here — I'm scoped to your investment profile, portfolio and risk questions. Happy to help with anything on those topics!",
      confidence: null,
      sources: [],
      abstained: true,
      refusedFormula: false,
    };
  }

  if (FORMULA_HINTS.some((h) => q.includes(h))) {
    return {
      text: `I can't share the exact scoring formula — it's confidential business logic. What I can tell you qualitatively is that your ${riskCategory} category reflects a combination of your stated horizon, loss tolerance, income stability and liquidity needs, weighted according to our suitability policy.`,
      confidence: 0.95,
      sources: findSources([riskCategory]),
      abstained: false,
      refusedFormula: true,
    };
  }

  if (q.includes("why") && (q.includes("risk") || q.includes("category") || q.includes("portfolio"))) {
    const sources = findSources([riskCategory, "loss", "CKA"]);
    return {
      text: `You were assigned ${riskCategory} based on the profile and preferences you shared (horizon, loss tolerance, income stability, and liquidity needs), checked against our suitability policy. This determines which model portfolios you're eligible for.`,
      confidence: 0.9,
      sources: sources.length ? sources : findSources(["risk category"]),
      abstained: false,
      refusedFormula: false,
    };
  }

  if (q.includes("rebalance")) {
    return {
      text: "Rebalancing revalues your holdings at current prices, checks how far each asset class has drifted from its target weight, and trades to bring the portfolio back in line with your target allocation.",
      confidence: 0.88,
      sources: findSources(["rebalance", "drift"]),
      abstained: false,
      refusedFormula: false,
    };
  }

  const conf = Number((0.45 + Math.random() * 0.2).toFixed(2));
  return {
    text: "I can give a general answer, but I'm not fully confident this covers your specific situation — you may want to confirm with an advisor. In general, your portfolio and risk settings are reviewed against our suitability policy whenever you take an action.",
    confidence: conf,
    sources: [],
    abstained: false,
    refusedFormula: false,
  };
}

export function confidenceBadge(confidence) {
  if (confidence === null || confidence === undefined) {
    return { label: "Out of scope", tone: "neutral" };
  }
  if (confidence >= 0.8) return { label: `High confidence (${Math.round(confidence * 100)}%)`, tone: "good" };
  if (confidence >= 0.6) return { label: `Medium confidence (${Math.round(confidence * 100)}%)`, tone: "warn" };
  return { label: `Low confidence (${Math.round(confidence * 100)}%) — please verify`, tone: "bad" };
}
