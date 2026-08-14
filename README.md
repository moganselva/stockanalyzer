# Stock Analyzer — Start Here

A hybrid equity analysis system: **deterministic Python** computes the numbers, **Claude** does the
reasoning. Global markets, free-tier data, dual-horizon (short 1d–3mo / long 1–5yr).

## The Four Documents

| File | What it is | When you read it |
|---|---|---|
| **`docs/01_PRICE_ACTION_FRAMEWORK.md`** | The analytical spec. Why stocks move, the 9 factor blocks, expected price action per factor, interaction traps, and the full decision tree | Read first. This is the actual intellectual content |
| **`CLAUDE.md`** | The engineering contract. Repo layout, 16 non-negotiable rules, tech stack, 9-milestone build order, known traps | Claude Code reads this automatically every session |
| **`prompts/MASTER_ANALYSIS.md`** | Part A: the bootstrap prompt to paste into Claude Code. Part B: the per-ticker reasoning prompt | Part A once, Part B every analysis |
| **`docs/02_DATA_AND_MCP_SETUP.md`** | Free data sources with global coverage, MCP servers, connectors, and model selection | Before you write any code |

## Quick Start

```bash
mkdir stock-analyzer && cd stock-analyzer
# copy CLAUDE.md, docs/, prompts/ in

# minimum viable MCP setup
claude mcp add --scope project openbb -- uvx --from openbb-mcp-server --with openbb openbb-mcp --transport stdio
claude mcp add --scope project fetch  -- uvx mcp-server-fetch
claude mcp add --scope project sqlite -- uvx mcp-server-sqlite --db-path ./data/cache.db

# free API keys → .env
# FRED, Finnhub, Alpha Vantage, Tiingo

claude          # start on Opus
# paste Part A of prompts/MASTER_ANALYSIS.md
```

Then `/model sonnet` for the implementation grind, back to Opus for milestone design and for
running the actual per-ticker analysis.

## The Three Ideas That Matter Most

If you take nothing else from the framework document:

1. **Horizon separation.** Flows dominate days, earnings revisions dominate months, ROIC dominates
   years. Producing one blended score destroys the information. The system outputs two scores and
   never averages them.

2. **The channel identity.** `ΔPrice ≈ ΔEarnings + ΔMultiple` (+ dividends, ± share count). Every
   explanation must name its channel. Did consensus EPS actually move, or did the market just
   change what it will pay for the same earnings? Those are completely different situations that
   look identical on a price chart.

3. **The reverse DCF.** Instead of asking "what is it worth?", ask "what does the current price
   already assume?" Then judge whether that assumption is defensible. This is the highest-value
   single output in the entire system and the fastest route to a genuine variant view.

## Build Order

Start at M1 (data layer) and stop there. Data quality is 70% of the difficulty and 100% of the
credibility. A beautiful decision tree fed by bad data is not neutral — it is confidently wrong.

Build the **prediction log and scorer (M7) early**, not last. A system that never grades itself
will drift for years without anyone noticing.

---

*Research and decision support, not investment advice.*
