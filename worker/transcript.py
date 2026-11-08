"""Read Claude Code JSONL transcripts incrementally and condense them.

Transcripts are append-only. We read from a saved byte offset, keep only
user/assistant messages, split them into work episodes at idle gaps, and
condense each episode to prose + action metadata (no code, no tool output).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import List, Tuple


@dataclass
class Entry:
    start: int  # byte offset where the line starts
    end: int  # byte offset just after the line
    ts: float
    role: str  # "user" | "assistant"
    cwd: str
    session_id: str
    content: object


@dataclass
class Episode:
    path: str
    entries: List[Entry]

    @property
    def session_id(self) -> str:
        return self.entries[0].session_id

    @property
    def cwd(self) -> str:
        return self.entries[0].cwd

    @property
    def start_ts(self) -> float:
        return self.entries[0].ts

    @property
    def end_ts(self) -> float:
        return self.entries[-1].ts

    @property
    def end_offset(self) -> int:
        return self.entries[-1].end

    @property
    def id(self) -> str:
        return f"{self.session_id}:{int(self.start_ts)}"


def parse_ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def read_entries(path: str, offset: int) -> Tuple[List[Entry], int]:
    """Return messages after `offset` and the offset of the last complete line."""
    entries: List[Entry] = []
    pos = offset
    with open(path, "rb") as f:
        f.seek(offset)
        for raw in f:
            if not raw.endswith(b"\n"):
                break  # line still being written
            start, pos = pos, pos + len(raw)
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if obj.get("type") not in ("user", "assistant"):
                continue
            if obj.get("isSidechain") or obj.get("isMeta"):
                continue
            ts = obj.get("timestamp")
            if not ts:
                continue
            msg = obj.get("message") or {}
            entries.append(
                Entry(start, pos, parse_ts(ts), obj["type"], obj.get("cwd", ""),
                      obj.get("sessionId", ""), msg.get("content"))
            )
    return entries, pos


def split_episodes(path: str, entries: List[Entry], idle_seconds: float) -> List[Episode]:
    episodes: List[Episode] = []
    current: List[Entry] = []
    for e in entries:
        if current and e.ts - current[-1].ts > idle_seconds:
            episodes.append(Episode(path, current))
            current = []
        current.append(e)
    if current:
        episodes.append(Episode(path, current))
    return episodes


# ---------- condensing ----------

_TAG_BLOCK = re.compile(
    r"<(system-reminder|command-[a-z-]+|local-command-[a-z-]+|ide_[a-z_]+|task-notification)\b[^>]*>.*?</\1>",
    re.S,
)
_PASTED = re.compile(r"<pasted_content\b[^>]*>.*?</pasted_content[^>]*>", re.S)
_FENCE = re.compile(r"```.*?(```|$)", re.S)
_WS = re.compile(r"\n{3,}")

MAX_MSG_CHARS = 1500


def _clean(text: str) -> str:
    text = _PASTED.sub("[pasted content omitted]", text)
    text = _TAG_BLOCK.sub("", text)
    text = _FENCE.sub("[code omitted]", text)
    text = _WS.sub("\n\n", text).strip()
    if len(text) > MAX_MSG_CHARS:
        text = text[:MAX_MSG_CHARS] + " …[truncated]"
    return text


def _describe_tool(block: dict) -> str:
    name = block.get("name", "tool")
    inp = block.get("input") or {}
    if not isinstance(inp, dict):
        return name
    for key in ("file_path", "path", "notebook_path", "pattern", "url", "description"):
        if inp.get(key):
            return f"{name} {str(inp[key])[:150]}"
    if inp.get("command"):
        first = str(inp["command"]).strip().splitlines()[0] if str(inp["command"]).strip() else ""
        return f"{name}: {first[:150]}"
    return name


def condense(episode: Episode) -> str:
    """Prose + action metadata only. Tool outputs, file contents and code are dropped."""
    lines: List[str] = []
    for e in episode.entries:
        content = e.content
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else (content or [])
        for b in blocks:
            if not isinstance(b, dict):
                continue
            kind = b.get("type")
            if kind == "text":
                text = _clean(b.get("text", ""))
                if text:
                    lines.append(f"[{e.role.upper()}] {text}")
            elif kind == "tool_use" and e.role == "assistant":
                lines.append(f"[ACTION] {_describe_tool(b)}")
            # tool_result, thinking, images: dropped on purpose
    return "\n".join(lines)
