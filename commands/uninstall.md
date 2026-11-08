---
description: Delete all Career Memory local data
allowed-tools: Bash(python3:*)
---
This permanently deletes `~/.career-memory` (claims, settings, offsets). Ask the user to confirm first. Only after they confirm, run `python3 "${CLAUDE_PLUGIN_ROOT}/worker/cm.py" uninstall --yes`, then tell them to run `/plugin uninstall career-memory` to remove the plugin itself.
