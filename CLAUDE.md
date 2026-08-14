# CLAUDE.md — Stock Analyzer

Coding instructions for Claude Code working in this repository.
Read `docs/01_PRICE_ACTION_FRAMEWORK.md` before writing any analysis logic. It is the
specification; this file is the engineering contract.

---

## 1. What This Project Is

A hybrid equity analysis system for **global markets** on **free-tier data**, producing a
**dual-horizon** view (short: 1d–3mo, long: 1–5yr) for any listed ticker.

Two layers, deliberately separated:

- **Deterministic layer (Python).** Fetches data, normalises it, computes factors, runs valuation
  models, scores, applies gates, backtests. Same input → same output, always. No LLM calls.
- **Reasoning layer (Claude).** Reads the deterministic output and produces causal attribution,
  variant perception, the bear case, and the written decision. Never computes numbers itself.

**The boundary is absolute.** If Claude ever produces a number that the Python layer should have
computed, that is a bug. If the Python layer ever hardcodes a narrative judgement, that is also a
bug.

---

## 2. Repository Layout

```
stock-analyzer/
├── CLAUDE.md                      # this file
├── config/
│   ├── factors.yaml               # factor definitions, weights, thresholds — EDITABLE, NOT CODE
│   ├── universe.yaml              # markets, exchanges, index constituents, FX base
│   ├── providers.yaml             # data source priority, rate limits, retry policy
│   └── decision_rules.yaml        # gate thresholds, action bands, hysteresis, sizing caps
├── src/stock_analyzer/
│   ├── data/
│   │   ├── base.py                # Provider ABC — every source implements this
│   │   ├── providers/             # yfinance, stooq, edgar, fred, worldbank, finnhub, av...
│   │   ├── cache.py               # SQLite cache, TTL per data class
│   │   ├── normalize.py           # currency, fiscal calendar, share class, ticker mapping
│   │   └── quality.py             # cross-source reconciliation, staleness, completeness score
│   ├── factors/
│   │   ├── valuation.py  quality.py  momentum.py  revisions.py
│   │   ├── sentiment.py  flows.py    macro.py     growth.py
│   │   └── registry.py            # factor registry: name → fn, channel, horizon, direction
│   ├── valuation/
│   │   ├── dcf.py                 # forward DCF with explicit CAP
│   │   ├── reverse_dcf.py         # implied expectations — the highest-value module
│   │   ├── multiples.py           # own-history + peer-relative
│   │   └── scenarios.py           # bull/base/bear + probability-weighted EV
│   ├── attribution/
│   │   ├── decompose.py           # market / sector / style / residual
│   │   ├── channel.py             # E vs M vs F vs S attribution
│   │   └── events.py              # timestamped event tape matching
│   ├── decision/
│   │   ├── gates.py               # Stage 1 — hard vetoes
│   │   ├── scoring.py             # Stage 2 — L and S composites
│   │   ├── tree.py                # Stage 3 — action mapping with hysteresis
│   │   └── sizing.py              # position sizing
│   ├── backtest/
│   │   ├── engine.py              # walk-forward, point-in-time
│   │   ├── costs.py               # spread, impact, FX, withholding, borrow
│   │   └── metrics.py             # incl. Deflated Sharpe Ratio
│   ├── predictions/
│   │   ├── log.py                 # append-only prediction log
│   │   └── score.py               # resolve and score elapsed predictions
│   └── report/
│       ├── builder.py             # assembles the JSON handed to the reasoning layer
│       └── templates/
├── prompts/
│   ├── MASTER_ANALYSIS.md         # the main reasoning prompt
│   ├── ATTRIBUTION.md  VARIANT.md  BEAR_CASE.md  RED_TEAM.md
├── data/  (gitignored: cache.db, raw/, predictions.jsonl)
├── reports/
├── tests/
└── docs/
```

---

## 3. Non-Negotiable Engineering Rules

### 3.1 Data
1. **Every number carries provenance.** Return `Value(value, source, as_of, url, confidence)` —
   never a bare float. If provenance can't be attached, the value doesn't enter the system.
2. **Point-in-time or nothing.** Store the *publication date* alongside every fundamental. Never
   overwrite a historical figure with a restated one. Restatements are appended as new rows.
3. **Two sources for anything decision-critical.** Price, shares outstanding, currency, and
   reported EPS get cross-checked. Disagreement > 2% raises a `DataConflict` and lowers conviction.
4. **Missing is missing.** Never impute silently. `None` propagates and reduces the completeness
   score. Never substitute a sector median without flagging it in the output.
5. **Cache aggressively, respect rate limits.** SQLite cache with per-class TTL: intraday 15min,
   EOD prices 12h, fundamentals 24h, filings 24h, macro 24h, static metadata 30d. All providers
   go through a token-bucket rate limiter. Exponential backoff with jitter on 429.
6. **Currency and calendar discipline.** Everything normalised to a configurable base currency
   with the FX rate *as of the data date*, not today. Fiscal years aligned to calendar quarters
   before any cross-market comparison. This is where global coverage actually breaks — test it.

### 3.2 Factors
7. **Factors are declarative.** Defined in `config/factors.yaml` with name, formula reference,
   channel (`E`/`M`/`F`/`S`), horizon, expected direction, weight, and required inputs. Adding a
   factor must not require touching the scoring engine.
8. **Always sector- and region-relative.** Z-scores computed within `(sector, region)` buckets,
   winsorised at ±3σ. A raw cross-market comparison of a P/B is a bug, not a signal.
9. **Every factor declares its expected sign** and the codebase asserts it in tests. If a factor's
   realised sign flips in walk-forward, that is a finding to report, not a weight to quietly flip.

### 3.3 Determinism & Reproducibility
10. **Seed everything. Version everything.** Every run writes a manifest: config hash, code git
    SHA, data as-of timestamps, provider versions. Re-running with the same manifest must
    reproduce byte-identical output.
11. **No network calls in tests.** All providers have recorded fixtures. Tests run offline.
12. **No look-ahead, ever.** The backtest engine physically cannot see data with a publication
    date after the simulation date. Enforce this in the data layer, not by convention — write a
    test that tries to peek and asserts it raises.

### 3.4 The Reasoning Layer
13. **Claude receives a JSON payload, not raw data.** `report/builder.py` produces a complete,
    self-contained payload: computed factors, scores, gate results, valuation outputs, event tape,
    peer table, data-quality report. Claude reasons over this and nothing else.
14. **Claude never does arithmetic that matters.** If a calculation appears in a report, it came
    from Python. Claude's job is *interpretation, causality, and judgement under uncertainty*.
15. **Prompts are versioned files in `prompts/`**, not string literals in code. Changing a prompt
    is a reviewable diff.

### 3.5 Predictions
16. **Every prediction is logged, append-only, with all seven contract fields** (§7 of the
    framework doc), and scored when the horizon elapses. Build `predictions/score.py` in the first
    milestone, not the last — a system that never grades itself will drift for years unnoticed.

---

## 4. Tech Stack

| Concern | Choice | Note |
|---|---|---|
| Language | Python 3.11+ | |
| Package manager | `uv` | fast, lockfile-based |
| Data | `pandas`, `numpy`, `pyarrow` | Parquet for panel storage |
| Validation | `pydantic v2` | every provider response validated at the boundary |
| Config | `pydantic-settings` + YAML | no magic constants in code |
| HTTP | `httpx` + `tenacity` | async where it helps, retry with jitter |
| Storage | SQLite (cache + predictions), Parquet (panels) | no server dependency |
| Stats | `statsmodels`, `scipy` | rolling regressions for betas |
| Filings | `edgartools` or direct SEC EDGAR JSON API | |
| CLI | `typer` + `rich` | |
| Charts | `plotly` → static HTML | |
| Testing | `pytest`, `pytest-recording` | offline fixtures |
| Lint/format | `ruff`, `mypy --strict` on `src/` | |

Avoid heavy ML frameworks. This is a factor and valuation engine; a gradient-boosted black box
would defeat the entire purpose, which is *explaining* price action.

---

## 5. Build Order

Do not build this all at once. Each milestone must be tested and usable before the next begins.

| M | Deliverable | Done when |
|---|---|---|
| **M1** | Data layer + cache + normalisation + quality report | `analyze fetch AAPL 7203.T ASML.AS 1299.HK` returns clean, currency-normalised, provenance-tagged data for all four |
| **M2** | Factor library + registry, config-driven | `analyze factors <TICKER>` prints every factor with z-score, channel, horizon, and source |
| **M3** | Valuation: DCF, **reverse DCF**, multiples, scenarios | Reverse DCF states the growth rate the price implies, with sensitivity |
| **M4** | Attribution: market/sector/style/residual + event tape | `analyze why <TICKER> --window 30d` explains a move or honestly says "unexplained" |
| **M5** | Decision engine: gates → scores → tree → sizing | `analyze decide <TICKER>` outputs the full gate trace and both scores |
| **M6** | Reasoning layer: payload builder + prompts | `analyze report <TICKER>` produces the written dual-horizon report |
| **M7** | Prediction log + scorer | Predictions resolve automatically; calibration report available |
| **M8** | Backtest engine, point-in-time, walk-forward, DSR | Factor weights are validated rather than asserted |
| **M9** | Screening across a universe, watchlist, scheduled runs | |

**Start with M1 and stop.** Data quality is 70% of this project's difficulty and 100% of its
credibility. A beautiful decision tree fed by bad data is worse than useless — it is confidently
wrong.

---

## 6. Definition of Done (every PR)

- [ ] `ruff check` and `mypy --strict src/` clean
- [ ] Tests pass offline (no network)
- [ ] New factors have a sign-expectation test and appear in `factors.yaml`
- [ ] Any new number in a report traces to Python, not to prose
- [ ] Look-ahead test still passes
- [ ] `docs/` updated if the framework's logic changed

---

## 7. Known Traps (read before debugging)

- **yfinance is unofficial and breaks.** Yahoo's backend has changed repeatedly. Never make it a
  single point of failure — always have Stooq or Finnhub as fallback, and alert on schema drift.
- **Ticker identity is genuinely hard globally.** `BMW.DE` vs `BMW3.DE`, ADRs vs ordinaries, dual
  listings, share classes, Hong Kong H-shares vs A-shares. Build a resolver keyed on **ISIN** where
  available, and a manual override map. Budget real time for this.
- **Fiscal year alignment.** Japanese companies end in March, Australian in June. Comparing "FY24"
  across markets without calendar alignment produces silent garbage.
- **Split/dividend adjustment.** Confirm whether each provider returns adjusted or raw. Mixing
  them creates fake momentum signals.
- **Free tiers are small.** Alpha Vantage free is ~25 requests/day. Design for a cache-first,
  batch-nightly access pattern, not on-demand fan-out.
- **EM data is thin and late.** Lower the conviction ceiling for markets where fundamentals arrive
  with a long lag. Encode this per-market in `universe.yaml`.
- **Survivorship bias in free data.** Delisted tickers usually vanish from free APIs entirely.
  Snapshot the universe monthly to your own store from day one, or you will never be able to
  backtest honestly.

---

## 8. Guardrails

- Output is **decision support, not investment advice**. Every report carries the disclaimer.
- **Never fabricate a number or a causal story.** "Unexplained" and "data unavailable" are correct
  answers. See §2 and §9 of the framework doc.
- **No auto-trading.** This system does not place orders. If broker integration is ever added, it
  is read-only (positions and prices), never execution.
- **Respect data licences.** Free tiers are personal-use; do not redistribute raw vendor data.
- **Secrets in `.env`**, never committed. `.gitignore` covers `data/`, `.env`, `reports/`.
