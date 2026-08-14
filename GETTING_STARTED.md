# Getting Started with Claude Code — macOS

Written for your MacBook Pro, ending with the Stock Analyzer running. Steps 1–5 are Claude Code
itself; steps 6–10 set up this project. Total time about 30 minutes, most of it waiting on
downloads.

Everything in a `Terminal` block goes in the macOS Terminal app (Cmd+Space, type "Terminal").
Everything in a `Claude Code` block gets typed at the `>` prompt *inside* a running session.

---

## Step 0 — Check you can actually use it

Claude Code needs a **Pro, Max, Team, or Enterprise** subscription, or a Claude Console account
with credits. **The free Claude.ai plan does not include Claude Code access.** You're already
using the desktop app, so if you're on a paid plan you're set.

Also needed: macOS 13.0 or later, 4 GB+ RAM. Check with  → About This Mac.

---

## Step 1 — Install Claude Code

**Terminal:**

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

This is the native installer and it **auto-updates in the background**, which is what you want.

If you already use Homebrew and prefer it: `brew install --cask claude-code`. Note that Homebrew
installs do *not* auto-update — you'd run `brew upgrade claude-code` yourself. The native
installer is the easier path.

Close and reopen Terminal after it finishes, so your shell picks up the new command.

---

## Step 2 — Verify the install

**Terminal:**

```bash
claude --version
```

You should see something like `2.1.211 (Claude Code)`.

If you get `command not found`, reopen Terminal first — that fixes it most of the time. If it
persists:

```bash
claude doctor
```

That prints read-only diagnostics about install health and settings problems without starting a
session. It's also the right first move any time something behaves oddly later.

---

## Step 3 — Log in

**Terminal:**

```bash
claude
```

First run opens your browser to authenticate. Sign in with the same account you use for the
desktop app. Credentials are stored, so this is a one-time step. To switch accounts later, type
`/login` inside a session.

---

## Step 4 — Learn the four things that matter

You're now at the `>` prompt. Before doing real work, know these:

| What | Why it matters |
|---|---|
| **`Shift+Tab`** | Cycles permission modes: default (asks before each change) → `acceptEdits` (auto-approves edits) → `plan` (proposes without editing). This is the single most useful key in Claude Code |
| **`/help`** | Lists every command. Typing `/` alone shows commands and skills |
| **`/clear`** | Wipes conversation history. Use it between unrelated tasks — a stale context makes Claude worse, not better |
| **`Esc`** | Interrupts Claude mid-work. Use it the moment it heads somewhere wrong, rather than waiting politely |

Exit with `/exit` or Ctrl+D twice. Resume later with `claude -c` (continue most recent in this
folder) or `claude -r` (pick from a list).

---

## Step 5 — Try it on something disposable

Do not start on the Stock Analyzer. Spend five minutes somewhere consequence-free first.

**Terminal:**

```bash
mkdir ~/scratch && cd ~/scratch && claude
```

**Claude Code:**

```
> write a python script that fetches the current time in Kuala Lumpur and prints it nicely
> now add a test for it
> commit this with a sensible message
```

Watch how it asks permission before writing, how it runs things itself, how it reports back. Five
minutes here saves confusion later.

---

## Step 6 — Install the project's prerequisites

**Terminal:**

```bash
# Homebrew, if you don't have it
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

brew install git python@3.11
curl -LsSf https://astral.sh/uv/install.sh | sh    # uv: the Python package manager this project uses
```

Verify:

```bash
git --version && python3 --version && uv --version
```

---

## Step 7 — Unpack the Stock Analyzer

**Terminal:**

```bash
mkdir -p ~/projects && cd ~/projects
unzip ~/Downloads/stock-analyzer-autonomy.zip -d stock-analyzer
cd stock-analyzer
git init && git add -A && git commit -m "Initial scaffold: framework, instructions, autonomy kit"
```

The `git init` matters more than it looks. Without version control you can't review what an
autonomous session changed, and reviewing the diff is the supervision that "minimal supervision"
still requires.

---

## Step 8 — Get your free API keys

Fifteen minutes, once. All free, no card required:

| Source | Where | What it gives you |
|---|---|---|
| **FRED** | fred.stlouisfed.org → My Account → API Keys | US macro, rates, credit spreads |
| **Finnhub** | finnhub.io → Register | Global fundamentals, analyst estimates, news |
| **Alpha Vantage** | alphavantage.co → Get free API key | Gap-filling (~25 requests/day) |
| **Tiingo** | tiingo.com → Register | Price fallback, news |

**Terminal:**

```bash
cat > .env <<'EOF'
FRED_API_KEY=your_key_here
FINNHUB_API_KEY=your_key_here
ALPHAVANTAGE_API_KEY=your_key_here
TIINGO_API_KEY=your_key_here
EOF
```

`.env` is already in `.gitignore`, and `.claude/settings.json` explicitly denies Claude from
reading it — so an unattended session can't leak a key into a transcript or a commit.

---

## Step 9 — Add the MCP servers

**Terminal:**

```bash
claude mcp add --scope project openbb -- uvx --from openbb-mcp-server --with openbb openbb-mcp --transport stdio
claude mcp add --scope project fetch  -- uvx mcp-server-fetch
claude mcp add --scope project sqlite -- uvx mcp-server-sqlite --db-path ./data/cache.db

claude mcp list
```

`--scope project` writes `.mcp.json` into the repo so the setup is committed and reproducible,
rather than living only on this machine.

---

## Step 10 — Start building

**Terminal:**

```bash
cd ~/projects/stock-analyzer
claude --model opusplan
```

**Claude Code — first turn, to check it understood the spec:**

```
> Read CLAUDE.md, AUTONOMY.md, docs/01_PRICE_ACTION_FRAMEWORK.md and
  docs/02_DATA_AND_MCP_SETUP.md. Summarise the two-layer separation in under 200
  words and tell me what you think the hardest engineering problem is.
```

If it doesn't say something close to *global ticker identity resolution and point-in-time
fundamentals*, have it re-read before going further. That check costs one turn and catches a
misread spec before it becomes a thousand lines of wrong code.

**Then hand off milestone 1** — copy the M1 goal from `MILESTONES.md`, and press `Shift+Tab` to
reach auto mode first so it isn't stopping for approval on every step.

---

## What to do when it goes sideways

| Symptom | Fix |
|---|---|
| `command not found: claude` | Reopen Terminal. Then `claude doctor` |
| Claude heads in the wrong direction | `Esc` immediately. Don't wait for it to finish |
| Answers feel confused or off-topic | `/clear` and restate. Long stale context degrades quality |
| Keeps asking permission for the same command | Add it to the `allow` list in `.claude/settings.json` |
| It says a milestone is done | Run `./scripts/verify_milestone.sh N` yourself. Trust the script over the summary |
| Something feels broken generally | `claude doctor`, then `claude update` |

---

## Habits worth forming early

**Be specific.** "Fix the login bug where users see a blank screen after wrong credentials" gets a
real fix. "Fix the bug" gets a guess.

**Let it explore before it edits.** `Shift+Tab` into plan mode for anything non-trivial and read
the plan before approving. Cheaper than reviewing the wrong implementation.

**Use `/clear` between unrelated tasks.** More context is not better context.

**Read the diff.** `git diff` after each milestone. Watch specifically for the drift signature of
long autonomous runs — a loosened assertion, a new `xfail`, a `fillna` that appeared to make a
test pass. The system is built to catch that, but nothing replaces five minutes of your eyes.

**One milestone at a time.** Nine milestones under one goal compounds errors and destroys your
ability to tell which layer broke.

---

## Sources

- [Claude Code Quickstart](https://code.claude.com/docs/en/quickstart)
- [Advanced setup — system requirements, install methods, updates](https://code.claude.com/docs/en/setup)
- [Connect Claude Code to tools via MCP](https://code.claude.com/docs/en/mcp)
- [Model configuration](https://code.claude.com/docs/en/model-config)
- [Keep Claude working toward a goal](https://code.claude.com/docs/en/goal)
