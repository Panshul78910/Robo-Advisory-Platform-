// End-to-end suite for Steps 7-11: risk profiling, suitability gating,
// session date + invest + holdings, rebalance, and revert.
//
// Same prerequisites as smoke.mjs: backend on :8000, this app on :3000.
// Assumes a freshly seeded database (no prior assessments or holdings).

import { chromium } from "playwright";

const APP = "http://localhost:3000";
const API = "http://localhost:8000";
const results = [];
function check(name, pass, detail = "") {
  results.push({ name, pass, detail });
  console.log(`${pass ? "PASS" : "FAIL"}  ${name}${detail ? ` — ${detail}` : ""}`);
}

const browser = await chromium.launch();
const ctx = await browser.newContext();
const page = await ctx.newPage();
const pageErrors = [];
page.on("pageerror", (e) => pageErrors.push(e.message));

const T = (ms = 700) => page.waitForTimeout(ms);

async function login(email) {
  await page.goto(`${APP}/login`, { waitUntil: "networkidle" });
  await page.evaluate(() => sessionStorage.clear());
  await page.goto(`${APP}/login`, { waitUntil: "networkidle" });
  await page.fill("#email", email);
  await page.fill("#password", "password123");
  await page.click('button[type="submit"]');
  await T(1100);
}

async function tab(name) {
  await page.click(`button.tab-btn:has-text("${name}")`);
  await T(900);
}

// Drive the profiling conversation in the real UI until it completes or the
// backend stops asking. Returns the rendered category, or null.
async function runProfiling(maxTurns = 12) {
  await tab("Risk profiling");
  await T(1200);
  for (let i = 0; i < maxTurns; i++) {
    if (await page.locator('[data-testid="risk-outcome"]').count()) break;
    const input = page.locator("#profiling-input");
    if (!(await input.count())) break;
    await input.fill("Yes, that is right.");
    await page.click('.chat-input-row button[type="submit"]');
    await T(1000);
  }
  const outcome = page.locator('[data-testid="risk-category"]');
  return (await outcome.count()) ? (await outcome.textContent()).trim() : null;
}

// ============================================================ STEP 7
console.log("\n=== STEP 7: risk profiling ===");

await login("c001@roboadvisor.local");
const c001a = await runProfiling();
check("c001 reaches a category", c001a !== null, c001a);
check("c001 is low/conservative", /conserv|preservation/i.test(c001a || ""), c001a);
const c001Explain = await page.locator('[data-testid="risk-explanation"]').textContent();
check("c001 explanation shown prominently", (c001Explain || "").length > 40,
  (c001Explain || "").slice(0, 80));
check("explanation names factors, not weights",
  /factors that mattered/i.test(c001Explain || "") && !/\d+\s*%\s*weight|weight of \d/i.test(c001Explain || ""));

await login("c003@roboadvisor.local");
const c003 = await runProfiling();
check("c003 reaches a category", c003 !== null, c003);
check("c003 is high/aggressive", /aggress/i.test(c003 || ""), c003);

// Determinism: same client, same answers, same category
await login("c001@roboadvisor.local");
const c001b = await runProfiling();
check("c001 repeat gives the identical category", c001a === c001b, `${c001a} vs ${c001b}`);

// c004 is deliberately inconsistent -> clarifying question, no category
await login("c004@roboadvisor.local");
const c004 = await runProfiling(6);
check("c004 gets NO category", c004 === null, c004 ?? "(none, as expected)");
const c004Body = await page.textContent("body");
check("c004 is asked a clarifying question",
  /answers conflict|pull in opposite directions|which matters more/i.test(c004Body));

// Formula request must be declined, qualitatively
await login("c001@roboadvisor.local");
await tab("Ask the advisor");
await page.fill("#advisor-input", "What is the exact formula and the numeric weights used to compute my risk score?");
await page.click('[data-testid="advisor-chat"] .chat-input-row button[type="submit"]');
await T(1600);
const advisorAnswer = (await page.locator('[data-testid="advisor-answer"]').last().textContent()) || "";
const advisorIntent = (await page.locator('[data-testid="advisor-intent"]').last().textContent()) || "";
check("advisor declines to reveal the model", /can't share|cannot share|confidential/i.test(advisorAnswer),
  advisorAnswer.slice(0, 90));
check("advisor still answers qualitatively", /factors|shaped your result|horizon|tolerance/i.test(advisorAnswer));
check("no numeric weights leak", !/weight[s]?\s*[:=]\s*\d|\d+\s*points per/i.test(advisorAnswer));
check("classified as formula_request", /formula_request/.test(advisorIntent), advisorIntent.trim());

// ============================================================ STEP 8
console.log("\n=== STEP 8: suitability & gating ===");

// c005 has not passed CKA in the seed
await login("c005@roboadvisor.local");
await runProfiling();
await tab("Portfolio");
await T(1200);
const eligBody = await page.textContent("body");
for (const label of ["Risk category", "Highest permitted", "Satellite cap", "Knowledge check"]) {
  check(`eligibility shows ${label}`, eligBody.includes(label));
}
const ckaState = (await page.locator('[data-testid="elig-cka"]').textContent()) || "";
check("c005 has not passed CKA", /not passed/i.test(ckaState), ckaState.trim());
check("eligible portfolios listed",
  /P\d/.test((await page.locator('[data-testid="elig-portfolios"]').textContent()) || ""));

// firedRules expandable list
await page.click('[data-testid="rules-toggle"]');
await T(500);
const rulesTable = await page.locator('[data-testid="fired-rules"]').count();
check("fired rules expand into a list", rulesTable === 1);
const rulesText = rulesTable ? await page.locator('[data-testid="fired-rules"]').textContent() : "";
check("fired rules show code/kind/cap/explanation",
  /category_cap/.test(rulesText) && /permits portfolios up to/i.test(rulesText));

// Gating: invest above the cap must be refused in plain language
const maxP = ((await page.locator('[data-testid="elig-max"]').textContent()) || "").trim();
await page.fill("#session-date", "2026-02-02");
await page.click('[data-testid="session-picker"] button[type="submit"]');
await T(1000);
await page.selectOption("#portfolio-code", "P8");
await page.click('[data-testid="invest-button"]');
await T(1400);
const refusal = (await page.locator('[data-testid="invest-error"]').textContent()) || "";
check("P8 refused for a capped client", /above the highest portfolio|not.*permitted|outside/i.test(refusal),
  refusal.slice(0, 100));
check("refusal is plain language, not a status code", !/^\s*(403|422|500)\s*$/.test(refusal.trim()));
check("refusal names the cap", refusal.includes(maxP), `max=${maxP}`);

// CKA flow
const ckaBtn = await page.locator('[data-testid="cka-button"]').count();
check("CKA button offered when not passed", ckaBtn === 1);
if (ckaBtn) {
  await page.click('[data-testid="cka-button"]');
  await T(1500);
  const after = (await page.locator('[data-testid="elig-cka"]').textContent()) || "";
  check("CKA passes and eligibility refreshes", /passed/i.test(after) && !/not passed/i.test(after),
    after.trim());
}

// The refusal must reach the admin escalation queue
await login("admin@roboadvisor.local");
await T(1200);
await page.click('button.tab-btn:has-text("Escalation & review queue")');
await T(1400);
const queue = await page.textContent("body");
check("refusal appears in admin escalations", /suitability_refusal/i.test(queue),
  queue.match(/suitability_refusal/i)?.[0] ?? "not found");
check("escalation names the client", /C005/.test(queue));

// ============================================================ STEP 9
console.log("\n=== STEP 9: session date, invest, holdings ===");

await login("c002@roboadvisor.local");
await runProfiling();
await tab("Portfolio");
await T(1000);

// Before a date is set, holdings are unavailable by design
check("session date picker is present", (await page.locator('[data-testid="session-picker"]').count()) === 1);

await page.fill("#session-date", "2026-02-02");
await page.click('[data-testid="session-picker"] button[type="submit"]');
await T(1400);
check("session date active", /2026-02-02/.test(
  (await page.locator('[data-testid="active-session-date"]').textContent()) || ""));

await page.selectOption("#portfolio-code", "P5");
await page.click('[data-testid="invest-button"]');
await T(2000);
const investErr = await page.locator('[data-testid="invest-error"]').count();
check("invest into an eligible portfolio succeeds", investErr === 0,
  investErr ? await page.locator('[data-testid="invest-error"]').textContent() : "");

const totalAt02 = (await page.locator('[data-testid="total-value"]').textContent()) || "";
check("holdings show a total value", /\$[\d,]+\.\d\d/.test(totalAt02), totalAt02.trim());
check("holdings show byAssetClass", (await page.locator('[data-testid="by-asset-class"]').count()) === 1);
check("holdings show byInstrument", (await page.locator('[data-testid="by-instrument"]').count()) === 1);
const instrumentText = await page.locator('[data-testid="by-instrument"]').textContent();
check("instrument rows carry target vs actual", /%/.test(instrumentText));

// Date-forward: values and drift must move
await page.fill("#session-date", "2026-05-01");
await page.click('[data-testid="session-picker"] button[type="submit"]');
await T(1800);
const totalAt05 = (await page.locator('[data-testid="total-value"]').textContent()) || "";
check("moving the session date forward changes valuation", totalAt02 !== totalAt05,
  `${totalAt02.trim()} -> ${totalAt05.trim()}`);

// ============================================================ STEP 10
console.log("\n=== STEP 10: rebalance ===");

await page.click('[data-testid="preview-button"]');
await T(1800);
check("preview renders", (await page.locator('[data-testid="rebalance-preview"]').count()) === 1);
const explanation = (await page.locator('[data-testid="rebalance-explanation"]').textContent()) || "";
check("preview shows the backend explanation", /drifted from its targets/i.test(explanation),
  explanation.slice(0, 90));
check("preview lists trades", (await page.locator('[data-testid="preview-trades"]').count()) === 1);
check("preview lists drift", (await page.locator('[data-testid="preview-drift"]').count()) === 1);
// Capture the actual proposed trades, not just their count, so the
// "different date -> different trades" check below can really fail.
const firstPreviewTrades = await page.locator('[data-testid="preview-trades"]').textContent();
const firstPreviewDrift = await page.locator('[data-testid="preview-drift"]').textContent();

// capture pre-rebalance holdings for the Step 11 comparison
const beforeRebalance = await page.locator('[data-testid="by-asset-class"]').textContent();

await page.click('[data-testid="confirm-rebalance"]');
await T(2400);
check("rebalance executes", (await page.locator('[data-testid="rebalance-ok"]').count()) === 1,
  (await page.locator('[data-testid="rebalance-ok"]').textContent().catch(() => "")) || "");

const afterRebalance = await page.locator('[data-testid="by-asset-class"]').textContent();
check("holdings changed after rebalance", beforeRebalance !== afterRebalance);
// weights should have snapped back onto target
const weightsOnTarget = await page.evaluate(() => {
  const rows = [...document.querySelectorAll('[data-testid="by-asset-class"] tbody tr')];
  return rows.every((r) => {
    const c = r.querySelectorAll("td");
    return c[4].textContent.trim() === c[5].textContent.trim();
  });
});
check("actual weights moved back onto target", weightsOnTarget);

// Different date -> different proposed trades
await page.fill("#session-date", "2026-06-01");
await page.click('[data-testid="session-picker"] button[type="submit"]');
await T(1600);
await page.click('[data-testid="preview-button"]');
await T(1800);
const secondDrift = (await page.locator('[data-testid="preview-drift"]').textContent()) || "";
const secondTrades = (await page.locator('[data-testid="preview-trades"]').textContent()) || "";
check("a later session date proposes DIFFERENT trades",
  secondTrades.length > 0 && secondTrades !== firstPreviewTrades,
  secondTrades === firstPreviewTrades ? "identical trade tables" : "trade tables differ");
check("a later session date shows DIFFERENT drift",
  secondDrift.length > 0 && secondDrift !== firstPreviewDrift,
  secondDrift === firstPreviewDrift ? "identical drift tables" : "drift tables differ");

// ============================================================ STEP 11
console.log("\n=== STEP 11: revert ===");

// Go back to the rebalance date to revert it
await page.fill("#session-date", "2026-05-01");
await page.click('[data-testid="session-picker"] button[type="submit"]');
await T(1600);
await page.click('[data-testid="revert-button"]');
await T(2400);
check("revert executes", (await page.locator('[data-testid="rebalance-ok"]').count()) === 1,
  (await page.locator('[data-testid="rebalance-ok"]').textContent().catch(() => "")) || "");

const afterRevert = await page.locator('[data-testid="by-asset-class"]').textContent();
check("holdings exactly match the pre-rebalance state", afterRevert === beforeRebalance);

await tab("Activity");
await T(1200);
check("activity lists the invest", (await page.locator('[data-testid="event-invest"]').count()) >= 1);
check("rebalance and revert are SEPARATE entries",
  (await page.locator('[data-testid="event-rebalance"]').count()) >= 1 &&
  (await page.locator('[data-testid="event-revert"]').count()) >= 1);
const activity = await page.locator('[data-testid="activity-log"]').textContent();
check("revert links back to the event it reversed", /…/.test(activity));

// ============================================================ idempotency
console.log("\n=== idempotency keys ===");
const keys = await page.evaluate(async () => {
  const auth = JSON.parse(sessionStorage.getItem("roboadvisor.auth") || "{}");
  const seen = [];
  for (let i = 0; i < 2; i++) {
    const r = await fetch("http://localhost:8000/api/clients/me/invest", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${auth.accessToken}`,
        "Content-Type": "application/json",
        "Idempotency-Key": crypto.randomUUID(),
      },
      body: JSON.stringify({ portfolioCode: "P5" }),
    });
    seen.push(r.status);
  }
  const noKey = await fetch("http://localhost:8000/api/clients/me/invest", {
    method: "POST",
    headers: { Authorization: `Bearer ${auth.accessToken}`, "Content-Type": "application/json" },
    body: JSON.stringify({ portfolioCode: "P5" }),
  });
  return { seen, noKey: noKey.status };
});
check("invest without an Idempotency-Key is rejected", keys.noKey === 422, `HTTP ${keys.noKey}`);

console.log("\n=== page errors ===");
console.log(pageErrors.length ? pageErrors.join("\n") : "(none)");

const failed = results.filter((r) => !r.pass);
console.log(`\n=== ${results.length - failed.length}/${results.length} passed ===`);
if (failed.length) {
  console.log("FAILURES:");
  failed.forEach((f) => console.log(` - ${f.name} :: ${f.detail}`));
}
await browser.close();
process.exit(failed.length ? 1 : 0);
