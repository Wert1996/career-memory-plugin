---
description: Connect Career Memory to the web app (approve in your browser)
argument-hint: "[app-url] [token]"
allowed-tools: Bash(python3:*)
---
Arguments: `$ARGUMENTS` (optional: an app URL, and optionally a `cm_…` token from Settings → API tokens).

Before running it, tell the user in one line: "Opening your browser: click Approve there." Then run (it waits up to about two minutes for the approval):

`python3 "${CLAUDE_PLUGIN_ROOT}/worker/cm.py" connect $ARGUMENTS`

If the browser didn't open, show the user the approval URL from the output. If it timed out, say so and suggest running `/career-memory:connect` again.

Report the result in one or two lines. Never repeat a token back in your reply.
