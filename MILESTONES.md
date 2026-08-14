# Milestone Goal Conditions

Copy-paste `/goal` strings, one per milestone. Run them **one at a time**. Setting a single goal
for all nine compounds errors and destroys your ability to tell which layer broke.

Every condition follows the same shape, for a reason:

- **A verifiable end state** — the script's single output line, not a judgement call.
- **The evasions named explicitly** — long autonomous runs drift toward making the check pass
  rather than making the system correct. Naming the shortcuts closes them.
- **A turn bound** — so a stuck goal reports instead of grinding.
- **Which subagent does what** — so model routing actually gets used.

Before each goal, `git checkout -b milestone-N`. The `git push` denial in settings means you
review and publish yourself.

---

### M1 — Data layer *(start here, then stop)*

```
/goal ./scripts/verify_milestone.sh 1 prints "MILESTONE 1: PASS". No test may be skipped
or xfailed to get there, and no synthetic fixture may be labelled as recorded. Use the
data-engineer subagent for implementation and test-writer for tests. Before declaring
done, have quant-reviewer review src/stock_analyzer/data/ and address every CONFIRMED
finding. Stop after 25 turns and report what remains if not met.
```

### M2 — Factor library

```
/goal ./scripts/verify_milestone.sh 2 prints "MILESTONE 2: PASS". Every factor must be
declared in config/factors.yaml with channel, horizon and expected direction, and adding
a factor must not require editing the scoring engine. Have quant-reviewer specifically
check for sign errors on the contrarian factors — 1-month reversal, analyst dispersion,
sentiment extremes. Stop after 25 turns and report what remains.
```

### M3 — Valuation

```
/goal ./scripts/verify_milestone.sh 3 prints "MILESTONE 3: PASS", and `analyze value
<TICKER> --reverse` states the growth rate and margin the current price implies, with a
sensitivity table. The reverse DCF is the highest-value module in this project — have
quant-reviewer verify the solver converges and that the implied-growth output is not
silently clamped at a bound. Stop after 25 turns and report what remains.
```

### M4 — Attribution

```
/goal ./scripts/verify_milestone.sh 4 prints "MILESTONE 4: PASS", and `analyze why
<TICKER> --window 30d` reports the share of the move it could NOT explain as an explicit
headline number. A run that explains 100% of every move is a bug, not a success — verify
on at least three tickers that "unexplained" appears where it should. Stop after 25 turns.
```

### M5 — Decision engine

```
/goal ./scripts/verify_milestone.sh 5 prints "MILESTONE 5: PASS". Gates must veto
regardless of score, hysteresis must hold (entry threshold strictly stricter than exit),
and thresholds must come from config/decision_rules.yaml with no magic numbers in code.
Have quant-reviewer trace one full worked example end to end and confirm the gate trace
matches docs/01_PRICE_ACTION_FRAMEWORK.md section 6. Stop after 25 turns.
```

### M6 — Reasoning layer

```
/goal ./scripts/verify_milestone.sh 6 prints "MILESTONE 6: PASS", and the payload from
report/builder.py is self-contained — the equity-analyst subagent can produce all eleven
report sections without needing any data outside it. Verify by running equity-analyst on
one real payload and checking no section reports missing inputs. Stop after 25 turns.
```

### M7 — Prediction log *(do not defer this)*

```
/goal ./scripts/verify_milestone.sh 7 prints "MILESTONE 7: PASS". The log must reject any
prediction missing one of the seven contract fields, must reject point price targets, and
must auto-score predictions when their horizon elapses. Include a test that replays a
synthetic year of predictions and produces hit rate, magnitude error and Brier score
broken down by factor and horizon. Stop after 25 turns.
```

### M8 — Backtest *(the one that must not be rushed)*

```
/goal ./scripts/verify_milestone.sh 8 prints "MILESTONE 8: PASS". The look-ahead test must
prove enforcement at the data layer — a deliberate attempt to read data published after the
simulation date must raise, not merely be avoided by convention. Walk-forward only, no
in-sample fitting anywhere. Costs must include spread, market impact, FX conversion,
dividend withholding and borrow. Report the Deflated Sharpe Ratio alongside the raw Sharpe
and record how many configurations were tested. Have quant-reviewer audit the whole
backtest module before declaring done, and treat any CONFIRMED finding as blocking.
Stop after 40 turns and report what remains.
```

### M9 — Screening

```
/goal ./scripts/verify_milestone.sh 9 prints "MILESTONE 9: PASS", and screening a universe
respects every provider rate limit with a cache-first access pattern — no fan-out that
would exhaust the Alpha Vantage daily cap. Stop after 25 turns.
```

---

## After Every Milestone

1. **Read the diff.** `git diff main --stat`, then read the interesting files. This is the
   supervision that "minimal supervision" still requires.
2. **Check for the drift signature:** newly skipped tests, loosened assertions, `xfail` markers,
   a `fillna` that appeared, a threshold moved to make something pass.
3. **Confirm the universal gates still pass** — they re-run at every milestone precisely so M6
   cannot quietly break M2.
4. `git push` yourself.

## The One Manual Step That Cannot Be Automated

After M1, validate against **live** data before trusting anything downstream. Fixtures prove the
code runs; only a real response proves the *mapping* is right. A revenue field silently mapped to
the wrong XBRL tag passes every offline test in the suite and then quietly poisons every valuation
built on top of it.

Pull one company you know well, in each market you care about, and check the numbers against the
actual filing with your own eyes. Thirty minutes, once. It is the highest-value half hour in the
entire build.
