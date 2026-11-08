"""Send claims to the Career Memory backend (docs/api.md). Stdlib only."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Optional


class SyncError(Exception):
    pass


def _request(cfg: dict, method: str, path: str, body: Optional[dict] = None) -> Optional[dict]:
    url = cfg["api_url"].rstrip("/") + "/api/v1" + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {cfg['api_token']}",
        "Content-Type": "application/json",
        "User-Agent": "career-memory-plugin",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        if e.code == 401:
            raise SyncError("token rejected (401). Create a new one in Settings and run /career-memory:connect") from e
        raise SyncError(f"HTTP {e.code}: {detail}") from e
    except (urllib.error.URLError, OSError) as e:
        raise SyncError(f"cannot reach {url}: {e}") from e


def _post_public(base: str, path: str, body: dict) -> tuple:
    """Unauthenticated POST (device connect). Returns (status, json)."""
    req = urllib.request.Request(base.rstrip("/") + "/api/v1" + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "career-memory-plugin"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, OSError) as e:
        raise SyncError(f"cannot reach {base}: {e}") from e


def device_start(base: str, port: Optional[int] = None) -> dict:
    status, data = _post_public(base, "/device/start", {"port": port} if port else {})
    if status != 200 or not data.get("device_code"):
        raise SyncError(f"could not start connecting (HTTP {status})")
    return data


def device_poll(base: str, device_code: str, exchange: Optional[str] = None) -> Optional[str]:
    """The API token once approved, None while pending. Raises SyncError when expired."""
    body = {"device_code": device_code}
    if exchange:
        body["exchange"] = exchange
    status, data = _post_public(base, "/device/token", body)
    if status == 200 and data.get("token"):
        return data["token"]
    if status == 202:
        return None
    raise SyncError("the connect request expired")


def whoami(cfg: dict) -> dict:
    return _request(cfg, "GET", "/me") or {}


def _payload(row) -> dict:
    return {
        "client_id": f"local:{row['hash'][:24]}",
        "text": row["text"][:600],
        "category": row["category"] or "contribution",
        "skills": json.loads(row["skills"] or "[]")[:20],
        "project": row["project"],
        "organization": row["organization"],
        "context": row["context"] or "unknown",
        "confidence": row["confidence"] or "inferred",
        "significance": row["significance"],
        "status": "approved",
        "evidence": (row["evidence"] or "")[:1000] or None,
        "occurred_at": datetime.fromtimestamp(row["occurred_at"], timezone.utc).isoformat() if row["occurred_at"] else None,
        "source_ref": {"episode_id": row["episode_id"]},
    }


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else None


def _memory_payload(m) -> dict:
    return {
        "key": f"local:{m['project']}"[:128],
        "project": m["project"],
        "organization": m["organization"],
        "context": m["context"] or "unknown",
        "content_md": m["content"],  # empty = forgotten / being rebuilt
        "one_liner": m["one_liner"],
        "claim_count": m["claim_count"] or 0,
        "first_at": _iso(m["first_at"]),
        "last_at": _iso(m["last_at"]),
    }


def sync(cfg: dict, store) -> dict:
    """Push new claims and project memories; delete claims forgotten locally. Returns counts."""
    rows = store.unsynced(int(cfg.get("min_significance") or 3))
    sent = 0
    for i in range(0, len(rows), 100):
        batch = rows[i : i + 100]
        _request(cfg, "POST", "/claims/sync", {"claims": [_payload(r) for r in batch]})
        store.mark_synced([r["id"] for r in batch])
        sent += len(batch)
    deleted = 0
    for r in store.to_delete_remotely():
        _request(cfg, "DELETE", f"/claims/local:{r['hash'][:24]}")
        store.mark_remote_deleted([r["id"]])
        deleted += 1
    mems = store.unsynced_memories()
    if mems:
        _request(cfg, "POST", "/memory", {"memories": [_memory_payload(m) for m in mems]})
        store.mark_memories_synced([m["project"] for m in mems])
    return {"sent": sent, "deleted": deleted, "memories": len(mems)}
