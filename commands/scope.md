---
description: Change what Career Memory captures (allow, disallow, exclude, unexclude, work, pause, resume)
argument-hint: "<allow|disallow|exclude|unexclude> <path> | work <path> --org <Employer> | pause | resume"
allowed-tools: Bash(python3:*)
---
Arguments: `$ARGUMENTS`

Run: `python3 "${CLAUDE_PLUGIN_ROOT}/worker/cm.py" $ARGUMENTS`. Turn a relative path or `.` into an absolute path based on the current directory first.

- `work <path> --org <Employer>` tags sessions under that path as employer work (everything else counts as personal). Add `--remove` to undo.

Report the resulting scope.
