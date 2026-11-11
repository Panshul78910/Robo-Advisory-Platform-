# Trading: switch, preview tokens, and materiality

This note records three contracts added to the simulated trading path. Nothing
here calls the LLM; the trade arithmetic stays in `app/domain/trading/`.

## Switch

A client who already holds a model portfolio cannot invest again. To move to a
different eligible portfolio they **switch**:

- `GET /api/clients/me/switch/preview?portfolio_code=P4&session_date=…`
- `POST /api/clients/me/switch` with `{ "portfolioCode": "P4", "previewToken": "…" }`
  and an `Idempotency-Key`.

The event type is `switch` (`portfolio_event_type`). The linked decision reuses
`decision_type = rebalance`. Suitability is the same gate as invest: a request
above the assessed category is refused, escalated on a separate connection, and
writes no trade.

`current_portfolio_id()` follows the newest event that names a portfolio, so a
switch is how the held code changes. Rebalance still restores the *current*
portfolio's targets and cannot change which one that is.

Forward migration: `supabase/migrations/009_switch_event.sql` (`ALTER TYPE …
ADD VALUE IF NOT EXISTS 'switch'`). Fresh databases pick it up from
`001_extensions.sql`.

## Preview token

Rebalance and switch confirmations must execute the trade set that was
previewed (C.1). The preview response includes `previewToken`, a SHA-256 over:

`client_id`, `snapshot.seq`, `session_date`, `portfolio_code`, canonical trades
(`instrumentId`, `side`, `units`, `price`, `grossAmount`).

`POST /clients/me/rebalance` and `POST /clients/me/switch` require that token.
If the server recomputes a different hash (session date changed, a trade landed
in between, materiality settings changed), it returns **409** with
`code: preview_stale` and writes nothing. The client re-previews and confirms
again.

## Materiality

Two settings, both `Decimal`, both overridable from the environment:

| Setting | Env | Default | Meaning |
|---|---|---|---|
| `rebalance_band_pct` | `REBALANCE_BAND_PCT` | `0.5` | Percentage points of drift required before a rebalance produces any trades |
| `min_trade_notional` | `MIN_TRADE_NOTIONAL` | `1.00` | Drop an individual trade whose gross amount is below this |

They are parameters on `rebalance_trades(...)` — `arithmetic.py` stays pure and
does not read settings. Callers in `valuation.py` / `execution.py` pass the
configured values. A switch uses a band of `0` (a change of mandate, not a
drift trim) but still drops dust notionals.

A rebalance whose trade list is empty is a **no-op**: no `portfolio_event`, no
decision, response `{ alreadyOnTarget: true }`. That is what "already on
target" in the UI is for.
