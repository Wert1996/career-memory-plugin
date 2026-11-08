# Career Memory

A Claude Code plugin that quietly turns your coding sessions into a condensed memory of what you've built, per project. There is nothing to review: it decides what's worth remembering and keeps the rest out.

Everything runs on your machine. Extraction uses your own Claude Code (`claude -p`). Nothing leaves your computer unless you connect the web app ([career-memory.anshumaan.space](https://career-memory.anshumaan.space)), which then receives only the condensed project memories and items, never transcripts or code.

## Install

```
/plugin marketplace add Wert1996/career-memory-plugin
/plugin install career-memory
/career-memory:setup                      # opt in for the current directory
/career-memory:setup ~/code --backfill-days 30   # or a parent dir, including past sessions
```

Requires macOS or Linux with `python3` (3.9+) on PATH. There are no other dependencies.

## Commands

| Command | What it does |
|---|---|
| `/career-memory:setup [path] [--backfill-days N]` | Opt in. Nothing is captured before this runs. |
| `/career-memory:run [--dry-run] [--limit N]` | Process finished sessions now. `--dry-run` prints exactly what would be sent to the model. |
| `/career-memory:memory [project]` | Show each project's memory. `regroup` lets Claude group repos into real projects (one product can span several repos); `merge FROM INTO` does it by hand; `forget IDS` drops items (ids from the timeline) and rebuilds; `rebuild [project]` rebuilds from the log. New repos join an existing project automatically when they're part of the same work. |
| `/career-memory:timeline` | Show your timeline (also saved to `~/.career-memory/timeline.md`). |
| `/career-memory:scope allow\|disallow\|exclude\|unexclude <path>`, `pause`, `resume` | Change what gets captured. |
| `/career-memory:connect` | Link the web app: opens the browser to approve this computer, then syncs. Later changes sync automatically. |
| `/career-memory:instructions [change]` | Show or edit your instructions for how sessions are read (see below). |
| `/career-memory:status` | Show scope, memory counts and the next run time. |
| `/career-memory:uninstall` | Delete all local data. |

To exclude a repo, add a `.careerignore` file at its root.

## Steering it

Write plain-English instructions in `~/.career-memory/instructions.md` (or ask `/career-memory:instructions <what you want>` to write them for you). They are followed in every step: what's worth remembering, how repos are grouped into projects, and how memories are written. For example:

```markdown
## Projects
- Everything under ~/work/acme is one project called "Payments platform".
- "playground" is throwaway experiments: never remember anything from it.

## What to remember
- I'm moving into ML infrastructure; emphasise system design and reliability.
- Don't name the client "Contoso"; call it "a fintech client".
```

A `.careermemory.md` at a repo root adds instructions for sessions in that repo. Instructions apply to new sessions; `/career-memory:memory rebuild` and `regroup` re-apply them to existing memory. Safety rules (no secrets, no invented facts) always win.

## How it works

1. **Stop hook** (`scripts/enqueue.sh`): after each Claude reply, it appends the transcript path to `~/.career-memory/queue.jsonl`. If a run is due (at most every 12h), it starts the worker detached. It takes milliseconds.
2. **Worker** (`worker/cm.py run`):
   - Reads each transcript from its last byte offset. It also scans `~/.claude/projects/` in case a hook was missed.
   - Splits sessions into **work episodes** at 45-minute idle gaps, so a tab left open for months still yields separate episodes. The newest episode is processed only once it has gone idle.
   - **Condenses** each episode to your prompts, Claude's prose and action metadata (files edited, commands run). Code blocks, tool outputs, file contents and thinking are dropped.
   - **Scrubs** secrets and personal data: API keys, tokens, private keys, JWTs, `password=…`, URLs, emails, IPs, high-entropy strings and your home path.
   - Runs `claude -p` with no tools and no saved session to extract a few **items** worth remembering, each scored for **significance** (1-5). Setup trouble, small fixes and routine debugging are skipped.
   - Items scoring 3+ are **merged into the project's memory**: one living Markdown document per project (overview, what was built, hard problems, decisions, stack, timeline), rewritten at the level of capabilities rather than steps.
   - Everything lives in `~/.career-memory/claims.db` (SQLite). The item log is kept, so any project memory can be rebuilt. Low-significance items never leave your machine.
3. The worker's own `claude -p` calls are never captured: the hook sees `CAREER_MEMORY_WORKER=1`, the sessions aren't persisted, and their working directory is excluded.

## Configuration

`~/.career-memory/config.json`: `allowed_paths`, `excluded_paths`, `idle_minutes` (45), `run_interval_hours` (12), `min_messages` (4), `model` (`sonnet`), `backfill_days`, `min_significance` (3).

## Development

```
python3 -m unittest discover -s tests
CAREER_MEMORY_HOME=/tmp/cm python3 worker/cm.py setup --allow ~/code
CAREER_MEMORY_HOME=/tmp/cm python3 worker/cm.py run --dry-run --limit 3
```

To try the plugin locally: `claude --plugin-dir .`
