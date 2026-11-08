---
description: Show what Career Memory remembers per project, group repos into projects, merge projects, forget items, or rebuild
argument-hint: "[PROJECT] | regroup [--dry-run] | merge FROM INTO | forget IDS | rebuild [PROJECT]"
allowed-tools: Bash(python3:*)
---
Arguments: `$ARGUMENTS`

- `regroup [--dry-run]`: run `python3 "${CLAUDE_PLUGIN_ROOT}/worker/cm.py" regroup [--dry-run]`. Claude looks at every repo/folder that has been remembered and groups them into real projects (one product or initiative can span several repos), then rebuilds the project memories and syncs. Show the resulting grouping. Suggest `--dry-run` first if the user seems unsure.
- `merge <from> <into>`: run `python3 "${CLAUDE_PLUGIN_ROOT}/worker/cm.py" merge "<from>" "<into>"`. Folds one project into another; this choice is kept by future regroups.
- `forget <ids>`: run `python3 "${CLAUDE_PLUGIN_ROOT}/worker/cm.py" forget <ids>` (ids come from `/career-memory:timeline`). The affected project memories are rebuilt without them.
- `rebuild [project]`: run `python3 "${CLAUDE_PLUGIN_ROOT}/worker/cm.py" rebuild [project]`.
- Otherwise run `python3 "${CLAUDE_PLUGIN_ROOT}/worker/cm.py" memory $ARGUMENTS` and show the output as is.

New repos are grouped automatically: the first time work from a new repo is remembered, it joins an existing project when it's part of the same work.
