---
description: Process finished work sessions now (use --dry-run to preview redacted input)
argument-hint: "[--dry-run] [--limit N]"
allowed-tools: Bash(python3:*)
---
Run: `python3 "${CLAUDE_PLUGIN_ROOT}/worker/cm.py" run --force $ARGUMENTS`

With `--dry-run`, this prints the condensed, redacted text that would be sent to the model and changes nothing. Point out anything in it that looks sensitive. Otherwise, summarise in one line how many episodes were processed and which project memories were updated.
