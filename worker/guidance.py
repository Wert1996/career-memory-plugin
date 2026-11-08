"""The engineer's own instructions for how sessions become memory, in plain Markdown.

~/.career-memory/instructions.md applies everywhere (extraction, project grouping, memory writing).
A `.careermemory.md` at a repo root adds instructions for sessions in that repo (extraction only).
"""
from __future__ import annotations

import os
import re
from typing import Optional

MAX_CHARS = 4000
REPO_FILE = ".careermemory.md"

TEMPLATE = """# Career Memory instructions

<!--
Write in plain English how Career Memory should read your sessions. Text inside these comment
markers is ignored, so write your instructions below this block (or delete it).
They apply to new sessions. To re-apply them to what is already remembered, run
/career-memory:memory rebuild and /career-memory:memory regroup.

Examples:

## About me
- I'm a backend engineer moving into ML infrastructure. Emphasise system design, scale and reliability.

## Projects
- Everything under ~/work/acme is one project called "Payments platform".
- Treat "infra-scripts" and "deploy-tools" as part of whatever project they support, never their own project.
- "playground" is throwaway experiments: never remember anything from it.

## What to remember
- Always record interviewing and mentoring work.
- Skip anything about my dotfiles, editor config or homelab.
- Don't mention the client "Contoso" by name; call it "a fintech client".
-->
"""


def _clean(text: str) -> str:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    lines = [l for l in text.splitlines() if l.strip()]
    if lines and lines[0].strip().lower() == "# career memory instructions":
        lines = lines[1:]
    return "\n".join(lines).strip()


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return _clean(f.read())
    except OSError:
        return ""


def path(home: str) -> str:
    return os.path.join(home, "instructions.md")


def ensure(home: str) -> str:
    """Create the instructions file from the template if missing; return its path."""
    p = path(home)
    if not os.path.exists(p):
        os.makedirs(home, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(TEMPLATE)
    return p


def load(home: str, repo_root: Optional[str] = None) -> str:
    """The instructions that apply, as a prompt section ('' when there are none)."""
    parts = []
    g = _read(path(home))
    if g:
        parts.append(g)
    if repo_root:
        r = _read(os.path.join(repo_root, REPO_FILE))
        if r:
            parts.append(f"For this repository:\n{r}")
    if not parts:
        return ""
    text = "\n\n".join(parts)[:MAX_CHARS]
    return ("\n\nTHE ENGINEER'S OWN INSTRUCTIONS. Follow them where they apply; they take precedence over the guidance "
            "above, but never over the rules against secrets, confidential details and invented facts:\n"
            f"<<<\n{text}\n>>>\n")
