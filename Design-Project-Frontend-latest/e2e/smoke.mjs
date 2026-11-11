// End-to-end smoke test for the real (non-mock) auth, profile and officer views.
//
// Playwright is deliberately NOT a dependency of this project -- install it
// wherever you like and point node at this file:
//
//   npm init -y && npm install playwright && npx playwright install chromium
//   node /path/to/frontend/e2e/smoke.mjs
//
// Requires: backend on :8000 (cd backend && make up) and this app on :3000
// (npm run dev -- --port 3000). Port 3000 matters -- the backend's CORS
// allowlist is localhost:5173 and localhost:3000 only.
//
// Re-runnable: the profile test resets monthlySalary before changing it.

import { chromium } from "playwright";
import fs from "node:fs";

const APP = "http://localhost:3000";
const TMP = new URL(".", import.meta.url).pathname; // fixtures live beside this file
const results = [];
function check(name, pass, detail = "") {
  results.push({ name, pass, detail });
  console.log(`${pass ? "PASS" : "FAIL"}  ${name}${detail ? ` — ${detail}` : ""}`);
}

const browser = await chromium.launch();
const ctx = await browser.newContext();
const page = await ctx.newPage();

const consoleErrors = [];
page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
page.on("pageerror", (e) => consoleErrors.push(`pageerror: ${e.message}`));

async function login(email) {
  await page.goto(`${APP}/login`, { waitUntil: "networkidle" });
  await page.fill("#email", email);
  await page.fill("#password", "password123");
  await page.click('button[type="submit"]');
  await page.waitForTimeout(1200);
}

// ---------------------------------------------------------------- TASK 1
console.log("\n=== TASK 1: real auth ===");
await login("c001@roboadvisor.local");

check("c001 lands on /dashboard", new URL(page.url()).pathname === "/dashboard", page.url());

const stored = await page.evaluate(() => JSON.parse(sessionStorage.getItem("roboadvisor.auth") || "null"));
const uuidRe = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
check("context holds real clientId (uuid)", uuidRe.test(stored?.clientId || ""), stored?.clientId);
check("context role === 'client'", stored?.role === "client", stored?.role);
check("accessToken is a JWT", (stored?.accessToken || "").split(".").length === 3,
  `len=${stored?.accessToken?.length}`);
check("displayName from backend", stored?.displayName === "Aaron Tan", stored?.displayName);

// No signup screen anywhere
await page.goto(`${APP}/signup`, { waitUntil: "networkidle" });
check("no signup route (redirects away)", !page.url().includes("signup"), page.url());

// Wrong password surfaces the backend's message
await page.goto(`${APP}/login`, { waitUntil: "networkidle" });
await page.evaluate(() => sessionStorage.clear());
await page.goto(`${APP}/login`, { waitUntil: "networkidle" });
await page.fill("#email", "c001@roboadvisor.local");
await page.fill("#password", "wrongpassword");
await page.click('button[type="submit"]');
await page.waitForTimeout(900);
const errText = await page.locator(".error-text").first().textContent().catch(() => "");
check("bad password shows backend error", /incorrect/i.test(errText || ""), (errText || "").trim());

// Login screen copy
const loginBody = await page.textContent("body");
check("login copy has c001 example", loginBody.includes("c001@roboadvisor.local"));
check("login copy has admin example", loginBody.includes("admin@roboadvisor.local"));
check("no 'sanjana' anywhere", !/sanjana/i.test(loginBody));
check("no 'jiaying' anywhere", !/jiaying/i.test(loginBody));

// ProtectedRoute: client hitting the officer route
await login("c001@roboadvisor.local");
await page.goto(`${APP}/officer`, { waitUntil: "networkidle" });
await page.waitForTimeout(600);
const deniedText = await page.textContent("body");
check("client is denied /officer", /Access denied/i.test(deniedText), "ProtectedRoute panel shown");

// Dashboard must greet with the real backend name, with no mock identity left
await login("c001@roboadvisor.local");
await page.waitForTimeout(1200);
const dashBody = await page.textContent("body");
check("dashboard greets real displayName", /Welcome back, Aaron Tan/.test(dashBody));
check("no mock names on dashboard", !/sanjana|jia ?ying|rahul|wei ling/i.test(dashBody));
check("dashboard did not crash", /Holdings/i.test(dashBody));

// Every seeded client can log in and render
for (const [code, name] of [["c001","Aaron Tan"],["c002","Bala Krishnan"],["c003","Chloe Lim"],
                            ["c004","Devi Ramasamy"],["c005","Ethan Wong"]]) {
  await login(`${code}@roboadvisor.local`);
  await page.waitForTimeout(900);
  const body = await page.textContent("body");
  check(`${code} dashboard renders as ${name}`,
    new URL(page.url()).pathname === "/dashboard" && body.includes(name));
}

// ---------------------------------------------------------------- TASK 2
console.log("\n=== TASK 2: profile screen ===");
await login("c001@roboadvisor.local");
await page.goto(`${APP}/profile`, { waitUntil: "networkidle" });
await page.waitForTimeout(1000);

const salaryBefore = await page.inputValue("#monthlySalary");
check("profile loaded from GET", salaryBefore !== "", `monthlySalary=${salaryBefore}`);

for (const id of ["monthlySalary", "monthlyCommitments", "dependants", "investmentObjective",
  "investmentHorizonYrs", "lossTolerance", "liquidityNeeds", "incomeStability", "investmentExperience"]) {
  const present = await page.locator(`#${id}`).count();
  check(`field present: ${id}`, present === 1);
}

// Reset to a known-different value first so this suite is re-runnable: the
// form deliberately disables Save when nothing actually changed.
if (Number(salaryBefore) === 8000) {
  await page.fill("#monthlySalary", "4200");
  await page.click('button[type="submit"]:not([disabled])');
  await page.waitForTimeout(1500);
}
check("baseline salary is not 8000", Number(await page.inputValue("#monthlySalary")) !== 8000,
  await page.inputValue("#monthlySalary"));

// Change salary to 8000 and save
await page.fill("#monthlySalary", "8000");
await page.click('button[type="submit"]:not([disabled])');
await page.waitForTimeout(1500);
const toast = await page.locator(".toast").first().textContent().catch(() => "");
check("save reported confirmed", /Saved and confirmed/i.test(toast || ""), (toast || "").trim());

// Hard reload -> did it stick?
await page.reload({ waitUntil: "networkidle" });
await page.waitForTimeout(1200);
const salaryAfter = await page.inputValue("#monthlySalary");
check("monthlySalary persisted as 8000 after reload", Number(salaryAfter) === 8000, `got ${salaryAfter}`);

// Upload a dummy PDF
const pdfPath = `${TMP}/dummy.pdf`;
await page.setInputFiles("#document", pdfPath);
await page.waitForTimeout(1800);
const upToast = await page.locator(".toast").first().textContent().catch(() => "");
check("upload success state shows filename", /dummy\.pdf/i.test(upToast || ""), (upToast || "").trim());

const tableText = await page.locator("table").last().textContent().catch(() => "");
check("uploaded file appears in document list", /dummy\.pdf/i.test(tableText || ""));

// Reject a disallowed type client-side
fs.writeFileSync(`${TMP}/notes.txt`, "not an allowed type");
await page.setInputFiles("#document", `${TMP}/notes.txt`);
await page.waitForTimeout(800);
const upErr = await page.locator(".error-text").first().textContent().catch(() => "");
check("non-PDF/PNG/JPEG rejected", /Unsupported file type/i.test(upErr || ""), (upErr || "").trim());

// ---------------------------------------------------------------- TASK 3
console.log("\n=== TASK 3: officer view ===");
await login("admin@roboadvisor.local");
check("admin lands on /officer", new URL(page.url()).pathname === "/officer", page.url());
await page.waitForTimeout(1200);

const officerBody = await page.textContent("body");
for (const code of ["C001", "C002", "C003", "C004", "C005"]) {
  check(`client ${code} renders`, officerBody.includes(code));
}
for (const name of ["Aaron Tan", "Bala Krishnan", "Chloe Lim", "Devi Ramasamy", "Ethan Wong"]) {
  check(`name renders: ${name}`, officerBody.includes(name));
}
check("emails render", officerBody.includes("c001@roboadvisor.local"));
const rowCount = await page.locator("table tbody tr").count();
check("exactly 5 client rows", rowCount === 5, `got ${rowCount}`);

// Escalations tab
await page.click('button:has-text("Escalation & review queue")');
await page.waitForTimeout(1200);
const escBody = await page.textContent("body");
// Either state is correct: an empty queue, or real refusals if the advisory
// suite has run against this database and escalated one.
check("escalations tab renders",
  /No pending escalations/i.test(escBody) || /suitability_refusal/i.test(escBody),
  /No pending escalations/i.test(escBody) ? "empty queue" : "live escalations listed");

// The real 403: a client token against /api/admin/clients, from the browser
await login("c001@roboadvisor.local");
const forbidden = await page.evaluate(async () => {
  const auth = JSON.parse(sessionStorage.getItem("roboadvisor.auth") || "{}");
  const r = await fetch("http://localhost:8000/api/admin/clients", {
    headers: { Authorization: `Bearer ${auth.accessToken}` },
  });
  return { status: r.status, body: await r.text() };
});
check("backend returns 403 for client token", forbidden.status === 403,
  `HTTP ${forbidden.status} ${forbidden.body.slice(0, 90)}`);

console.log("\n=== console errors ===");
console.log(consoleErrors.length ? consoleErrors.join("\n") : "(none)");

const failed = results.filter((r) => !r.pass);
console.log(`\n=== ${results.length - failed.length}/${results.length} passed ===`);
if (failed.length) {
  console.log("FAILURES:");
  failed.forEach((f) => console.log(` - ${f.name} :: ${f.detail}`));
}

await browser.close();
process.exit(failed.length ? 1 : 0);
