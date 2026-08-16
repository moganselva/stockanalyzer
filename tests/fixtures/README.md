# Fixture provenance

All fixtures here are real recorded API responses, captured live by running the actual
`yfinance`/`httpx` client code against the real endpoints. None are synthetic or
hand-written.

- AAPL, 7203.T, ASML.AS, 1299.HK: captured **2026-08-14**.
- MSFT, NVDA, ORCL, CRM: captured **2026-08-17**, added to bring AAPL's own (sector,
  region) bucket — US/Technology — up to 5 members, the minimum
  `factors/registry.py::winsorized_zscore` requires before it will compute a real
  z-score instead of reporting "insufficient peers." Sector, currency, and exchange
  were independently verified live before adding these to `config/universe.yaml`;
  each one's ISIN was cross-checked against multiple independent public sources, not
  guessed.

| File pattern | Source | What it is |
|---|---|---|
| `yfinance_<TICKER>.json` | `yfinance.Ticker(t).info` | Full info dict for each ticker in `config/universe.yaml` |
| `yfinance_history_<TICKER>.json` | `yfinance.Ticker(t).history(period="14mo", auto_adjust=True)` | Daily closes, split/dividend-adjusted |
| `yfinance_earnings_<TICKER>.json` | `yfinance.Ticker(t).earnings_dates` | Dated EPS estimate/reported/surprise rows |
| `yfinance_<PAIR>_X.json` | `yfinance.Ticker("<PAIR>=X").info` | FX spot quote (JPYUSD, EURUSD, HKDUSD) |
| `stooq_<symbol>.csv` | `GET https://stooq.com/q/d/l/?s=<symbol>&i=d` | The **real** response body — as of this date Stooq gates this endpoint behind a client-side JS proof-of-work challenge, so the body is an HTML/JS challenge page, not CSV. This is the actual current failure mode, captured verbatim, not fabricated, for every ticker recorded here. `StooqProvider` is tested against it to confirm it raises `ProviderUnavailable` instead of misparsing the page as data. |

Re-recording: rerun the snippets in the M1 build session, or see
`src/stock_analyzer/data/providers/*.py` docstrings for the exact calls.
