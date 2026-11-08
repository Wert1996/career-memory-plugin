"""Project memory: a living, condensed document per project, merged from new claims."""
from __future__ import annotations

from datetime import datetime
from typing import Callable, List

from extract import parse_json

BATCH = 60  # claims per merge call

PROMPT = """You maintain the long-term memory of one project a software engineer has worked on. It is their condensed professional history of this project: what someone would want to know a year from now, or when writing a resume.

Merge the NEW ITEMS into the CURRENT MEMORY and return the full updated memory.

How to merge:
- Integrate, don't append. Fold related items into one statement at the level of the capability ("Built X with Y, handling Z"), not individual steps. Many small steps on the same feature become one line.
- Keep only what matters: things built and shipped, design decisions and trade-offs, hard problems solved, technologies used, real outcomes. Drop trivia, setup trouble and minor fixes, even if listed in the new items.
- When newer work supersedes older (a rewrite, a replacement), keep the latest state and mention the evolution only if it's notable.
- Never invent facts, metrics, users or impact. Never include secrets, URLs, people's names or confidential details.
- Keep dates: end every bullet in Built, Hard problems solved and Decisions with when it happened, as "(YYYY-MM)" or "(YYYY-MM to YYYY-MM)", taken from the item dates. When merging items, the range covers them all.
- Stay compact: at most ~400 words in total, no more than ~8 lines in "Built".

Format (Markdown, omit empty sections):
## Overview
1-2 sentences: what the project is, the engineer's role in it, and when (e.g. "Oct to Nov 2026").
## Built
- ... (YYYY-MM)
## Hard problems solved
- ... (YYYY-MM)
## Decisions and trade-offs
- ... (YYYY-MM)
## Stack
Comma-separated technologies.
## Timeline
- YYYY-MM: one line per phase of work

Project: {project}{org}

CURRENT MEMORY:
{current}

NEW ITEMS (date; significance 1-5; text):
{items}

Return ONLY a JSON object, no code fences: {{"memory": "<the full updated Markdown>", "one_liner": "<one sentence describing the engineer's work on this project>"}}
"""


def _items(claims) -> str:
    lines = []
    for c in claims:
        when = datetime.fromtimestamp(c["occurred_at"]).strftime("%Y-%m-%d") if c["occurred_at"] else "undated"
        lines.append(f"- {when}; {c['significance'] or 3}; {c['text']}")
    return "\n".join(lines)


def merge(current: str, claims: List, project: str, organization: str | None,
          call: Callable[[str], str]) -> dict:
    """Merge claims (sqlite rows) into the current memory. `call` runs one LLM prompt."""
    out = {"memory": current, "one_liner": ""}
    for i in range(0, len(claims), BATCH):
        prompt = PROMPT.format(
            project=project,
            org=f" (at {organization})" if organization else "",
            current=out["memory"] or "(empty: this is the first work recorded)",
            items=_items(claims[i : i + BATCH]),
        )
        data = parse_json(call(prompt))
        memory = str(data.get("memory", "")).strip()
        if memory:
            out = {"memory": memory, "one_liner": str(data.get("one_liner", "")).strip()[:300]}
    return out


# ---------- grouping workspaces (repos / folders) into projects ----------

GROUP_RULES = """A project is one product, system or initiative, not one repository or one session. Work in different repositories or folders belongs to the SAME project when it serves the same product or goal: e.g. a model repo and the service that uses it, frontend and backend of one app, a prototype and its rewrite, an evaluation harness for a system. Different employers are always different projects. Unrelated products stay separate. Name projects the way the engineer would on a resume: a short, recognisable name (an existing repo name is fine when it fits), no secrets or confidential codenames beyond what the names already show."""


def _when(ts) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m") if ts else "?"


def _ws(s: dict) -> str:
    org = f" (at {s['organization']})" if s.get("organization") else ""
    items = "\n".join(f"    - {t}" for t in s["items"])
    return f"- {s['workspace']}{org}, {_when(s['first_at'])} to {_when(s['last_at'])}, {s['count']} items:\n{items}"


RESOLVE_PROMPT = """You organise a software engineer's work memory into projects.

{rules}

EXISTING PROJECTS:
{projects}

NEW WORKSPACE:
{workspace}

Does the new workspace belong to one of the existing projects? Return ONLY a JSON object, no code fences: {{"project": "<exact name of the existing project, or a name for a new project>"}}
"""


def resolve(summary: dict, projects: List, call: Callable[[str], str]) -> str:
    """Pick the project a new workspace belongs to. `projects` are memory rows (project, organization, one_liner, first_at, last_at)."""
    lines = []
    for p in projects:
        org = f" (at {p['organization']})" if p["organization"] else ""
        lines.append(f"- {p['project']}{org}, {_when(p['first_at'])} to {_when(p['last_at'])}: {p['one_liner'] or ''}")
    listing = "\n".join(lines)
    data = parse_json(call(RESOLVE_PROMPT.format(rules=GROUP_RULES, projects=listing, workspace=_ws(summary))))
    name = str(data.get("project", "")).strip()[:80]
    return name or summary["workspace"]


REGROUP_PROMPT = """You organise a software engineer's work memory into projects.

{rules}

Group ALL of these workspaces into projects. Every workspace appears in exactly one project.{pinned}

WORKSPACES:
{workspaces}

Return ONLY a JSON object, no code fences: {{"projects": [{{"name": "...", "workspaces": ["...", "..."]}}]}}
"""


def regroup(summaries: List[dict], call: Callable[[str], str]) -> dict:
    """Return {workspace: project} for every workspace in `summaries`."""
    pinned = [s for s in summaries if s.get("manual")]
    note = ("\nThese were set by the engineer and must stay as given: " +
            "; ".join(f"{s['workspace']} -> {s['manual']}" for s in pinned)) if pinned else ""
    data = parse_json(call(REGROUP_PROMPT.format(rules=GROUP_RULES, pinned=note,
                                                 workspaces="\n".join(_ws(s) for s in summaries))))
    out = {}
    for p in data.get("projects") or []:
        name = str(p.get("name", "")).strip()[:80]
        for w in p.get("workspaces") or []:
            if name and isinstance(w, str):
                out[w] = name
    for s in summaries:  # anything the model dropped keeps its own name; pinned ones are enforced
        out[s["workspace"]] = s["manual"] or out.get(s["workspace"]) or s["workspace"]
    return out
