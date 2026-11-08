---
description: Connect Career Memory to the web app (approve in your browser)
argument-hint: "[app-url] [token]"
allowed-tools: Bash(python3:*)
---
Arguments: `$ARGUMENTS` (optional: an app URL, and optionally a `cm_…` token from Settings → API tokens).

Run:

`python3 "${CLAUDE_PLUGIN_ROOT}/worker/cm.py" connect $ARGUMENTS`

It opens the browser to approve this computer and waits for up to two minutes. Tell the user to check the code in the browser matches the one printed, and approve. If it reports it is still waiting, ask the user to approve and then run `/career-memory:connect` again to finish.

Report the result in one or two lines. Never repeat a token back in your reply.
