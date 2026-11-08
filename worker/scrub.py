"""Local redaction of secrets and personal data before any LLM sees the text."""
from __future__ import annotations

import math
import os
import re
from collections import Counter
from typing import List, Tuple

_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[PRIVATE_KEY]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[AWS_KEY]"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b|\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "[GITHUB_TOKEN]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), "[API_KEY]"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), "[SLACK_TOKEN]"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "[GOOGLE_KEY]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "[JWT]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer [TOKEN]"),
    (re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|auth)(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|\S+)"),
     r"\1\2[REDACTED]"),
    (re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s)\]>\"']+", re.I), "[URL]"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[EMAIL]"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[IP]"),
]

# No "/" so long file paths are judged segment by segment, not redacted whole.
_CANDIDATE = re.compile(r"[A-Za-z0-9+_=-]{32,}")


def _entropy(s: str) -> float:
    counts = Counter(s)
    return -sum(c / len(s) * math.log2(c / len(s)) for c in counts.values())


def _looks_random(s: str) -> bool:
    # Long random-looking strings (keys, hashes) go; long words/paths stay.
    if not any(c.isdigit() for c in s):
        return False
    if all(c in "0123456789abcdefABCDEF" for c in s):
        return True  # hex keys/hashes (entropy tops out at 4.0)
    return _entropy(s) > 4.0


def scrub(text: str) -> Tuple[str, int]:
    """Return (redacted text, number of redactions)."""
    total = 0
    home = os.path.expanduser("~")
    if home and home != "/":
        text = text.replace(home, "~")
    for pattern, repl in _PATTERNS:
        text, n = pattern.subn(repl, text)
        total += n
    hits = [0]

    def _sub(m: re.Match) -> str:
        if _looks_random(m.group(0)):
            hits[0] += 1
            return "[REDACTED]"
        return m.group(0)

    text = _CANDIDATE.sub(_sub, text)
    return text, total + hits[0]
