# Data Sources, MCPs, Connectors & Model Selection

Everything needed to make the Stock Analyzer actually run. Global coverage, free tiers.

---

## 1. Model Selection

The two-layer architecture means you should **not** use one model for everything. Match the model
to the job:

| Job | Model | Why |
|---|---|---|
| **Architecture, framework design, decision-tree logic, factor spec, debugging subtle data bugs** | **Claude Opus 5** | Highest reasoning depth. Use for the bootstrap session, milestone design, and anything where being wrong is expensive and hard to detect |
| **Bulk code implementation** (providers, tests, plumbing, refactors) | **Claude Sonnet** | Fast and much cheaper. The spec is already written; this is execution. Most of your token spend lives here |
| **Per-ticker reasoning layer** (the Part B report) | **Claude Opus 5** | This is the actual product. It is judgement under uncertainty with adversarial self-checking — exactly where the deeper model earns its cost |
| **Routine batch screening** across a large universe | **Claude Sonnet** | Volume work over pre-computed deterministic scores. Escalate only the names that clear the screen to Opus |
| **Red-team / adversarial review pass** | **Claude Opus 5** | Must be able to find the flaw the first pass missed |

**Practical setup in Claude Code:** run the bootstrap and each milestone *design* conversation on
Opus, then switch to Sonnet with `/model` for the implementation grind. Use subagents for
verification passes so the reviewer starts from a cold context and cannot simply agree with itself.

**Rough cost shape:** Sonnet does ~80% of the tokens (implementation), Opus does ~20% (design and
per-ticker reasoning) but carries most of the value. Do not run per-ticker analysis on the cheap
model to save money — that is the one place where model quality maps directly to output quality.

---

## 2. Free Data Sources — Global Coverage

Verified availability as of 2026. Free tiers change; the code must degrade gracefully, not crash.

### Tier 1 — Core (build on these)

| Source | Coverage | Data | Free limits | Role |
|---|---|---|---|---|
| **yfinance** (Yahoo) | 🌍 Global equities | OHLCV, splits, dividends, financials, options chains, some estimates | Unofficial; no documented limit | **Primary global workhorse.** Also the biggest fragility — Yahoo's backend has broken repeatedly since 2023. Never a single point of failure |
| **Stooq** | 🌍 US, Europe, Asia, indices, commodities | Historical prices | Unlimited via `pandas-datareader` | **Price fallback.** Boring and reliable |
| **SEC EDGAR** | 🇺🇸 US only | All filings (10-K/Q, 8-K), structured XBRL financials, insider Forms 3/4/5, 13F | 10 req/sec, free, no key | **Gold standard where it applies.** Point-in-time by construction — filings carry real dates |
| **FRED** | 🇺🇸 US macro | 800k+ series: rates, curve, CPI, credit spreads, employment | 1,000 req/day with free key | **Discount rate + regime module** |
| **World Bank Open Data** | 🌍 200+ countries | Country macro indicators | Unlimited, no key | Global macro where FRED stops |

### Tier 2 — Fundamentals, Estimates & News

| Source | Coverage | Data | Free limits | Role |
|---|---|---|---|---|
| **Finnhub** | 🌍 Global | Fundamentals, **analyst estimates**, earnings surprises, news + sentiment, insider transactions | Free key, generous per-minute rate limit; some endpoints premium-gated | **Best free source for the revisions block** — which is the strongest short-horizon signal in the framework. Verify per-endpoint gating at signup |
| **Alpha Vantage** | 🌍 Global | OHLCV, fundamentals, earnings, FX, technicals, news sentiment | **~25 req/day** — very tight | Gap-filling only. Never build a workflow that depends on it |
| **Tiingo** | 🌍 US + international | EOD prices, curated news | 500 req/hr, 1,000 symbols/day | Good news feed, solid price fallback |
| **Polygon.io** | 🇺🇸 US only | EOD prices, excellent quality | Unlimited historical prior-day close; no real-time | Best US price quality on a free tier |
| **SimFin** | 🇺🇸 US | Income statement, balance sheet, cash flow — **bulk download** | Bulk available | Bulk download beats API paging for backtests |
| **BLS** | 🇺🇸 US labour | CPI components, PPI, employment | 500/day, 2,500/day registered | Inflation detail |

### Tier 3 — Alternative & Sentiment

| Source | Data | Limits | Role |
|---|---|---|---|
| **Google Trends** (`pytrends`) | Search interest | Unofficial wrapper | Retail attention proxy |
| **NewsAPI** | Global financial headlines | 100 req/day | Thin, but usable for the event tape |
| **Exchange websites** (LSE, TSE, HKEX, SGX, Bursa, Euronext) | Company announcements | Scraping, varies | **The only real filing source outside the US.** Per-exchange scrapers — budget real time |
| **Central bank sites** (ECB, BoJ, BoE, RBA, BNM) | Policy rates, statements | Free | Macro regime for non-US markets |

### 2.1 The Honest Global-Coverage Gaps

Build these limitations into the conviction score rather than pretending they don't exist:

- **Analyst estimates outside the US/Europe are sparse on free tiers.** For much of Asia and EM
  the entire earnings-revisions block — the framework's strongest medium-term signal — will be
  dark. Cap conviction accordingly and say so in the report.
- **Non-US filings have no EDGAR equivalent.** Each exchange has its own announcement portal with
  its own format. There is no shortcut. Start with the two or three markets you actually trade.
- **Short interest and options data are largely US-only and mostly paid.** Block F will be partly
  unavailable globally.
- **Point-in-time historical fundamentals are essentially not available free.** You must build
  your own by snapshotting from day one. Start the snapshot job in M1 even though you won't
  backtest until M8 — a year of accumulated point-in-time data is an asset you cannot buy back
  later.
- **Delisted securities vanish from free APIs**, which means survivorship bias in any backtest
  built purely from live free data. Same fix: snapshot your universe monthly, forever.

---

## 3. MCP Servers

Two categories: **local MCPs** (self-hosted, free, run alongside Claude Code) and **remote
connectors** (hosted, some paid, connected via claude.ai).

### 3.1 Essential local MCPs

| MCP | Purpose | Install |
|---|---|---|
| **OpenBB MCP Server** ⭐ | Single unified interface to dozens of providers — equity, fundamentals, economy, news. **The highest-leverage install for this project.** Free and self-hosted; add provider API keys in `~/.openbb_platform/user_settings.json` | `pip install openbb-mcp-server` then `claude mcp add openbb -- uvx --from openbb-mcp-server --with openbb openbb-mcp --transport stdio` |
| **Filesystem MCP** | Read/write the repo, reports, cached data | Standard MCP, usually built in |
| **SQLite MCP** | Query the cache and prediction log conversationally | `claude mcp add sqlite -- uvx mcp-server-sqlite --db-path ./data/cache.db` |
| **Fetch MCP** | Retrieve filings, exchange announcements, IR pages that aren't in any API | `claude mcp add fetch -- uvx mcp-server-fetch` |
| **SEC EDGAR MCP** | Direct filing and XBRL access for US names | Several community servers exist; verify the repo before installing |
| **Yahoo Finance MCP** | Quick conversational data pulls during development | Community server; useful for exploration, not for the pipeline |
| **Git MCP** | Version control from within the session | Standard |
| **Sequential Thinking MCP** | Structured multi-step reasoning for the decision tree | Optional, helps on complex attribution |

> **Caution:** community finance MCP servers are of highly variable quality and several are
> abandoned. Read the source before installing anything that will touch API keys. Prefer OpenBB
> (maintained, large project) plus your own thin wrappers over a pile of one-off servers.

### 3.2 Remote connectors (via claude.ai → Connectors)

| Connector | What it adds | Cost | Verdict |
|---|---|---|---|
| **Bigdata.com** ⭐ | SEC filings, earnings-call transcripts, premium news, sentiment, tearsheets, events calendar — **all cited**. Ships with ~25 ready-made analysis skills (investment memo, earnings preview, variant perception, moat review, scenario analysis) that map almost one-to-one onto this framework | Paid | **The single highest-value paid add-on** if you ever loosen the free-only constraint. Its skill set is essentially a productised version of the reasoning layer you are building. Worth evaluating even just to benchmark your own output against |
| **Alpha Vantage MCP** | Official hosted server: 120+ tools, fundamentals, technicals, options, news | Free key (~25 req/day) | Convenient for exploration; the daily cap makes it useless for pipeline work |
| **FMP** | Broad market data, analyst data, calendars, COT | Paid tiers | Good value if you upgrade one thing, upgrade this — it fills the global estimates gap |
| **Interactive Brokers** | Global market data + your actual positions and P&L | Free with an IBKR account | **Genuinely useful if you have an account.** Real global coverage and it closes the loop between analysis and your real portfolio |
| **Oxford Economics** | Institutional macro forecasts | Paid | Only if macro overlay becomes central |

### 3.3 Suggested minimum viable setup

```
Local:   OpenBB MCP + Filesystem + SQLite + Fetch + Git
Keys:    FRED (free) + Finnhub (free) + Alpha Vantage (free) + Tiingo (free)
Python:  yfinance, pandas-datareader (Stooq), edgartools, fredapi, finnhub-python
Remote:  Interactive Brokers if you have an account
Later:   Bigdata.com or FMP once you know which gap actually hurts
```

That covers roughly 80% of the framework's data needs at zero cost. The missing 20% is
concentrated in: non-US analyst estimates, options/short-interest positioning, and point-in-time
history — and you should know exactly which of those matters to you before spending anything.

### 3.4 Claude Code configuration notes

- Add MCPs with `claude mcp add <name> -- <command>`; check with `claude mcp list`.
- Scope matters: use `--scope project` so `.mcp.json` is committed and the setup is reproducible.
- Store all API keys in `.env`, referenced from `providers.yaml`. Never commit them, never let an
  MCP config inline a key.
- Set up a **subagent for verification passes** so the reviewer starts cold and cannot rubber-stamp
  its own work.
- Consider a scheduled task for a nightly cache warm + snapshot job, so the point-in-time store
  accumulates without you thinking about it.

---

## 4. Cost & Rate-Limit Budget

| Item | Cost | Note |
|---|---|---|
| All Tier-1/2 free data | $0 | Rate-limited; cache-first design is mandatory |
| Claude Code usage | Your plan | Sonnet for implementation keeps this modest |
| Optional: FMP | ~$20–50/mo | Best single upgrade — closes the global estimates gap |
| Optional: Bigdata.com | Paid | Best output-quality upgrade |
| Optional: IBKR | $0 with account | Best portfolio-integration upgrade |

**Design rule:** the system must be fully functional at $0 and merely *better* with paid sources.
If a paid source becomes load-bearing, you have coupled yourself to a vendor — which is a design
decision, not an accident, and should be made deliberately.

---

## Sources

- [awesome-financial-data-apis — verified 2026 free-tier availability and rate limits](https://github.com/jeff3388/awesome-financial-data-apis)
- [Free Financial API Comparison for AI Agents (2026) — Qveris](https://qveris.ai/guides/free-financial-api-comparison/)
- [Best Stock Market and Financial Data APIs in 2026 — APIScout](https://apiscout.dev/guides/best-stock-market-financial-apis-2026)
- [openbb-mcp — OpenBB Docs](https://docs.openbb.co/odp/python/extensions/interface/openbb-mcp)
- [OpenBB — Open Data Platform (GitHub)](https://github.com/OpenBB-finance/OpenBB)
- [Finnhub Stock API — pricing and free tier](https://finnhub.io/pricing-stock-api-market-data)
- [Finnhub API documentation](https://finnhub.io/docs/api)
- [Connect Claude Code to tools via MCP — Claude Code Docs](https://code.claude.com/docs/en/mcp)
- [awesome-mcp-servers — finance & crypto](https://github.com/TensorBlock/awesome-mcp-servers/blob/main/docs/finance--crypto.md)
- [Yahoo Finance API: Complete Guide + Alternatives (2026) — MarketXLS](https://marketxls.com/blog/yahoo-finance-api-ultimate-guide)
