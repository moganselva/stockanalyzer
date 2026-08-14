# Price Action Framework — Why Stocks Move, and What to Do About It

**Purpose:** the analytical substrate for the Stock Analyzer project. Everything the code computes
and everything the reasoning layer argues must trace back to a mechanism in this document.

**Scope:** global equities (US, Europe, Asia-Pacific, EM), free-tier data only, dual horizon
(short-term: 1 day – 3 months; long-term: 1 – 5 years).

**Not investment advice.** This is a research and decision-support framework. Outputs are
probabilistic views, not recommendations. See §9.

---

## 1. The Identity That Governs Everything

Every stock move must decompose into one of three channels. This is an accounting identity, not a
theory, and it is the discipline that keeps the analysis honest:

```
Total Return  ≈  Dividend Yield  +  Earnings Growth  ±  Change in Multiple
                 └─ cash back ─┘     └─ E channel ─┘     └── M channel ──┘

Price = EPS × P/E          →  ΔP% ≈ ΔEPS% + Δ(P/E)%
```

(Bogle's decomposition, applied empirically across regions by Robeco: US 2015–2024 returns came
from *both* earnings growth and multiple expansion; Europe/Japan had earnings growth but no
re-rating; EM re-rated while earnings deteriorated.)

**The M channel expands further.** Using a Gordon-style dividend/FCF discount identity:

```
P/E  ≈  f( expected growth g,  cost of equity r,  payout/reinvestment quality )
r    =  risk-free rate  +  equity risk premium  +  stock-specific risk premium
```

So the multiple moves when: (a) expected long-run growth changes, (b) the risk-free rate moves,
(c) the market's risk appetite moves, or (d) the perceived riskiness/durability of *this* business
changes.

**Practical rule for the engine:** every factor in §3 must be tagged with the channel it acts
through — `E` (earnings), `M` (multiple), `F` (flow/mechanical, acts on M via supply-demand),
or `S` (share count). A factor that cannot be tagged is not a factor; it is a narrative.

### 1.1 The Fourth Channel: Flows

The identity above assumes prices clear against fundamental value. They do not clear instantly.
Gabaix & Koijen's **Inelastic Markets Hypothesis** finds that $1 of net inflow into the equity
market raises aggregate market value by roughly $5 — demand curves for stocks are steeply
downward-sloping, not flat. At the single-stock level this means index inclusion, buybacks,
lock-up expiries, and forced deleveraging move prices *without any change in fundamentals*.

Flows are the dominant driver at short horizons and the least visible one on free data. Treat any
move you cannot explain with E or M as a flow hypothesis and go looking for the mechanical cause.

### 1.2 Horizon Separation — the single most important structural idea

Different drivers dominate at different time scales. Mixing them is the most common analytical
error.

| Horizon | Dominant driver | What is basically noise at this horizon |
|---|---|---|
| Intraday – 1 week | Order flow, liquidity, positioning, news shock | Intrinsic value, ROIC |
| 1 week – 3 months | Earnings surprise + estimate revisions (PEAD), momentum, catalysts | Terminal growth assumptions |
| 3 months – 1 year | Earnings revision trend, multiple re-rating, macro regime | Day-to-day flow |
| 1 – 5 years | ROIC vs WACC, reinvestment runway, competitive durability, starting valuation | Momentum, sentiment |

The engine must therefore produce **two independent scores** (short-horizon and long-horizon) and
must never average them into one number. A stock can legitimately be *long-term buy, short-term
avoid*. That is a valid and common state, not a contradiction.

---

## 2. Why a Stock Moves — the Causal Taxonomy

Answering "why did it move?" is a **decomposition + attribution** problem, executed in this order:

1. **Strip the market.** `r_stock = α + β_mkt·r_mkt + β_sector·r_sector + ε`.
   Most single-stock moves are market and sector. Only `ε` needs explaining.
2. **Strip the style.** Regress `ε` on factor returns (value, momentum, size, quality, low-vol).
   A "mystery" 4% drop is often just a momentum unwind.
3. **Attribute the residual.** Only the residual is idiosyncratic. Match it against a
   timestamped event tape: earnings, guidance, filings (8-K/6-K/announcements), analyst actions,
   regulatory news, index events, insider filings, sector news.
4. **Assign channel.** Did the residual come from ΔEPS expectations (E), Δmultiple (M), or a
   flow event (F)? Compute this explicitly: fetch consensus EPS before and after the move.
   If consensus barely changed but price fell 8%, it was a de-rating — a change in what the
   market will *pay* for the same earnings.
5. **Label confidence.** `Confirmed` (documented event, dated, magnitude consistent),
   `Probable` (plausible event, timing fits), `Unexplained` (say so — do not invent a story).

> **Anti-hallucination rule.** "Unexplained" is a legitimate and required output. The model must
> never manufacture a narrative to fill a residual. Roughly 30–40% of daily idiosyncratic moves
> have no identifiable public cause.

---

## 3. The Factor Library

Nine blocks. Each factor carries: **mechanism**, **channel**, **horizon**, **direction**,
**typical magnitude**, **decay**, **evidence strength**, **failure mode**.

### Block A — Intrinsic Value / Valuation `channel: M` `horizon: long`

| Factor | Mechanism | Expected price action |
|---|---|---|
| DCF / FCFE fair value gap | Price converges toward discounted cash flows as uncertainty resolves | Large positive gap (>30% MoS) → positive drift over 1–3 yrs, *very* weak <1 yr |
| **Reverse DCF (implied expectations)** | Solve for the growth/margin the current price already embeds | If implied growth > your defensible forecast → asymmetric downside. This is the highest-value single valuation output |
| EV/EBIT, EV/Sales, FCF yield vs own 5-yr history | Mean reversion of the multiple | Bottom-decile own-history multiple → re-rating tailwind |
| P/B, P/E vs sector peers | Relative cheapness | Cross-sectional value premium |
| Sum-of-the-parts / asset value | Hidden asset recognition | Step re-rating on a catalyst (spin-off, sale) |

**Critical empirical nuance (OSAM, *Factors from Scratch*):** value stocks' fundamentals typically
**deteriorate** after purchase. The value premium is earned almost entirely through **multiple
expansion** — the market overshoots on the downside and re-rates once fundamentals merely
*stabilise*. Excess return concentrates in **year one** and flattens after. This means: cheapness
alone is a bet on re-rating, and re-rating needs a *stabilisation trigger*. Cheap without a trigger
is dead money.

**Failure mode:** the value trap — cheap, deteriorating fundamentals, no stabilisation, terminal
decline (structural disruption, melting ice cube). See the Gate logic in §6.

### Block B — Earnings Power & Revisions `channel: E` `horizon: short–medium` ★ strongest short-horizon signal

| Factor | Mechanism | Expected price action |
|---|---|---|
| **Earnings surprise (SUE)** | Market under-reacts to new information | **PEAD**: drift continues in the direction of the surprise for ~60–90 days |
| **Revenue surprise** | Revenue surprises are harder to manufacture than EPS | Adds meaningfully to PEAD beyond EPS surprise alone |
| **Estimate revision breadth** (% of analysts raising, 1m/3m) | Analysts revise slowly and serially | Positive breadth → positive drift; the single most reliable free-data medium-term signal |
| Guidance change | Direct reset of the E path | Immediate re-pricing + revision cascade over following weeks |
| Analyst responsiveness | Faster analyst revision → weaker subsequent drift | Drift is largest where coverage is thin/slow — i.e. small caps, non-US names |

**Why it works:** investor under-reaction to earnings news. Drift is stronger where information
diffuses slowly. **This is your edge on a global, small/mid-cap, thin-coverage universe.**

### Block C — Quality, Moat & Capital Allocation `channel: E + M` `horizon: long`

| Factor | Mechanism | Expected price action |
|---|---|---|
| **ROIC − WACC spread** | Value creation only exists when spread > 0 | Sustained positive spread supports a structurally higher multiple |
| **Competitive Advantage Period (CAP)** | How many years the spread survives | The dominant term in any long-horizon DCF; almost never modelled explicitly, so it's where variant views live |
| Gross margin level & stability | Pricing power proxy | Stable/rising margin → multiple support |
| **Accrual ratio / cash conversion (OCF÷NI, FCF÷NI)** | Low cash conversion signals aggressive recognition | High accruals → **negative** future returns (robust anomaly) |
| Leverage, interest cover, refi wall | Solvency + fragility | Non-linear: fine until it isn't, then gap-down |
| Buyback vs issuance (net share change) `channel: S` | Direct EPS arithmetic + signalling | Persistent net buyback → mechanical EPS tailwind |
| Capital allocation record (M&A ROI, incremental ROIC) | Determines whether growth creates or destroys value | Growth at ROIC < WACC **destroys** value — growth is not automatically good |
| Governance (dual-class, related-party, comp design, insider ownership) | Agency risk | Persistent discount; occasional blow-up |

### Block D — Growth & Reinvestment `channel: E`

Revenue CAGR & acceleration/deceleration (second derivative matters more than level), TAM
penetration, unit economics, incremental ROIC, backlog/deferred revenue, cohort retention.

**The rule:** value = growth × (ROIC − WACC) × duration. Decompose growth into price, volume and
mix. Growth funded by dilution or debt at low returns is a negative, not a positive.

### Block E — Momentum & Trend `channel: E + M` `horizon: short–medium`

| Factor | Mechanism | Expected price action |
|---|---|---|
| **12-1 month momentum** | Under-reaction to gradually diffusing information | Continuation over 3–12m |
| 1-month reversal | Liquidity provision / overshoot | Short-term *mean reversion* — opposite sign to 12-1 |
| 52-week-high proximity | Anchoring bias | Near-high stocks drift higher |
| Relative strength vs sector/index | Isolates idiosyncratic strength | Persistence |
| MA structure (50/200), realised vol, drawdown | Trend regime + risk state | Regime classifier, not a standalone signal |
| Volume confirmation | Conviction behind the move | Price move on low volume is far less durable |

**Critical empirical nuance (OSAM):** momentum works because recent returns **predict actual
earnings growth** better than valuation does. Momentum stocks' fundamentals genuinely improve — but
the multiple contracts while it happens, and by **year two the factor mean-reverts into
underperformance**. Momentum is therefore a *rented* signal with a hard expiry. Value converges
toward fair value; momentum diverges above it. They are temporally complementary — the mechanical
basis for the value+momentum combination (Asness, Moskowitz & Pedersen, *Value and Momentum
Everywhere* — the two are negatively correlated and their combination has historically had a far
higher Sharpe than either alone, across 8 asset classes and markets).

### Block F — Sentiment & Positioning `channel: M` `horizon: short` — *contrarian at extremes*

| Factor | Mechanism | Expected price action |
|---|---|---|
| Short interest % float, days-to-cover | Crowded short = fuel | Moderate short interest → negative drift; extreme + positive catalyst → violent squeeze |
| Options skew, put/call, IV rank | Hedging demand & fear pricing | Extreme skew often marks local capitulation |
| **Gamma positioning** | Dealer hedging is mechanically price-amplifying | Short-gamma dealers amplify moves both ways; long-gamma pins price |
| Insider buying (open-market, cluster) | Best-informed agents | Cluster buying → positive drift. **Insider selling is near-uninformative** (diversification, 10b5-1) |
| Analyst dispersion | Disagreement proxy | High dispersion → **lower** future returns (Diether-Malloy-Scherbina) |
| News/transcript tone | Narrative shift | Weak standalone, useful as a confirming overlay |
| Retail attention (search trends, forum volume) | Retail crowding | Spikes → short-term overshoot then reversal |
| Baker-Wurgler style aggregate sentiment | Market-wide risk appetite | High sentiment → subsequent **underperformance** of speculative, hard-to-value, high-vol, unprofitable, distressed stocks. Sentiment conditions *which factors work* |

**Key structural insight:** sentiment is best used as a **regime conditioner**, not as a stock
signal. When aggregate sentiment is high, tilt toward quality/profitability. When it is depressed,
speculative and deep-value names have their asymmetry.

### Block G — Flows & Liquidity `channel: F` `horizon: short, mechanical, high-confidence`

Index inclusion/deletion and float-adjusted reweights; IPO lock-up expiries (D+180, D+366);
secondary offerings & ATM programmes; buyback execution windows and blackout periods; ETF/fund
flows into the sector; passive ownership share; free float and ADV (days-to-trade); margin
debt/deleveraging; window dressing and quarter-end rebalancing; tax-loss selling (Dec) and the
January effect.

These are the **highest-confidence short-term predictions available**, because the demand is
mechanical and the dates are known in advance. Under inelastic markets, the price impact is far
larger than the flow size suggests. Build a **known-dates calendar** — it is the backbone of the
short-horizon module.

### Block H — Macro & Discount Rate `channel: M` `horizon: all` — *conditions everything*

Real yields (the denominator — long-duration/high-growth stocks have the highest sensitivity);
yield-curve shape; policy path and central-bank divergence; inflation surprise; credit spreads (the
best single risk-appetite gauge); USD (EM and exporter earnings translation); commodity/input
costs; FX translation for multinationals; country risk premium and capital controls; sector-level
cyclicality betas.

**Do not treat macro as a stock signal.** Use it to (a) set the discount rate, (b) classify the
regime, and (c) scale conviction. Compute each stock's empirical beta to real yields, the dollar,
oil and credit spreads via rolling regression rather than assuming it.

### Block I — Idiosyncratic Catalysts & Events `channel: E or M` `horizon: dated`

Scheduled: earnings dates, investor days, index review dates, patent expiries, regulatory decision
dates (approvals, licences), contract renewals, refinancing maturities, lock-up expiries, AGMs.
Unscheduled but foreseeable: M&A (as target or acquirer), litigation milestones, product cycles,
management change, activist stakes, spin-offs, guidance resets.

Every catalyst gets: **date/window, direction, magnitude estimate, probability, what to watch**.
Rank by *expected impact*, not by date.

---

## 4. Expected Price Action — Individual Factors

Direction and horizon for a factor firing **in isolation**, all else equal. Confidence reflects
strength of published evidence *and* reliability on free data.

| # | Factor state | Direction | Horizon | Typical magnitude | Confidence |
|---|---|---|---|---|---|
| 1 | Positive earnings surprise (SUE top decile) + revenue beat | ↑ | 1–90 d | Drift continues past the announcement pop | **High** |
| 2 | Estimate revision breadth strongly positive (3m) | ↑ | 1–6 mo | Persistent | **High** |
| 3 | Guidance cut | ↓ | Immediate + drift | Gap-down then continued drift | **High** |
| 4 | 12-1 momentum top decile | ↑ | 3–12 mo | Then reverses in yr 2 | **High** |
| 5 | 1-month return top decile | ↓ | 1 mo | Short-term reversal | Medium |
| 6 | Deep value (bottom decile EV/EBIT) | ↑ | 12–36 mo | Via **multiple expansion**, not growth | Medium |
| 7 | High accrual ratio / OCF÷NI < 0.8 | ↓ | 6–24 mo | Quality drag | **High** |
| 8 | Sustained ROIC − WACC > 500bps | ↑ | Multi-year | Compounding, supports premium multiple | **High** |
| 9 | Net buyback > 3% of shares p.a. `S` | ↑ | 6–24 mo | Mechanical EPS lift | Medium-High |
| 10 | Secondary offering / heavy dilution `S` | ↓ | Immediate | Dilution + signalling | **High** |
| 11 | Index inclusion announced `F` | ↑ | Announce→effective | Front-run, then partial reversal | **High** |
| 12 | Lock-up expiry `F` | ↓ | Days around date | Magnitude scales with days-to-trade vs ADV | Medium-High |
| 13 | Cluster insider buying | ↑ | 3–12 mo | Positive drift | Medium-High |
| 14 | Insider selling | ~ | — | Largely uninformative alone | Low |
| 15 | Extreme short interest + positive catalyst | ↑↑ | Days | Squeeze, violent and non-linear | Medium |
| 16 | High short interest, no catalyst | ↓ | 3–12 mo | Informed-short drift | Medium |
| 17 | High analyst dispersion | ↓ | 6–12 mo | Disagreement penalty | Medium |
| 18 | Real yields rise 50bps | ↓ | Immediate | Hits long-duration/high-multiple hardest | **High** (direction) |
| 19 | Credit spreads widen sharply | ↓ | Immediate | Levered & cyclical worst | **High** |
| 20 | USD strengthens sharply | ↓ | Weeks | EM + US multinationals; ↑ for US-domestic & EM importers | Medium-High |
| 21 | Aggregate sentiment at an extreme high | ↓ | 6–24 mo | Speculative/unprofitable/high-vol names underperform most | Medium-High |
| 22 | Retail attention spike | ↑ then ↓ | Days then weeks | Overshoot and reversal | Medium |
| 23 | Reverse-DCF implied growth >> defensible forecast | ↓ | 1–3 yr | Asymmetric downside | Medium-High |
| 24 | Sector in early-cycle position with expanding margins | ↑ | 6–18 mo | Cyclical re-rating | Medium |

### 4.1 Magnitude Estimation

Never state magnitude from intuition. Estimate it empirically from the stock's own history:

```
Expected |move| ≈ event_beta × factor_shock,
   where event_beta is fitted from the stock's own history of comparable events
   (e.g. mean absolute 1-day and 20-day return after its last 12 earnings beats),
   and widened to a range by the dispersion of those historical outcomes.
```

Always output a **range with a probability**, never a point estimate. `"+4% to +9% over 20
sessions, ~60% probability of a positive 20-day return"` is a usable claim. `"target price $180"`
implies a precision that does not exist.

---

## 5. Factor Interactions — Combinations

Factors are **not additive**. The interactions carry more information than the levels.

### 5.1 The high-value combinations

| Combination | Interpretation | Expected action |
|---|---|---|
| **Cheap + Quality + improving revisions** | Mispriced good business with a live stabilisation trigger | Strongest long setup. Highest conviction state in the whole framework |
| **Cheap + positive momentum** | Re-rating already underway, market has begun to agree | Value's re-rating with momentum's timing. The classic combination |
| **Quality + negative short-term momentum + intact fundamentals** | Good business on temporary bad news | Accumulate on weakness. Requires evidence the damage is genuinely transient |
| **Positive SUE + positive revision breadth + positive momentum** | Fully aligned fundamental momentum | Strongest short-horizon setup |
| **High short interest + positive earnings surprise + low float** | Forced covering | Violent upside, non-linear, short-lived |
| **Deep value + insider cluster buying + falling leverage** | Insiders confirm the stabilisation the market doubts | Strong contrarian long |

### 5.2 The traps — combinations that *look* good and are not

| Combination | Why it's a trap | Expected action |
|---|---|---|
| **Cheap + deteriorating fundamentals + negative revisions + declining ROIC** | **Value trap.** Cheapness is correct pricing of terminal decline | Avoid. Cheapness needs a stabilisation trigger, not just a low multiple |
| **High momentum + extreme valuation + crowded + high retail attention** | **Momentum crash setup.** Multiple already prices perfection | Trim/exit. Momentum crashes are fast and correlated |
| **High growth + ROIC < WACC + rising dilution** | Growth is destroying value; each $ reinvested returns <$1 | Avoid regardless of the growth headline |
| **Strong reported EPS + weak cash conversion + rising DSO/DIO** | Earnings quality failure; likely reversal | Avoid. Run the full quality screen before anything else |
| **Cheap + high leverage + refi wall + widening credit spreads** | Equity is a call option on a distressed capital structure | Avoid unless explicitly sizing it as a small option-like position |
| **Good fundamentals + severe macro headwind to its dominant factor beta** | Right stock, wrong regime | Hold/reduce, wait for regime turn |
| **Positive surprise + stock falls anyway** | Expectations were higher than consensus (buy-side whisper) | Signal is *bearish*, not bullish. Trust the price reaction over the print |

### 5.3 Conflict Resolution Rules

When signals disagree, resolve in this fixed priority order:

1. **Data integrity beats everything.** Accounting red flags void all other positive signals.
2. **Solvency beats valuation.** No margin of safety survives a going-concern risk.
3. **Horizon separation beats averaging.** Do not net a long-term buy against a short-term sell.
   Report both. Let position sizing and entry timing express the tension.
4. **The price reaction beats the fundamental print.** If the market rejects good news, the market
   knows something about positioning or expectations that you do not.
5. **Mechanical flows beat narrative** at horizons under one month.
6. **Fundamentals beat flows** at horizons over one year.
7. **When confidence is low, size down.** Do not resolve genuine ambiguity by picking a side.

---

## 6. The Decision Tree

Three stages: **hard gates** (binary, veto power) → **scoring** (continuous) → **action mapping**
(with hysteresis). Gates run first and are non-negotiable — no score can override a failed gate.

### Stage 1 — Hard Gates (any failure → `AVOID`, stop, explain)

```
G0  DATA INTEGRITY
    ├─ ≥2 independent sources agree on price/shares/currency?          no → AVOID (unverifiable)
    ├─ Financials < 12 months stale?                                    no → AVOID (stale)
    └─ Currency, share class, ADR ratio correctly resolved?             no → AVOID

G1  ACCOUNTING QUALITY
    ├─ OCF/NI ≥ 0.7 (3-yr avg)?                                        no → FLAG, then AVOID if ≥2 flags
    ├─ Balance-sheet accrual ratio not in worst decile?                 no → FLAG
    ├─ DSO / DIO growth not >> revenue growth?                          no → FLAG
    ├─ GAAP vs adjusted gap not persistently extreme?                   no → FLAG
    └─ Beneish M-Score < -1.78 (where computable)?                      no → FLAG
    ≥2 flags → AVOID.  Reported numbers cannot be trusted; nothing else matters.

G2  SOLVENCY & GOING CONCERN
    ├─ Net debt/EBITDA < sector distress threshold?                     no → AVOID (or size as option)
    ├─ Interest cover > 2.0x?                                           no → AVOID
    ├─ No refi wall within 18m without a funding plan?                  no → AVOID
    └─ Altman Z / sector equivalent above distress zone?                no → AVOID

G3  INVESTABILITY
    ├─ ADV × 20 days ≥ intended position size?                          no → AVOID (can't exit)
    ├─ Market accessible, no capital controls / foreign ownership cap?  no → AVOID
    └─ Not an imminent delisting / suspension / halt risk?              no → AVOID

G4  GOVERNANCE VETO
    └─ Fraud allegation, auditor resignation, restatement, regulator
       investigation into the accounts, or repeated related-party
       value transfer?                                                  yes → AVOID
```

### Stage 2 — Dual Scoring

Two independent composites. Each sub-factor is winsorised at ±3σ, converted to a
**sector-relative and region-relative z-score** (never a raw cross-market comparison — a Japanese
bank's P/B is not comparable to a US software company's), then weighted.

```
LONG-HORIZON SCORE  (L, −100…+100)      SHORT-HORIZON SCORE  (S, −100…+100)
  Valuation / MoS ......... 30%           Earnings revisions & SUE ..... 30%
  Quality & moat .......... 25%           Price momentum (12-1) ........ 20%
  Growth & reinvestment ... 20%           Catalyst proximity & skew .... 20%
  Capital allocation ...... 15%           Positioning & flows .......... 15%
  Governance & risk ....... 10%           Sentiment extremes (contra) .. 10%
                                          1-month reversal (contra) ..... 5%

MACRO/REGIME MULTIPLIER  (R, 0.5…1.2)   CONVICTION  (C, 0.2…1.0)
  applied to both scores.                  = data completeness × signal
  Built from the stock's own fitted           agreement × evidence strength
  betas to real yields, credit spreads,       × valuation-model robustness
  USD and its sector's cycle position.
```

**Weights are a starting hypothesis, not truth.** They must be validated by walk-forward testing
(§8) and the config must make them editable without code changes.

### Stage 3 — Action Mapping (with hysteresis)

Hysteresis is mandatory: the threshold to *enter* is stricter than the threshold to *exit*. Without
it the system whipsaws on noise and generates unusable turnover.

```
                        ┌─ NEW POSITION ────────────────────────────────────┐
L ≥ +40 and C ≥ 0.6  ──►│ STRONG BUY   — full target weight                 │
L ≥ +20 and C ≥ 0.5  ──►│ BUY          — half weight, add on S confirmation │
L ∈ (−20, +20)       ──►│ NO ACTION    — no edge; do not force a trade      │
L ≤ −20              ──►│ AVOID        — do not initiate                    │
                        └───────────────────────────────────────────────────┘

                        ┌─ EXISTING POSITION ───────────────────────────────┐
L ≥ +10                ►│ HOLD         — thesis intact                      │
L ∈ (−10, +10)         ►│ HOLD / TRIM  — thesis eroding, reduce to half     │
L ≤ −10  or  any gate
   newly failed        ►│ SELL         — exit                               │
                        └───────────────────────────────────────────────────┘

TIMING OVERLAY (modifies *execution*, never the long-horizon verdict)
  L high, S high  → buy now, full size
  L high, S low   → buy, but stage entry / wait for revision-breadth turn
  L low,  S high  → trading setup only; not an investment. Size small, hard stop, defined exit date
  L low,  S low   → avoid entirely

THESIS-BREAK OVERRIDE (immediate SELL regardless of score)
  • Any Stage-1 gate newly fails
  • The specific variant-perception claim is empirically falsified
  • ROIC − WACC turns negative and management guides to further reinvestment
  • Capital structure event materially dilutes or subordinates equity holders
```

### 3.1 Position Sizing

```
weight = base_weight × C × R × (1 / vol_ratio) × liquidity_cap
   capped at:  single name ≤ 5%,  sector ≤ 25%,  single country ≤ 40%,
               single-factor exposure ≤ 30% of active risk
```

Sizing — not the buy/sell label — is where uncertainty gets expressed. A 0.3-conviction buy and a
0.9-conviction buy are both "BUY" and should be very different positions.

---

## 7. Prediction Output Contract

Every prediction the system emits must carry all seven fields. A prediction missing any of them is
rejected by the output validator:

1. **Direction** — up / down / range-bound
2. **Magnitude** — a *range*, never a point
3. **Horizon** — explicit dates or session counts
4. **Probability** — calibrated, and logged for later scoring
5. **Mechanism** — which factor(s), through which channel (E / M / F / S)
6. **Falsifier** — the specific observation that would prove the view wrong
7. **Confidence** — with the data gaps that constrain it named explicitly

**Every prediction is written to a log and scored when its horizon elapses.** A prediction that is
never scored is not a prediction, it is a comment. Track hit rate, magnitude error, and Brier
score by factor and by horizon, and feed that back into the factor weights.

---

## 8. Validation Discipline (non-negotiable)

- **Point-in-time data only.** Restated financials and current index membership are look-ahead
  bias. Use as-reported figures with their original publication dates.
- **Survivorship bias.** Delisted, acquired and bankrupt names must stay in the historical
  universe, or every backtest is a fantasy.
- **Walk-forward, never in-sample.** Fit on a rolling window, test on the next out-of-sample
  window, roll forward.
- **Deflate for multiple testing.** Every parameter you try inflates the observed Sharpe. Apply
  the **Deflated Sharpe Ratio** (Bailey & López de Prado) and record the number of configurations
  tested. A Sharpe of 1.5 found after 200 trials is noise.
- **Realistic costs.** Spread, market impact, FX conversion, dividend withholding tax, and
  borrow costs. Free-data backtests that ignore these routinely overstate returns by several
  percentage points a year.
- **Regime-split results.** Report performance separately across rate regimes, volatility
  regimes, and 2008 / 2020 / 2022-style stress windows. A strategy that only works in one regime
  is a regime bet in disguise.

---

## 9. Guardrails

- **This is decision support, not advice.** Every report carries the disclaimer.
- **Cite or omit.** Any factual claim about a company must trace to a fetched document with a URL
  and date. No claim from model memory. Financial data changes; training data is stale.
- **Never fabricate a number.** Missing data is reported as missing and lowers the conviction
  score. It is never estimated silently.
- **Never fabricate a causal story.** "Unexplained" is a valid output (§2).
- **State the bear case with equal force**, always, including for high-conviction longs.
- **Respect data licences.** Free-tier data is generally personal use only. Do not redistribute.
- **Base rates over narrative.** When a compelling story conflicts with the base rate, the base
  rate is usually right.

---

## Sources

- [What Drives Stock Prices: Fundamentals or Flows? — Aravis Capital](https://www.araviscapital.com/uploads/insights/Fundamentals-or-Flows.pdf)
- [Decomposing equity returns: Earnings growth versus multiple expansion — Robeco](https://www.robeco.com/en-int/insights/2025/02/decomposing-equity-returns-earnings-growth-versus-multiple-expansion)
- [Decomposition of US equity returns over time — LSEG/FTSE Russell](https://www.lseg.com/en/insights/ftse-russell/decomposition-of-us-equity-returns-over-time-is-it-different-this-time)
- [Total Shareholder Return — Mauboussin, Counterpoint Global (Morgan Stanley)](https://www.morganstanley.com/im/publication/insights/articles/article_totalshareholderreturns.pdf)
- [In Search of the Origins of Financial Fluctuations: The Inelastic Markets Hypothesis — Gabaix & Koijen, NBER w28967](https://www.nber.org/papers/w28967)
- [Value and Momentum Everywhere — Asness, Moskowitz & Pedersen, Journal of Finance](https://www.aqr.com/Insights/Research/Journal-Article/Value-and-Momentum-Everywhere)
- [Factors from Scratch — O'Shaughnessy Asset Management](https://osam.com/Commentary/factors-from-scratch)
- [Notes on OSAM's Factors from Scratch — Moontower](https://moontowermeta.com/notes-on-osams-factors-from-scratch/)
- [Post-Earnings-Announcement Drift and Analyst Forecasts — UCLA Anderson](https://www.anderson.ucla.edu/documents/areas/fac/accounting/drift503.pdf)
- [Post-Earnings-Announcement Drift: The Role of Revenue Surprises — Livnat, NYU Stern](https://pages.stern.nyu.edu/~jlivnat/drift%20revenue%20and%20earnings.pdf)
- [Analyst responsiveness and the post-earnings-announcement drift — Journal of Accounting and Economics](https://www.sciencedirect.com/science/article/abs/pii/S0165410108000220)
- [Investor Sentiment and the Cross-Section of Stock Returns — Baker & Wurgler, NBER w10449](https://www.nber.org/system/files/working_papers/w10449/w10449.pdf)
- [Fama-French 5-factor model: Why more is not always better — Robeco](https://www.robeco.com/en-int/insights/2024/10/fama-french-5-factor-model-why-more-is-not-always-better)
- [The Fama-French Five-Factor Model Plus Momentum — Schmalenbach Business Review](https://link.springer.com/article/10.1007/s41464-020-00105-y)
- [Constructing Long-Only Multifactor Strategies: Portfolio Blending vs. Signal Blending — Financial Analysts Journal](https://www.tandfonline.com/doi/full/10.2469/faj.v74.n3.5)
- [The Merits and Methods of Multi-Factor Investing — S&P Dow Jones Indices](https://www.spglobal.com/spdji/en/documents/research/research-the-merits-and-methods-of-multi-factor-investing.pdf)
- [MSCI Quality Indices Methodology](https://www.msci.com/eqb/methodology/meth_docs/MSCI_Quality_Indices_Methodology.pdf)
- [The Deflated Sharpe Ratio — Bailey & López de Prado](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)
- [Expectations Investing — Mauboussin & Rappaport, Columbia University Press](https://cup.columbia.edu/book/expectations-investing/9780231554848/)
