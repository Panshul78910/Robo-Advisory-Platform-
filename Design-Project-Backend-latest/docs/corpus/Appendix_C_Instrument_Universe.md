# Appendix C: Instrument Universe

The platform's model portfolios are built from a fixed set of seven
exchange-traded funds. There are no single stocks in this iteration.

## Instruments

| Ticker | Instrument | Role in the platform |
|---|---|---|
| SPY | S&P 500 ETF | Core broad equity |
| AGG | US aggregate bond ETF | Core fixed income |
| BIL | 1-3 month Treasury bill ETF | Cash / money-market proxy |
| XLK | Technology sector ETF | Satellite |
| XLV | Healthcare sector ETF | Satellite |
| XLE | Energy sector ETF | Satellite |
| SMH | Semiconductor / AI thematic ETF | Satellite |

The eight model portfolios defined in Appendix E are built from these
instruments only. SPY, AGG and BIL are the core sleeves; XLK, XLV, XLE and SMH
are the sector and thematic funds from which a satellite position is chosen.

## How holdings are valued

All instrument prices are supplied to the platform. No live market data is used
at runtime, so the same session date always produces the same valuation.

Valuations use the adjusted closing price for the session date. Where the
session date is not a trading day, the most recent prior trading day's prices
apply.

Units held are the amount allocated divided by that date's adjusted closing
price, recorded to four decimal places. The current value of a position is its
units multiplied by the latest adjusted closing price, and its actual weight is
that value divided by the portfolio's total value.
