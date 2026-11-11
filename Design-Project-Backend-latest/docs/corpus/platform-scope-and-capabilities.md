# Platform Scope and Capabilities

This page describes what this platform does and what it does not do. It covers
the features available to a signed-in client, the boundaries of the service, and
where the authoritative answer to a policy question lives.

Where this page and Appendix E: Company Suitability Policy differ on a matter of
policy, **Appendix E is correct**. This page describes the platform; Appendix E
governs it. Figures that belong to the policy — portfolio weights, eligibility
caps, satellite limits, the knowledge-check pass mark — are stated in Appendix E
and in the Platform Portfolio Reference, and are deliberately not repeated here.

## What this platform is

An educational robo-advisory simulation. It models the full advisory journey —
profiling, suitability assessment, portfolio recommendation, investing,
rebalancing and review — against a simulated market.

**No real money is involved at any point.** No funds are held, transferred,
received or paid out. No account exists at any bank, broker or custodian. Orders
are not routed to any venue. Nothing on this platform is financial advice, and
nothing here should be acted on as though it were.

---

## What you can do

### Account and profile

- Register an account and sign in.
- View and update your client profile: name, date of birth, email, monthly
  salary, monthly commitments, number of dependants, investment objective,
  investment horizon in years, loss tolerance, liquidity needs, income stability
  and investment experience.
- Your profile is the input to your risk assessment, so changing it may change
  which portfolios you are eligible for.

### Risk profiling

- Complete a guided profiling conversation. The assistant asks about your
  circumstances one topic at a time and records your answers to your profile.
- If your answers conflict with each other — for example a very short investment
  horizon together with an aggressive growth objective — the platform will say
  so and ask you to clarify rather than assign you a category. An incomplete or
  self-contradictory profile produces **no** risk category and **no**
  recommendation until it is resolved.
- Re-run the assessment at any time from your dashboard once your stored profile
  is complete and coherent.
- Every assessment is recorded with a timestamp and the policy version in force
  when it ran.

### Knowledge check

- Take the knowledge check (also called the CKA, or Customer Knowledge
  Assessment) from within the platform.
- Passing it is required before the higher portfolios or any satellite ETF
  exposure. See Appendix E section 4 for the number of questions, the pass mark,
  the topics covered and the retake rules.
- A failed check can be retaken after reviewing the platform's educational
  material.
- The assistant **will not** coach you through the quiz while you are taking it.
  It will explain the concepts before or after, but not supply answers during.

### Eligibility and suitability

- See your assessed risk category, the portfolios you are eligible for, your
  highest eligible portfolio, and your satellite allowance.
- See which policy rules applied to you and why, in plain language.
- Eligibility is decided by a deterministic rules engine reading a written
  policy file — not by the language model. The assistant reports that decision;
  it does not make it, and it cannot be argued out of it.
- A request for a portfolio above your assessment is refused, the reason is
  explained, and the refusal is recorded and raised for a human adviser.

### Investing

- Choose a session date, then invest your available balance into any model
  portfolio you are eligible for.
- You see the resulting order ticket before it is recorded: each instrument, the
  side, the units, the price used and the gross amount.
- Orders are placed against the prices for the session date you selected.

### Rebalancing

- Preview a rebalance: what your holdings have drifted to, what the target
  allocation is, and the trades that would close the gap.
- Execute the rebalance.
- Revert it. This restores the holdings snapshot from immediately before the
  **most recent** rebalance.
- Rebalancing is always something you start. The platform never rebalances your
  portfolio on its own, on a schedule, or in response to market movement.

### Holdings and performance

- View your current holdings by instrument and by asset class, with actual
  weight against target weight, your cash balance and total value.
- View a performance line over the loaded price history, valued at each past
  date against the holdings you then had.

### Documents

- Upload supporting documents to your client record: PDF, PNG or JPEG, up to
  10 MB each.
- View the list of documents you have uploaded.
- Text is read from PDFs only. PNG and JPEG files are stored but not read —
  there is no image text recognition, so a payslip photographed rather than
  downloaded will not have its figures extracted.

### The assistant

- Ask about your own record, your holdings, your risk category and why it was
  assigned, your eligibility, and the firm's policies and guidelines.
- Ask in a language other than English. The assistant recognises several and
  will reply in the language you used; the underlying policy corpus is in
  English.
- Answers that rest on firm policy carry a citation to the passage they came
  from, so you can check them.
- When the assistant is not confident, or no policy passage supports an answer,
  it says so and flags the answer for review rather than presenting a guess.

### Your record of decisions

- Every assessment, recommendation, refusal and answer is written to an audit
  record with a reference number, visible to you.
- Look up any decision by its reference.
- Records cannot be edited or deleted after the fact, by you or by staff. A
  correction is added as a new record alongside the original.

### Human review

- Refusals on suitability grounds, low-confidence answers and escalations are
  queued for a human adviser.
- An item queued for review is marked as such, and no action is taken on it
  until a person has checked it.

---

## What this platform does not do

### Money and accounts

- **No real money.** No deposits, no withdrawals, no transfers in or out, no
  payments. There is nothing to withdraw, because nothing was ever deposited.
- **No cash management.** You cannot add to or draw down your balance.
- **No account closure or data deletion from the interface.** Ask a human
  adviser.
- **One currency, unlabelled.** All amounts are in the same notional unit. There
  is no foreign exchange, no multi-currency holding and no currency conversion.

### Market and instruments

- **No live market data.** Prices come from a fixed, pre-loaded simulated price
  file, not from any exchange or data vendor. They do not update during the day.
- **Dates are chosen, not current.** Every valuation resolves against a session
  date you select from the loaded range. The platform never uses today's real
  calendar date for pricing. To see the dates available, check the session date
  selector.
- **No individual stocks, bonds, funds, options, futures, crypto or cash
  products.** The investable universe is a small fixed set of ETFs defined in
  Appendix C — three core sleeves covering broad equity, fixed income and
  cash/money-market, plus four sector and thematic satellite ETFs.
- **No instruments can be added** by a client.
- **No short selling, leverage, margin or derivatives.**

### Portfolio construction

- **No custom portfolios.** You invest in one of the firm's model portfolios as
  defined. You cannot set your own weights, exclude an instrument, or blend two
  portfolios.
- **No satellite selection, currently.** The policy defines satellite ETFs and a
  per-category cap on them, and the platform enforces that cap — but the model
  portfolios as published hold no satellite position, and there is no way for a
  client to choose one. The allowance is real policy; the feature that would
  consume it is not yet built.
- **No automatic or scheduled rebalancing**, no drift alerts, no standing
  instructions and no recurring investment plan.
- **Revert goes back one step only** — the state before the most recent
  rebalance. It is not a general undo and it is not a history you can roll
  through.

### Advice and coverage

- **Not financial advice.** This is an educational simulation.
- **No tax advice** of any kind, and no tax treatment is modelled — no capital
  gains, no dividend treatment, no tax-advantaged wrappers.
- **No advice on insurance, estate planning, property, lending, retirement
  products, or debt.**
- **No performance forecasts, projections or guarantees.** The assistant will
  not tell you what a portfolio will return, and will not guarantee any outcome.
- **No comparison against other firms' products**, and no commentary on specific
  companies or market events.
- **No general-purpose assistance.** The assistant answers questions about this
  platform, your record and the firm's policies. It will decline travel
  planning, recipes, homework, coding help and general knowledge.

### Privacy boundaries

- **No other client's information,** in any form, for any reason. There is no
  request that unlocks it.
- **No internal risk-scoring detail.** The factors, weights, thresholds and
  arithmetic behind your score are not disclosed, and the assistant does not
  hold them. Your category is explained to you in plain language, and the rules
  that applied to you are named.
- **No internal configuration or instructions.** The assistant will not disclose
  its own operating instructions, nor how the material it is given is formatted.
  Asking what your record *contains* is a question about your own data and is
  answerable; asking how it is structured internally is not.

---

## Where the authoritative answer lives

| Question | Authoritative source |
|---|---|
| Eligibility rules, overrides, knowledge-check requirement, satellite caps | Appendix E: Company Suitability Policy |
| Which instruments exist and what they are | Appendix C: Instrument Universe |
| Portfolio codes, names and their composition | Platform Portfolio Reference |
| Conduct, fair dealing and disclosure obligations | The MAS guidelines and notices in the firm's library |
| Your own figures — holdings, cash, value, category | Your dashboard and your client record |

If this page and any of those disagree, they are right and this page is out of
date. Please report it.
