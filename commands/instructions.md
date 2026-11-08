---
description: Tell Career Memory how to read your sessions (projects, what to remember, emphasis) in plain English
argument-hint: "[what to change, e.g. treat repos api and web as one project \"Checkout\"]"
allowed-tools: Bash(python3:*), Read, Edit, Write
---
Arguments: `$ARGUMENTS`

Career Memory follows a plain-Markdown instructions file, `~/.career-memory/instructions.md`, in every extraction, project-grouping and memory-writing step. A `.careermemory.md` at a repo root adds instructions for sessions in that repo.

1. Run `python3 "${CLAUDE_PLUGIN_ROOT}/worker/cm.py" instructions`. It creates the file from a template if needed and prints its path and the active instructions.
2. If there are no arguments: show the user the active instructions (or say there are none yet), and give two or three short examples of what they could add (project grouping, what to skip, what to emphasise).
3. If there are arguments: edit the instructions file to include what the user asked. Write it as short, clear bullets under a fitting heading (`## About me`, `## Projects`, `## What to remember`). Keep existing instructions unless the request replaces them. Write below the comment block, never inside it. If the request is about one repo only and the user is in that repo, ask whether to put it in that repo's `.careermemory.md` instead.
4. Then say in one line that it applies to new sessions, and that `/career-memory:memory rebuild` and `/career-memory:memory regroup` re-apply it to what is already remembered.

Never put secrets in the file.
