---
description: Opt in to Career Memory capture (defaults to the current directory)
argument-hint: "[path] [--backfill-days N]"
allowed-tools: Bash(python3:*)
---
The user is opting in to Career Memory, which records accomplishments from their Claude Code sessions locally.

Arguments: `$ARGUMENTS`

1. If the arguments contain a path, use it as the allowed path; otherwise use the current working directory. Pass `--backfill-days N` through if given.
2. Run: `python3 "${CLAUDE_PLUGIN_ROOT}/worker/cm.py" setup --allow "<path>" [--backfill-days N]`
3. Report the output in two or three lines. Mention that:
   - only sessions under the allowed paths are captured; add more with `/career-memory:scope allow <path>`, or drop a `.careerignore` file in any repo to exclude it;
   - processing runs in the background at most twice a day, using their own Claude Code;
   - `/career-memory:run --dry-run` shows exactly what text would be sent for extraction.
