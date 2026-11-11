"""B2: the confirmation hash covers the trade set, not just the date."""

from __future__ import annotations

import datetime as _dt
import uuid
from decimal import Decimal

from app.domain.trading.arithmetic import Trade
from app.domain.trading.execution import compute_preview_token

CLIENT = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
EQ = uuid.UUID("11111111-1111-1111-1111-111111111111")
WHEN = _dt.date(2026, 6, 19)


def _trade(units: str, price: str) -> Trade:
    u, p = Decimal(units), Decimal(price)
    return Trade(EQ, "sell", u, p, u * p)


def test_the_same_preview_hashes_the_same():
    trades = [_trade("16.533744", "90.5348")]
    a = compute_preview_token(
        client_id=CLIENT,
        snapshot_seq=1,
        session_date=WHEN,
        portfolio_code="P4",
        trades=trades,
    )
    b = compute_preview_token(
        client_id=CLIENT,
        snapshot_seq=1,
        session_date=WHEN,
        portfolio_code="P4",
        trades=list(trades),
    )
    assert a == b
    assert len(a) == 64


def test_a_different_date_or_trade_list_is_a_different_token():
    trades = [_trade("16.533744", "90.5348")]
    base = dict(
        client_id=CLIENT, snapshot_seq=1, session_date=WHEN, portfolio_code="P4", trades=trades
    )
    token = compute_preview_token(**base)
    other_date = compute_preview_token(**{**base, "session_date": _dt.date(2026, 3, 2)})
    other_units = compute_preview_token(**{**base, "trades": [_trade("18.219503", "88.60")]})
    other_seq = compute_preview_token(**{**base, "snapshot_seq": 2})
    assert token != other_date
    assert token != other_units
    assert token != other_seq
