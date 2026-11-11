# RoboAdvisor — Frontend (React + Vite)

The browser UI for the SC3098 robo-advisory platform. It talks to the FastAPI
backend (`Design-Project-Backend`)

## Run it

You need the backend running first (see its README: `make reset`, then `make up`
→ http://localhost:8000). Then:

```bash
npm install
npm run dev        # http://localhost:5173
```

In dev, Vite proxies `/api` to `http://localhost:8000`, so there is no CORS
setup. To point at a different backend, copy `.env.example` to `.env` and set
`VITE_PROXY_TARGET` (dev) or `VITE_API_URL` (production build).

Seeded logins (password `password123`): `admin@roboadvisor.local`,
`c001@…` to `c005@…@roboadvisor.local` (the login page lists what each one is for).

## Run without a backend (mock mode)

```bash
npm install
npm run dev:mock        # http://localhost:5173, no backend needed
```

`dev:mock` sets `VITE_MOCK=true` (from `.env.mock`). `src/api.js` then sends every
request to `src/mock/mockBackend.js`, an in-memory copy of the backend's endpoints and
response shapes. Normal `npm run dev` still talks to the real FastAPI backend.

- Logins: the same seeded emails, password `password123`. Session dates: 2026-01-02 to 2026-06-30.
- State resets on page refresh.
- Starting state: C002 is assessed (Balanced) and already invested in P4 from 5 Jan, so pick a later
  session date to see drift and try rebalance and revert. C003 is assessed with CKA passed (P8), nothing
  invested. C001, C004 and C005 need the risk conversation: C001 is forced to P1, C004 gets clarifying
  questions (answer "protect my capital", then "5 years"), C005 is capped at P5 until you pass the knowledge check.
- Chat triggers to try: "Why was I assigned this risk category?", "What is the formula?",
  "plan me a trip to Japan" (out of scope), "What should I do?" (ambiguity chips),
  "switch me to P8" (routed to officer review), any off-topic question (low confidence, abstain).
- Officer (`admin@roboadvisor.local`): a pending escalation is seeded; approve/edit/reject it, then check the
  audit log. "Reset store" on the System tab makes chat answers ungrounded until you ingest again.
- The scoring in the mock is a made-up stand-in, not Appendix B. Don't treat its numbers as correct.

## How it fits together

```
browser ──/api──▶ Vite proxy ──▶ FastAPI backend ──▶ Postgres + pgvector
                                        └──────────▶ LLM (stub or HPC)
```

Everything that touches the network is in **`src/api.js`**. Pages never call
`fetch` themselves. State lives in three contexts: `AuthContext` (who is signed
in), `SessionContext` (the simulated session date), `AppDataContext` (chat and
profiling conversations).

## Screens → checklist

| Screen | Route | Checklist |
|---|---|---|
| Login + session date picker | `/login` | A.1 (login), A.5(1) |
| Dashboard: holdings, performance, risk category, disclosure, grounded chat | `/dashboard` | A.4, C.1–C.4 |
| Profile form + document upload | `/profile` | A.1 (ii)(iii)(iv) |
| Risk conversation (one chat, clarifying questions) | `/profiling` | A.2 |
| Knowledge check, eligibility, invest, refusal, rebalance, revert, activity log | `/portfolio` | A.3, A.5, A.6, A.7 |
| Officer console: all clients, review queue, audit/provenance, vector store, last LLM payload | `/officer` | A.1(vi), A.3(3), C.5, C.6, B.4, B.5 |

Not covered by the UI because the backend has no endpoint for it: **creating a
new client account** (A.1 sign-off item 1). See `docs/PART_D_AND_BACKEND_HANDOFF.md`.

## Frontend safety measures (Part D, what the browser can do)

- Model output is rendered as inert text through `SafeText`, never as HTML.
- The access token is kept in memory only (not localStorage/sessionStorage) and
  cleared on logout, on any 401, and after 15 minutes idle.
- Error messages shown to users are the backend's public message or a fixed
  friendly string, raw response bodies are never displayed.
- The disclosure block cannot be hidden. Chat input is length-capped and
  throttled; transactional requests carry an `Idempotency-Key`; requests time out.
- Role guards on routes are UX only — real access control is in the backend.

Real defences against hallucination, jailbreaks, prompt leakage and data leakage
must live in the backend guardrails
