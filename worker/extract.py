"""Claim extraction via the user's own Claude Code (`claude -p`)."""
from __future__ import annotations

import json
import os
import re
import subprocess
from typing import List

CATEGORIES = ["contribution", "decision", "problem_solved", "outcome", "learning", "skill"]

PROMPT = """You extract a software engineer's professional accomplishments from a condensed, redacted log of one AI-assisted coding work session.

The log has lines tagged [USER] (the engineer), [ASSISTANT] (their AI coding agent) and [ACTION] (tools the agent ran: files edited, commands run). Code and tool output were removed for privacy.

What is worth remembering (be selective, most sessions yield 0-3 items):
- Things built or shipped, architecture and design decisions with their trade-offs, hard problems solved (non-obvious root causes, performance, reliability, security), new technologies adopted, measurable outcomes stated in the log.
- NOT worth remembering: setup and environment trouble, dependency or tooling hiccups, typos, renames, small config tweaks, routine debugging of one's own fresh code, failed attempts, questions asked, things only discussed or planned. A hindrance belongs in memory only if overcoming it showed real skill.
- Describe the work at the level of the capability or feature, not the individual step. "Built incremental transcript ingestion with crash-safe byte offsets" — not "added an offset column".

significance (1-5): would a hiring manager, or the engineer in six months, care?
 5 = a major system, launch or result; 4 = a substantial feature or a hard problem solved; 3 = solid, notable work; 2 = minor; 1 = trivial. Be strict: most items are 2-3.

Rules:
- Only state what the log supports. Never invent metrics, scope, team size, users, impact or responsibilities.
- The engineer directed the agent, so credit completed work to the engineer.
- confidence "observed": directly shown (code written, tests passing, shipped). "inferred": a reasonable reading the log does not state outright.
- text: one resume-style statement, past tense, no "I", at most 30 words, specific about the technical substance.
- Never include secrets, credentials, personal data, URLs, or customer/employer-confidential names. Describe internal systems generically.
- category is one of: {categories}.
{guidance}
Project hint: {project}

Return ONLY a JSON object, no prose, no code fences:
{{"summary": "<one sentence on what this session was about>", "claims": [{{"text": "...", "category": "...", "skills": ["..."], "confidence": "observed|inferred", "significance": 3, "evidence": "<short paraphrase of what in the log supports this>"}}]}}

LOG:
{log}
"""


class ExtractionError(Exception):
    pass


def parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ExtractionError(f"no JSON in model output: {text[:200]!r}")
    return json.loads(text[start : end + 1])


def call_claude(prompt: str, model: str, claude_bin: str, workdir: str, timeout: int = 600) -> str:
    """Run one tool-less, non-persisted `claude -p` call and return its text result."""
    env = dict(os.environ, CAREER_MEMORY_WORKER="1")
    cmd = [claude_bin, "-p", "--output-format", "json", "--tools", "", "--no-session-persistence"]
    if model:
        cmd += ["--model", model]
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                              timeout=timeout, cwd=workdir, env=env)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ExtractionError(f"claude -p failed: {e}") from e
    if proc.returncode != 0:
        raise ExtractionError(f"claude -p exited {proc.returncode}: {(proc.stderr or proc.stdout)[:500]}")
    try:
        envelope = json.loads(proc.stdout)
    except ValueError as e:
        raise ExtractionError(f"bad claude output: {proc.stdout[:300]!r}") from e
    if envelope.get("is_error"):
        raise ExtractionError(f"claude error: {str(envelope.get('result'))[:300]}")
    return str(envelope.get("result", ""))


def _significance(v) -> int:
    try:
        return max(1, min(5, int(v)))
    except (TypeError, ValueError):
        return 2


def normalize(data: dict) -> dict:
    claims: List[dict] = []
    for c in data.get("claims") or []:
        if not isinstance(c, dict) or not str(c.get("text", "")).strip():
            continue
        claims.append({
            "text": str(c["text"]).strip(),
            "category": c.get("category") if c.get("category") in CATEGORIES else "contribution",
            "skills": [str(s) for s in (c.get("skills") or []) if s][:10],
            "confidence": "observed" if c.get("confidence") == "observed" else "inferred",
            "significance": _significance(c.get("significance")),
            "evidence": str(c.get("evidence", ""))[:500],
        })
    return {"summary": str(data.get("summary", ""))[:500], "claims": claims}


def extract(log: str, project: str, model: str, claude_bin: str, workdir: str, timeout: int = 600, guidance: str = "") -> dict:
    prompt = PROMPT.format(categories=", ".join(CATEGORIES), project=project or "unknown", log=log, guidance=guidance)
    return normalize(parse_json(call_claude(prompt, model, claude_bin, workdir, timeout)))
