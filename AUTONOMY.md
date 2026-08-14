# Running This Project Autonomously

Answers two questions: **can Claude Code pick its own model?** and **can it build this with
minimal supervision?** Both answers are yes, with specific limits that are worth knowing before
you start rather than discovering at hour six.

---

## 1. Automatic Model Selection — What Actually Exists

There is **no single "auto" switch** that picks a model per task. There are three real mechanisms,
and used together they get you most of the way there.

### Mechanism 1 — `opusplan` (phase-based, automatic)

```bash
claude --model opusplan
```

Opus runs during plan mode, then execution automatically drops to Sonnet. This maps almost exactly
onto this project's shape: design a milestone with the deep model, grind the implementation with
the cheap one. It is already set in `.claude/settings.json` as the project default.

### Mechanism 2 — subagent `model:` frontmatter (task-based, automatic) ⭐

This is the real answer to your question. Claude delegates to a subagent based on its
`description` field, and each subagent declares its own model. So model choice follows task type
without you thinking about it. The routing is already written in `.claude/agents/`:

| Agent | Model | Gets the work when |
|---|---|---|
| `quant-reviewer` | **opus** | Reviewing factors, scoring, backtests, valuation, decision tree |
| `equity-analyst` | **opus** | Running an actual per-ticker analysis (the product) |
| `data-engineer` | **sonnet** | Providers, cache, normalisation, fixtures |
| `test-writer` | **sonnet** | Tests and fixtures |
| `Explore` | **haiku** | Routine codebase search (overrides the built-in, which would otherwise inherit Opus) |

The economics matter here: the built-in `Explore` agent inherits the main conversation's model, so
without that override every codebase search on an Opus session burns Opus tokens to run `grep`.

### Mechanism 3 — `best` and `fable` (capability-based)

- `best` → Fable 5 where your org has it, otherwise the latest Opus.
- `fable` → **Claude Fable 5**, documented as the most capable model in Claude Code, built for
  *"tasks larger than a single sitting"* — it sustains long autonomous sessions, investigates
  before acting, and verifies its own work more often than smaller models.

**For this project specifically:** Fable 5 is the closest match to "build it with minimal
supervision," because self-verification is exactly the property that unattended work needs. It is
not the default and may bill to usage credits depending on your plan — check the `/model` picker
for a "Requires usage credits" label before committing to a long run.

**Recommended:** start on `opusplan`. If a milestone stalls or you want a genuinely long
hands-off run, switch that session to `fable`.

---

## 2. Running Unattended

Three pieces combine. None of them is sufficient alone.

### `/goal` — keeps working across turns until a condition holds

After each turn a small fast model checks your condition and, if unmet, starts another turn
instead of handing control back. It clears itself when satisfied.

**The critical constraint:** the evaluator cannot run commands or read files. It only judges what
Claude has surfaced in the conversation. So the condition must be something Claude's own output
demonstrates. That is exactly why `scripts/verify_milestone.sh` prints one unambiguous line —
it converts "is this done?" from an opinion into a fact.

```
/goal ./scripts/verify_milestone.sh 1 prints "MILESTONE 1: PASS", with no test skipped
      or marked xfail to achieve it, and no fixture presented as recorded when it is
      synthetic. Stop after 25 turns and report what remains if not met.
```

Always include the turn bound. Without it a stuck goal will grind.

### Auto mode — removes the per-tool approval prompt

`/goal` handles per-turn prompts; auto mode handles per-tool prompts. You need both for genuinely
unattended operation. `.claude/settings.json` ships a deliberately narrow allowlist:

- **`git push` denied.** An unattended session should never publish. You review, you push.
- **`curl` and `wget` denied.** All network access must go through the versioned provider layer
  where it is rate-limited, cached and provenance-tagged. Ad-hoc fetching bypasses every guarantee
  the architecture makes.
- **Reading `.env` denied.** An autonomous session cannot leak a key into a transcript or commit.

Prefer this allowlist over `--dangerously-skip-permissions`. The narrow list is what makes
walking away reasonable.

### The milestone gate — stops premature "done"

The single biggest failure mode of long autonomous coding runs is declaring success on work that
doesn't hold up. Three defences are built in: the deterministic script, the `quant-reviewer`
subagent on Opus reviewing from a cold context, and universal gates in the script that re-run at
*every* milestone so M6 cannot quietly break M2.

### Putting it together

```bash
cd stock-analyzer
claude --model opusplan

# turn 1: orient
> Read CLAUDE.md, docs/01_PRICE_ACTION_FRAMEWORK.md, docs/02_DATA_AND_MCP_SETUP.md
  and AUTONOMY.md. Summarise the two-layer separation in under 200 words and tell me
  what you think the hardest engineering problem is.

# then hand off
> /goal ./scripts/verify_milestone.sh 1 prints "MILESTONE 1: PASS", with no test
  skipped or xfailed to get there. Use the data-engineer subagent for implementation
  and the test-writer subagent for tests. Before declaring done, have quant-reviewer
  review the data layer and address every CONFIRMED finding. Stop after 25 turns and
  report what remains if not met.
```

Then check back. Repeat per milestone. **Do not set one goal for all nine milestones** — the
error compounds and you lose the ability to tell which layer went wrong.

---

## 3. What Genuinely Needs You

Short and specific. Everything not on this list can run unattended.

| # | Needs you | Why | Time |
|---|---|---|---|
| 1 | **Free API keys** — FRED, Finnhub, Alpha Vantage, Tiingo | Account registration needs a human. Put them in `.env`; Claude never reads that file | ~15 min, once |
| 2 | **Which markets you actually trade** | Global coverage is a research direction, not a build target. Ticker-identity work is per-market and expensive. Pick 2–3 for M1 | 5 min |
| 3 | **Live-data validation at M1** | Fixtures prove the code runs; only real responses prove the *mapping* is right. A field silently mapped to the wrong XBRL tag passes every offline test | 30 min, once |
| 4 | **Reviewing factor weights after M8** | Backtest output is evidence, not a decision. Whether a weight change is a real finding or curve-fitting is a judgement call | 1 hr, once |
| 5 | **Any spend decision** | Upgrading to FMP or Bigdata.com is yours | — |
| 6 | **`git push`** | Deliberately denied. Review the diff yourself | Per milestone |

Note what is *not* on the list: architecture, provider implementation, factor construction, the
decision tree, tests, attribution logic, the report builder. That is the large majority of the
work, and it can run with you checking in between milestones.

---

## 4. The Blocker Worth Knowing Up Front

**Cowork sandboxes cannot reach financial data APIs.** Tested directly from this session — Yahoo
Finance, Stooq, SEC EDGAR, FRED, World Bank and Finnhub all fail to connect. PyPI, npm and the
GitHub API work fine, so it is a network allowlist, not a broken environment. Running a Cowork
task "on your computer" instead does not fix it: that workspace has no network access at all.

**This project must be built in the Claude Code CLI on your own machine**, where it uses your
real network. That is not a limitation of the design — it is where a data-integration project
belongs anyway, close to the keys and the disk.

What *can* still be done in a Cowork session: everything that doesn't touch live data — the
codebase against synthetic fixtures, the framework, config, tests, and the decision logic. If you
want a head start, that work transfers cleanly. Just remember the M1 live-data validation step
(§3, item 3) still has to happen on your machine before you trust a single number.

---

## 5. Honest Expectations

**What will go well.** The deterministic layer is well-specified and mostly mechanical. Factor
construction, the scoring engine, the decision tree, the report builder, and the test suite are
all things Claude Code does reliably against a spec this explicit. The `quant-reviewer` catching
look-ahead bias on a cold context is genuinely valuable and hard to do yourself.

**What will be slower than you expect.** Global ticker identity resolution — ADRs, dual listings,
share classes, HK H-shares versus A-shares. There is no clean solution, only accumulated special
cases. Budget several sessions and expect a manual override map. Non-US filing sources are
per-exchange scrapers with no shortcut. And provider schema drift will break things periodically;
that is maintenance, not a bug.

**What no amount of autonomy fixes.** The point-in-time data problem. You cannot buy back history
you did not record, so the snapshot job in M1 matters more than anything else in the early build.
A year from now that accumulated store is the thing that makes an honest backtest possible, and
nothing you do later can substitute for having started it today.

**The thing to watch for.** A long autonomous run tends to drift toward *making the check pass*
rather than *making the system correct* — weakening an assertion, marking a test xfail, quietly
imputing a missing value. That is why the goal conditions name those specific evasions, why
`grep` for silent imputation is a universal gate, and why the reviewer runs on a separate model
with a cold context. Spot-check the diffs anyway. Trust, but read the diff.
