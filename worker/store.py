"""Local SQLite store: file offsets, processed episodes, claims (the log) and project memories."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from typing import List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    offset INTEGER NOT NULL DEFAULT 0,
    updated_at REAL
);
CREATE TABLE IF NOT EXISTS episodes (
    id TEXT PRIMARY KEY,
    session_id TEXT, transcript TEXT, cwd TEXT, project TEXT,
    start_ts REAL, end_ts REAL, messages INTEGER, redactions INTEGER,
    summary TEXT, status TEXT, processed_at REAL
);
CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id TEXT REFERENCES episodes(id),
    hash TEXT UNIQUE,
    text TEXT NOT NULL, category TEXT, project TEXT, skills TEXT,
    confidence TEXT,            -- observed | inferred
    evidence TEXT,
    status TEXT NOT NULL DEFAULT 'approved',  -- approved (remembered) | rejected (forgotten)
    occurred_at REAL, created_at REAL
);
-- A workspace (git repo or folder) maps to the human-level project it belongs to.
CREATE TABLE IF NOT EXISTS aliases (
    workspace TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    manual INTEGER NOT NULL DEFAULT 0,   -- set by the user (merge); regroup keeps these
    created_at REAL
);
CREATE TABLE IF NOT EXISTS memories (
    project TEXT PRIMARY KEY,
    organization TEXT, context TEXT,
    content TEXT NOT NULL DEFAULT '',
    one_liner TEXT,
    claim_count INTEGER NOT NULL DEFAULT 0,
    first_at REAL, last_at REAL,
    updated_at REAL, synced_at REAL
);
"""


class Store:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(claims)")}
        for name, ddl in (
            ("context", "TEXT DEFAULT 'unknown'"),   # work | personal | unknown
            ("organization", "TEXT"),
            ("synced_at", "REAL"),                    # null = needs (re)sync
            ("remote_deleted", "INTEGER DEFAULT 0"),
            ("significance", "INTEGER"),              # 1-5; null = extracted before scoring existed
            ("merged", "INTEGER DEFAULT 0"),          # folded into project memory
            ("workspace", "TEXT"),                    # repo/folder it came from; `project` is the resolved project
        ):
            if name not in cols:
                self.db.execute(f"ALTER TABLE claims ADD COLUMN {name} {ddl}")
        self.db.execute("UPDATE claims SET workspace=project WHERE workspace IS NULL")
        # No review step any more: everything not forgotten is remembered.
        self.db.execute("UPDATE claims SET status='approved', synced_at=NULL WHERE status='pending'")
        self.db.commit()

    # ---------- offsets / episodes ----------

    def offset(self, path: str) -> int:
        row = self.db.execute("SELECT offset FROM files WHERE path=?", (path,)).fetchone()
        return row["offset"] if row else 0

    def set_offset(self, path: str, offset: int) -> None:
        self.db.execute(
            "INSERT INTO files(path, offset, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET offset=excluded.offset, updated_at=excluded.updated_at",
            (path, offset, time.time()),
        )
        self.db.commit()

    def has_episode(self, episode_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM episodes WHERE id=?", (episode_id,)).fetchone() is not None

    def save_episode(self, ep, workspace: str, redactions: int, status: str,
                     summary: str = "", claims: Optional[List[dict]] = None,
                     context: str = "unknown", organization: Optional[str] = None) -> int:
        now = time.time()
        project = self.alias(workspace) or workspace  # provisional until resolved
        self.db.execute(
            "INSERT OR REPLACE INTO episodes VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (ep.id, ep.session_id, ep.path, ep.cwd, workspace, ep.start_ts, ep.end_ts,
             len(ep.entries), redactions, summary, status, now),
        )
        added = 0
        for c in claims or []:
            h = hashlib.sha256(f"{workspace}|{c['text'].lower()}".encode()).hexdigest()
            cur = self.db.execute(
                "INSERT OR IGNORE INTO claims(episode_id, hash, text, category, project, skills, "
                "confidence, evidence, occurred_at, created_at, context, organization, significance, workspace) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ep.id, h, c["text"], c["category"], project, json.dumps(c["skills"]),
                 c["confidence"], c["evidence"], ep.end_ts, now, context, organization,
                 c.get("significance"), workspace),
            )
            added += cur.rowcount
        self.db.commit()
        return added

    # ---------- claims ----------

    def claims(self, status: Optional[str] = None) -> List[sqlite3.Row]:
        q = "SELECT * FROM claims"
        args: tuple = ()
        if status:
            q += " WHERE status=?"
            args = (status,)
        return self.db.execute(q + " ORDER BY occurred_at, id", args).fetchall()

    def set_status(self, ids: List[int], status: str) -> int:
        # Clearing synced_at queues the change for the next sync.
        cur = self.db.executemany("UPDATE claims SET status=?, synced_at=NULL WHERE id=?", [(status, i) for i in ids])
        self.db.commit()
        return cur.rowcount

    def projects_of(self, ids: List[int]) -> List[str]:
        marks = ",".join("?" * len(ids)) or "NULL"
        return [r[0] for r in self.db.execute(f"SELECT DISTINCT project FROM claims WHERE id IN ({marks})", tuple(ids))]

    def counts(self) -> dict:
        out = {r["status"]: r["n"] for r in self.db.execute(
            "SELECT status, COUNT(*) n FROM claims GROUP BY status")}
        out["episodes"] = self.db.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        out["failed_episodes"] = self.db.execute(
            "SELECT COUNT(*) FROM episodes WHERE status='failed'").fetchone()[0]
        out["projects"] = self.db.execute("SELECT COUNT(*) FROM memories WHERE content != ''").fetchone()[0]
        return out

    # ---------- projects (workspace -> project) ----------

    def alias(self, workspace: str) -> Optional[str]:
        row = self.db.execute("SELECT project FROM aliases WHERE workspace=?", (workspace,)).fetchone()
        return row["project"] if row else None

    def aliases(self) -> List[sqlite3.Row]:
        return self.db.execute("SELECT * FROM aliases ORDER BY project, workspace").fetchall()

    def set_alias(self, workspace: str, project: str, manual: bool = False) -> None:
        """Map a workspace to a project and move its claims there (changed claims re-sync with the new name)."""
        self.db.execute(
            "INSERT INTO aliases(workspace, project, manual, created_at) VALUES(?,?,?,?) "
            "ON CONFLICT(workspace) DO UPDATE SET project=excluded.project, manual=excluded.manual",
            (workspace, project, int(manual), time.time()))
        self.db.execute("UPDATE claims SET project=?, synced_at=NULL WHERE workspace=? AND project!=? AND status='approved'",
                        (project, workspace, project))
        self.db.execute("UPDATE claims SET project=? WHERE workspace=? AND project!=?", (project, workspace, project))
        self.db.commit()

    def clear_auto_aliases(self) -> None:
        self.db.execute("DELETE FROM aliases WHERE manual=0")
        self.db.commit()

    def unresolved_workspaces(self) -> List[str]:
        """Workspaces with new remembered work that haven't been assigned to a project yet."""
        return [r[0] for r in self.db.execute(
            "SELECT DISTINCT workspace FROM claims WHERE merged=0 AND status='approved' "
            "AND workspace NOT IN (SELECT workspace FROM aliases) ORDER BY workspace")]

    def workspaces_of(self, project: str) -> List[str]:
        return [r[0] for r in self.db.execute("SELECT DISTINCT workspace FROM claims WHERE project=?", (project,))]

    def workspace_summaries(self, min_sig: int, samples: int = 8) -> List[dict]:
        """Per workspace: date range, organization and its most significant items (for grouping)."""
        out = []
        for w in [r[0] for r in self.db.execute(
                "SELECT DISTINCT workspace FROM claims WHERE status='approved' ORDER BY workspace")]:
            st = self.db.execute(
                "SELECT COUNT(*) n, MIN(occurred_at) a, MAX(occurred_at) b, MAX(organization) org FROM claims "
                "WHERE workspace=? AND status='approved' AND (significance IS NULL OR significance >= ?)",
                (w, min_sig)).fetchone()
            if not st["n"]:
                continue
            items = [r[0] for r in self.db.execute(
                "SELECT text FROM claims WHERE workspace=? AND status='approved' AND (significance IS NULL OR significance >= ?) "
                "ORDER BY COALESCE(significance, 3) DESC, occurred_at DESC LIMIT ?", (w, min_sig, samples))]
            out.append({"workspace": w, "count": st["n"], "first_at": st["a"], "last_at": st["b"],
                        "organization": st["org"], "items": items, "manual": next(iter(self.db.execute(
                            "SELECT project FROM aliases WHERE workspace=? AND manual=1", (w,)).fetchone() or []), None)})
        return out

    # ---------- memory ----------

    def unmerged_projects(self) -> List[str]:
        return [r[0] for r in self.db.execute(
            "SELECT DISTINCT project FROM claims WHERE merged=0 AND status='approved' ORDER BY project")]

    def unmerged(self, project: str, min_sig: int) -> List[sqlite3.Row]:
        """Unmerged remembered claims for a project. Low-significance ones are marked merged and skipped."""
        self.db.execute(
            "UPDATE claims SET merged=1 WHERE project=? AND merged=0 AND significance IS NOT NULL AND significance < ?",
            (project, min_sig))
        self.db.commit()
        return self.db.execute(
            "SELECT * FROM claims WHERE project=? AND merged=0 AND status='approved' ORDER BY occurred_at, id",
            (project,)).fetchall()

    def significant(self, project: str, min_sig: int) -> List[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM claims WHERE project=? AND status='approved' "
            "AND (significance IS NULL OR significance >= ?) ORDER BY occurred_at, id",
            (project, min_sig)).fetchall()

    def mark_merged(self, ids: List[int]) -> None:
        self.db.executemany("UPDATE claims SET merged=1 WHERE id=?", [(i,) for i in ids])
        self.db.commit()

    def memory(self, project: str) -> Optional[sqlite3.Row]:
        return self.db.execute("SELECT * FROM memories WHERE project=?", (project,)).fetchone()

    def memories(self) -> List[sqlite3.Row]:
        return self.db.execute("SELECT * FROM memories WHERE content != '' ORDER BY last_at DESC").fetchall()

    def save_memory(self, project: str, content: str, one_liner: str, min_sig: int = 3) -> None:
        stats = self.db.execute(
            "SELECT COUNT(*) n, MIN(occurred_at) a, MAX(occurred_at) b, MAX(organization) org, MAX(context) ctx "
            "FROM claims WHERE project=? AND status='approved' AND merged=1 "
            "AND (significance IS NULL OR significance >= ?)", (project, min_sig)).fetchone()
        self.db.execute(
            "INSERT INTO memories(project, organization, context, content, one_liner, claim_count, first_at, last_at, updated_at, synced_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,NULL) ON CONFLICT(project) DO UPDATE SET organization=excluded.organization, "
            "context=excluded.context, content=excluded.content, one_liner=excluded.one_liner, claim_count=excluded.claim_count, "
            "first_at=excluded.first_at, last_at=excluded.last_at, updated_at=excluded.updated_at, synced_at=NULL",
            (project, stats["org"], stats["ctx"] or "unknown", content, one_liner, stats["n"], stats["a"], stats["b"], time.time()))
        self.db.commit()

    def drop_memory(self, project: str) -> None:
        """Blank a project's memory; the next sync removes it from the web app."""
        self.db.execute("UPDATE memories SET content='', one_liner=NULL, claim_count=0, synced_at=NULL WHERE project=?", (project,))
        self.db.commit()

    def reset_memory(self, project: str) -> None:
        self.db.execute("UPDATE claims SET merged=0 WHERE project=?", (project,))
        # synced_at=NULL: if the rebuild finds nothing to remember, the next sync removes the stale copy.
        self.db.execute("UPDATE memories SET content='', one_liner=NULL, synced_at=NULL WHERE project=?", (project,))
        self.db.commit()

    # ---------- sync ----------

    def unsynced(self, min_sig: int) -> List[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM claims WHERE synced_at IS NULL AND status='approved' "
            "AND (significance IS NULL OR significance >= ?) ORDER BY id", (min_sig,)).fetchall()

    def to_delete_remotely(self) -> List[sqlite3.Row]:
        """Claims forgotten locally since the last sync (deleting a never-sent one is a harmless no-op)."""
        return self.db.execute(
            "SELECT * FROM claims WHERE status='rejected' AND remote_deleted=0 AND synced_at IS NULL"
        ).fetchall()

    def mark_synced(self, ids: List[int]) -> None:
        self.db.executemany("UPDATE claims SET synced_at=? WHERE id=?", [(time.time(), i) for i in ids])
        self.db.commit()

    def mark_remote_deleted(self, ids: List[int]) -> None:
        self.db.executemany("UPDATE claims SET remote_deleted=1, synced_at=? WHERE id=?", [(time.time(), i) for i in ids])
        self.db.commit()

    def unsynced_memories(self) -> List[sqlite3.Row]:
        return self.db.execute("SELECT * FROM memories WHERE synced_at IS NULL").fetchall()

    def mark_memories_synced(self, projects: List[str]) -> None:
        self.db.executemany("UPDATE memories SET synced_at=? WHERE project=?", [(time.time(), p) for p in projects])
        self.db.commit()
