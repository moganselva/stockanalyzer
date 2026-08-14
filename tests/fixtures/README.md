# Fixture provenance

All fixtures here are real recorded API responses, captured live on **2026-08-14** by
running the actual `yfinance`/`httpx` client code against the real endpoints. None are
synthetic or hand-written.

| File pattern | Source | What it is |
|---|---|---|
| `yfinance_<TICKER>.json` | `yfinance.Ticker(t).info` | Full info dict for AAPL, 7203.T, ASML.AS, 1299.HK |
| `yfinance_<PAIR>_X.json` | `yfinance.Ticker("<PAIR>=X").info` | FX spot quote (JPYUSD, EURUSD, HKDUSD) |
| `stooq_<symbol>.csv` | `GET https://stooq.com/q/d/l/?s=<symbol>&i=d` | The **real** response body — as of this date Stooq gates this endpoint behind a client-side JS proof-of-work challenge, so the body is an HTML/JS challenge page, not CSV. This is the actual current failure mode, captured verbatim, not fabricated. `StooqProvider` is tested against it to confirm it raises `ProviderUnavailable` instead of misparsing the page as data. |

Re-recording: rerun the snippets in the M1 build session, or see
`src/stock_analyzer/data/providers/*.py` docstrings for the exact calls.
