#!/usr/bin/env python3
"""Career Memory local worker and CLI.

  cm.py setup --allow PATH [--backfill-days N]   opt in
  cm.py allow PATH | exclude PATH                manage capture scope
  cm.py run [--force] [--dry-run] [--limit N]    process finished episodes
  cm.py status | timeline | memory [PROJECT]       inspect what's remembered
  cm.py forget IDS | rebuild [PROJECT]            drop items / rebuild project memory from the log
  cm.py connect [URL] [TOKEN] | sync              link to the web app (browser approval), push memory
  cm.py work PATH --org NAME                     tag sessions under PATH as work for NAME
  cm.py instructions                             show/create ~/.career-memory/instructions.md
  cm.py uninstall --yes                          delete all local data
"""
from __future__ import annotations

import argparse
import fcntl
import glob
import json
import os
import shutil
import sys
import time
from collections import defaultdict
from datetime import datetime
from typing import List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import guidance  # noqa: E402
from extract import ExtractionError, call_claude, extract  # noqa: E402
from memory import merge, regroup, resolve  # noqa: E402
from scrub import scrub  # noqa: E402
from store import Store  # noqa: E402
from sync import SyncError, device_poll, device_start, sync, whoami  # noqa: E402
from transcript import condense, read_entries, split_episodes  # noqa: E402

HOME = os.environ.get("CAREER_MEMORY_HOME", os.path.expanduser("~/.career-memory"))
CLAUDE_PROJECTS = os.environ.get("CAREER_MEMORY_TRANSCRIPTS", os.path.expanduser("~/.claude/projects"))
CONFIG = os.path.join(HOME, "config.json")
MAX_CHUNK_CHARS = 40_000
DEFAULT_APP = os.environ.get("CAREER_MEMORY_APP", "https://career-memory.anshumaan.space")
CONNECT_WAIT = 100  # seconds to poll per run; stays under Claude Code's command timeout

DEFAULTS = {
    "enabled": True,
    "allowed_paths": [],
    "excluded_paths": [],
    "idle_minutes": 45,          # gap that ends a work episode
    "run_interval_hours": 12,    # worker runs at most this often
    "backfill_days": 0,          # how far before opt-in to read history
    "min_messages": 4,           # skip tiny episodes
    "model": "sonnet",
    "claude_bin": "",
    "work_paths": [],            # [{"path": "/Users/me/work", "org": "Acme"}]; else personal
    "api_url": "",
    "api_token": "",
    "min_significance": 3,       # 1-5; items below this stay in the local log only
}


# ---------- helpers ----------

def log(msg: str) -> None:
    print(f"{datetime.now().isoformat(timespec='seconds')} {msg}", flush=True)


def load_config() -> Optional[dict]:
    if not os.path.exists(CONFIG):
        return None
    with open(CONFIG) as f:
        return {**DEFAULTS, **json.load(f)}


def save_config(cfg: dict) -> None:
    os.makedirs(HOME, exist_ok=True)
    tmp = CONFIG + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG)


def require_config() -> dict:
    cfg = load_config()
    if not cfg:
        sys.exit("Career Memory is not set up. Run /career-memory:setup first.")
    return cfg


def under(path: str, roots: List[str]) -> bool:
    path = os.path.realpath(path) if path else ""
    return any(path == r or path.startswith(r.rstrip("/") + "/") for r in roots)


def git_root(path: str) -> Optional[str]:
    p = path
    while p and p != "/":
        if os.path.exists(os.path.join(p, ".git")):
            return p
        p = os.path.dirname(p)
    return None


def project_of(cwd: str) -> str:
    root = git_root(cwd)
    return os.path.basename(root or cwd) or "unknown"


def ignored(cwd: str, cfg: dict) -> Optional[str]:
    """Reason this episode must not be captured, or None."""
    if not cwd:
        return "no cwd"
    if under(cwd, [os.path.realpath(HOME)]):
        return "worker's own session"
    if not under(cwd, [os.path.realpath(p) for p in cfg["allowed_paths"]]):
        return "not in allowed paths"
    if under(cwd, [os.path.realpath(p) for p in cfg["excluded_paths"]]):
        return "excluded path"
    root = git_root(cwd)
    if root and os.path.exists(os.path.join(root, ".careerignore")):
        return ".careerignore"
    return None


def context_of(cwd: str, cfg: dict):
    """('work', org) for sessions under a configured work path, else ('personal', None)."""
    best = None
    for w in cfg.get("work_paths") or []:
        root = os.path.realpath(os.path.expanduser(w.get("path", "")))
        if under(cwd, [root]) and (best is None or len(root) > len(best[0])):
            best = (root, w.get("org") or None)
    return ("work", best[1]) if best else ("personal", None)


def chunks(text: str, size: int) -> List[str]:
    out, cur, n = [], [], 0
    for line in text.splitlines():
        if n + len(line) > size and cur:
            out.append("\n".join(cur))
            cur, n = [], 0
        cur.append(line)
        n += len(line) + 1
    if cur:
        out.append("\n".join(cur))
    return out


def transcripts(cfg: dict) -> List[str]:
    """Queued paths (from the Stop hook) plus a scan of all transcripts as recovery."""
    paths = set(glob.glob(os.path.join(CLAUDE_PROJECTS, "*", "*.jsonl")))
    queue = os.path.join(HOME, "queue.jsonl")
    if os.path.exists(queue):
        processing = queue + ".processing"
        os.replace(queue, processing)
        with open(processing) as f:
            for line in f:
                try:
                    p = json.loads(line).get("transcript_path")
                except ValueError:
                    continue
                if p and os.path.exists(p):
                    paths.add(p)
        os.remove(processing)
    return sorted(paths)


# ---------- run ----------

def cmd_run(args) -> None:
    cfg = require_config()
    if not cfg["enabled"]:
        return log("disabled; skipping")
    os.makedirs(os.path.join(HOME, "logs"), exist_ok=True)
    workdir = os.path.join(HOME, "tmp")
    os.makedirs(workdir, exist_ok=True)

    next_file = os.path.join(HOME, "next_run")
    now = time.time()
    if not (args.force or args.dry_run):
        try:
            if now < float(open(next_file).read().strip() or 0):
                return
        except (OSError, ValueError):
            pass

    lock = open(os.path.join(HOME, "worker.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return log("another run is in progress")

    if not args.dry_run:
        with open(next_file, "w") as f:
            f.write(str(int(now + cfg["run_interval_hours"] * 3600)))

    claude_bin = claude_bin_of(cfg)
    store = Store(os.path.join(HOME, "claims.db"))
    idle = cfg["idle_minutes"] * 60
    since = cfg.get("since_ts", 0)
    processed = added = failures = 0
    log(f"run started (dry_run={args.dry_run})")

    for path in transcripts(cfg):
        if args.limit and processed >= args.limit:
            break
        if failures >= 2:
            log("stopping after repeated extraction failures; will retry next run")
            break
        offset = store.offset(path)
        try:
            if os.path.getsize(path) < offset:
                offset = 0  # file was rewritten
            if os.path.getsize(path) == offset:
                continue
            entries, eof = read_entries(path, offset)
        except OSError as e:
            log(f"skip {path}: {e}")
            continue

        episodes = split_episodes(path, entries, idle)
        # The newest episode may still be in progress.
        if episodes and now - episodes[-1].end_ts < idle:
            open_ep = episodes.pop()
            safe_offset = open_ep.entries[0].start
        else:
            safe_offset = eof

        stopped = False
        for ep in episodes:
            if args.limit and processed >= args.limit:
                stopped = True
                break
            reason = None
            if ep.end_ts < since:
                reason = "before opt-in window"
            elif len(ep.entries) < cfg["min_messages"]:
                reason = "too short"
            elif store.has_episode(ep.id):
                reason = "already processed"
            else:
                reason = ignored(ep.cwd, cfg)
            if reason:
                if not args.dry_run:
                    store.set_offset(path, ep.end_offset)
                continue

            project = project_of(ep.cwd)
            text, redactions = scrub(condense(ep))
            when = datetime.fromtimestamp(ep.start_ts).strftime("%Y-%m-%d %H:%M")
            if args.dry_run:
                print(f"\n===== {project} · {when} · {len(ep.entries)} msgs · {redactions} redactions =====")
                print(text[:4000] + ("\n…" if len(text) > 4000 else ""))
                processed += 1
                continue

            try:
                summary, claims = "", []
                steer = guidance.load(HOME, git_root(ep.cwd))
                for part in chunks(text, MAX_CHUNK_CHARS):
                    res = extract(part, project, cfg["model"], claude_bin, workdir, guidance=steer)
                    summary = summary or res["summary"]
                    claims += res["claims"]
            except (ExtractionError, ValueError) as e:
                failures += 1
                log(f"extraction failed for {ep.id}: {e}")
                stopped = True  # keep offset here; retry next run
                break
            context, org = context_of(ep.cwd, cfg)
            n = store.save_episode(ep, project, redactions, "done", summary, claims, context, org)
            store.set_offset(path, ep.end_offset)
            processed += 1
            added += n
            log(f"{project} {when}: {n} new claims")

        if not stopped and not args.dry_run:
            store.set_offset(path, safe_offset)

    if not args.dry_run:
        if failures < 2:
            update_memories(store, cfg, claude_bin, workdir)
        write_timeline(store, cfg)
        if cfg.get("api_url") and cfg.get("api_token"):
            try:
                log(f"sync: {sync(cfg, store)}")
            except SyncError as e:
                log(f"sync failed (will retry next run): {e}")
    log(f"run finished: {processed} episodes, {added} new claims")


# ---------- memory ----------

def update_memories(store: Store, cfg: dict, claude_bin: str, workdir: str, only: Optional[str] = None) -> int:
    """Assign new workspaces to projects, then fold new significant claims into each project's living memory."""
    min_sig = int(cfg.get("min_significance") or 3)
    call = lambda prompt: call_claude(prompt, cfg["model"], claude_bin, workdir)
    if not only:
        resolve_workspaces(store, min_sig, call)
    updated = 0
    for project in store.unmerged_projects():
        if only and project != only:
            continue
        claims = store.unmerged(project, min_sig)
        if not claims:
            continue
        current = store.memory(project)
        try:
            res = merge(current["content"] if current else "", claims, project, claims[-1]["organization"], call,
                        guidance=guidance.load(HOME))
        except (ExtractionError, ValueError) as e:
            log(f"memory merge failed for {project}: {e}")
            continue  # claims stay unmerged; retried next run
        store.mark_merged([c["id"] for c in claims])
        store.save_memory(project, res["memory"], res["one_liner"], min_sig)
        updated += 1
        log(f"memory updated: {project} (+{len(claims)} items)")
    return updated


def resolve_workspaces(store: Store, min_sig: int, call) -> None:
    """A repo/folder seen for the first time joins an existing project when it's part of the same work."""
    pending = store.unresolved_workspaces()
    if not pending:
        return
    summaries = {s["workspace"]: s for s in store.workspace_summaries(min_sig)}
    for w in pending:
        existing = [m for m in store.memories() if m["project"] != w]
        if store.memory(w) and store.memory(w)["content"]:
            store.set_alias(w, w)  # already its own project
        elif not existing or w not in summaries:
            store.set_alias(w, w)
        else:
            try:
                project = resolve(summaries[w], existing, call, guidance=guidance.load(HOME))
            except (ExtractionError, ValueError) as e:
                log(f"project grouping failed for {w} (kept separate for now): {e}")
                continue
            store.set_alias(w, project)
            if project != w:
                log(f"grouped workspace {w} into project {project}")


def rebuild_projects(store: Store, cfg: dict, projects: List[str]) -> None:
    wd = os.path.join(HOME, "tmp"); os.makedirs(wd, exist_ok=True)
    for p in projects:
        store.reset_memory(p)
        n = update_memories(store, cfg, claude_bin_of(cfg), wd, only=p)
        print(f"{p}: {'rebuilt' if n else 'nothing significant to remember'}")


def try_sync(cfg: dict, store: Store) -> None:
    if not (cfg.get("api_url") and cfg.get("api_token")):
        return
    try:
        res = sync(cfg, store)
        print(f"Synced to the web app ({res['memories']} project memories updated).")
    except SyncError as e:
        print(f"Sync failed (will retry on the next run): {e}")


def cmd_regroup(args) -> None:
    """Re-cluster every workspace into projects with one Claude call, then rebuild all project memories."""
    cfg = require_config()
    store = Store(os.path.join(HOME, "claims.db"))
    min_sig = int(cfg.get("min_significance") or 3)
    summaries = store.workspace_summaries(min_sig)
    if not summaries:
        return print("Nothing remembered yet.")
    wd = os.path.join(HOME, "tmp"); os.makedirs(wd, exist_ok=True)
    mapping = regroup(summaries, lambda prompt: call_claude(prompt, cfg["model"], claude_bin_of(cfg), wd),
                      guidance=guidance.load(HOME))
    groups = defaultdict(list)
    for w, p in mapping.items():
        groups[p].append(w)
    print("Projects:")
    for p, ws in sorted(groups.items()):
        print(f"  {p}: {', '.join(sorted(ws))}")
    if args.dry_run:
        return print("(dry run: nothing changed)")
    old = [m["project"] for m in store.memories()]
    for w, p in mapping.items():
        store.set_alias(w, p, manual=bool(store.db.execute(
            "SELECT 1 FROM aliases WHERE workspace=? AND manual=1", (w,)).fetchone()))
    for p in old:
        if p not in groups:
            store.drop_memory(p)
    rebuild_projects(store, cfg, sorted(groups))
    write_timeline(store, cfg)
    try_sync(cfg, store)


def cmd_merge(args) -> None:
    """Fold one project into another (both by name, as shown by /career-memory:memory)."""
    cfg = require_config()
    store = Store(os.path.join(HOME, "claims.db"))
    src, dst = args.source, args.into
    workspaces = store.workspaces_of(src)
    if not workspaces:
        sys.exit(f"No project named {src!r}. Projects: {', '.join(m['project'] for m in store.memories())}")
    for w in workspaces:
        store.set_alias(w, dst, manual=True)
    store.drop_memory(src)
    print(f"Merged {src} into {dst}.")
    rebuild_projects(store, cfg, [dst])
    write_timeline(store, cfg)
    try_sync(cfg, store)


def claude_bin_of(cfg: dict) -> str:
    return cfg["claude_bin"] or shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")


def cmd_memory(args) -> None:
    require_config()
    store = Store(os.path.join(HOME, "claims.db"))
    rows = [m for m in store.memories() if not args.project or m["project"] == args.project]
    if not rows:
        return print("No project memory yet. It builds up after sessions are processed (/career-memory:run).")
    for m in rows:
        org = f" · {m['organization']}" if m["organization"] else ""
        ws = [w for w in store.workspaces_of(m["project"]) if w != m["project"]]
        also = f"\n(from: {', '.join(sorted(ws + [m['project']] if m['project'] in store.workspaces_of(m['project']) else ws))})" if ws else ""
        print(f"# {m['project']}{org}{also}\n\n{m['content']}\n")


def cmd_rebuild(args) -> None:
    cfg = require_config()
    store = Store(os.path.join(HOME, "claims.db"))
    projects = [args.project] if args.project else [m["project"] for m in store.memories()]
    rebuild_projects(store, cfg, projects)
    write_timeline(store, cfg)
    try_sync(cfg, store)


def cmd_forget(args) -> None:
    cfg = require_config()
    store = Store(os.path.join(HOME, "claims.db"))
    ids = parse_ids(args.ids)
    projects = store.projects_of(ids)
    n = store.set_status(ids, "rejected")
    print(f"Forgot {n} item(s).")
    if projects and not args.no_rebuild:
        args.project = None
        wd = os.path.join(HOME, "tmp"); os.makedirs(wd, exist_ok=True)
        for p in projects:
            store.reset_memory(p)
            update_memories(store, cfg, claude_bin_of(cfg), wd, only=p)
            print(f"Rebuilt memory for {p}.")
    write_timeline(store, cfg)


# ---------- timeline ----------

def write_timeline(store: Store, cfg: Optional[dict] = None) -> str:
    min_sig = int((cfg or {}).get("min_significance") or 3)
    by_month = defaultdict(lambda: defaultdict(list))
    for c in store.claims("approved"):
        if c["significance"] is not None and c["significance"] < min_sig:
            continue
        month = datetime.fromtimestamp(c["occurred_at"]).strftime("%Y-%m")
        by_month[month][c["project"]].append(c)
    lines = ["# Career Memory timeline", ""]
    for month in sorted(by_month, reverse=True):
        lines.append(f"## {datetime.strptime(month, '%Y-%m').strftime('%B %Y')}")
        for project, claims in sorted(by_month[month].items()):
            lines.append(f"\n### {project}")
            for c in claims:
                skills = ", ".join(json.loads(c["skills"] or "[]"))
                lines.append(f"- {c['text']}" + (f"  _({skills})_" if skills else "") + f"  `#{c['id']}`")
        lines.append("")
    path = os.path.join(HOME, "timeline.md")
    with open(path, "w") as f:
        f.write("\n".join(lines))
    return path


def parse_ids(ids: List[str]) -> List[int]:
    out = []
    for s in ids:
        for part in s.replace("#", "").split(","):
            if part.strip():
                out.append(int(part))
    return out


def cmd_timeline(args) -> None:
    cfg = require_config()
    path = write_timeline(Store(os.path.join(HOME, "claims.db")), cfg)
    print(open(path).read())


def cmd_status(args) -> None:
    cfg = load_config()
    if not cfg:
        return print("Not set up. Run /career-memory:setup.")
    counts = Store(os.path.join(HOME, "claims.db")).counts()
    try:
        nxt = datetime.fromtimestamp(float(open(os.path.join(HOME, "next_run")).read())).strftime("%Y-%m-%d %H:%M")
    except (OSError, ValueError):
        nxt = "on next Claude reply"
    print(json.dumps({
        "enabled": cfg["enabled"],
        "allowed_paths": cfg["allowed_paths"],
        "excluded_paths": cfg["excluded_paths"],
        "episodes_processed": counts.get("episodes", 0),
        "remembered_items": counts.get("approved", 0),
        "forgotten_items": counts.get("rejected", 0),
        "projects_in_memory": counts.get("projects", 0),
        "next_run": nxt,
        "connected_to": cfg.get("api_url") or None,
        "work_paths": cfg.get("work_paths", []),
        "data_dir": HOME,
    }, indent=2))


# ---------- scope ----------

def cmd_setup(args) -> None:
    cfg = load_config() or dict(DEFAULTS)
    path = os.path.realpath(os.path.expanduser(args.allow))
    if path not in cfg["allowed_paths"]:
        cfg["allowed_paths"].append(path)
    if args.backfill_days is not None:
        cfg["backfill_days"] = args.backfill_days
    if "since_ts" not in cfg or args.backfill_days is not None:
        cfg["since_ts"] = time.time() - cfg["backfill_days"] * 86400
    cfg["claude_bin"] = cfg.get("claude_bin") or shutil.which("claude") or ""
    cfg["enabled"] = True
    save_config(cfg)
    os.makedirs(os.path.join(HOME, "logs"), exist_ok=True)
    print(f"Career Memory enabled. Capturing sessions under: {', '.join(cfg['allowed_paths'])}")
    print(f"History window: from {datetime.fromtimestamp(cfg['since_ts']).strftime('%Y-%m-%d')}. "
          f"Worker runs at most every {cfg['run_interval_hours']}h. Data: {HOME}")


def cmd_scope(key: str, add: bool = True):
    def run(args) -> None:
        cfg = require_config()
        path = os.path.realpath(os.path.expanduser(args.path))
        if add and path not in cfg[key]:
            cfg[key].append(path)
        if not add and path in cfg[key]:
            cfg[key].remove(path)
        save_config(cfg)
        print(f"{key}: {cfg[key]}")
    return run


def cmd_pause(enabled: bool):
    def run(args) -> None:
        cfg = require_config()
        cfg["enabled"] = enabled
        save_config(cfg)
        print("Capture " + ("resumed." if enabled else "paused."))
    return run


def _finish_connect(cfg: dict, url: str, token: str) -> None:
    cfg["api_url"], cfg["api_token"] = url, token
    cfg.pop("pending_connect", None)
    try:
        me = whoami(cfg)
    except SyncError as e:
        sys.exit(f"Could not connect: {e}")
    save_config(cfg)
    print(f"Connected to {cfg['api_url']} as {me.get('email')}.")
    res = sync(cfg, Store(os.path.join(HOME, "claims.db")))
    print(f"Synced {res['sent']} item(s) and {res['memories']} project memories. See {cfg['api_url']}/memory")


def _callback_server():
    """A one-shot HTTP server on 127.0.0.1 that receives the browser's hand-off after approval."""
    import http.server
    import threading
    from urllib.parse import parse_qs, urlparse

    got = {}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = parse_qs(urlparse(self.path).query)
            if urlparse(self.path).path == "/callback" and q.get("code"):
                got["code"] = q["code"][0]
                done.set()
                body = b"<html><body style='font-family:system-ui;padding:40px'><h3>Connected</h3><p>You can close this tab and go back to Claude Code.</p></body></html>"
                self.send_response(200)
            else:
                body = b"Not found"
                self.send_response(404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, got, done


def cmd_connect(args) -> None:
    cfg = require_config()
    url = (args.url or cfg.get("api_url") or DEFAULT_APP).rstrip("/")
    if args.token:  # manual token from Settings → API tokens
        return _finish_connect(cfg, url, args.token)
    srv, got, done = _callback_server()
    try:
        try:
            d = device_start(url, srv.server_address[1])
        except SyncError as e:
            sys.exit(f"Could not connect: {e}")
        opened = False
        try:
            import webbrowser
            opened = webbrowser.open(d["verification_uri"])
        except Exception:
            pass
        print(("Opened your browser. " if opened else "") + f"Approve the connection at: {d['verification_uri']}")
        sys.stdout.flush()
        if not done.wait(CONNECT_WAIT):
            sys.exit("Timed out waiting for approval. Run /career-memory:connect again.")
        try:
            token = device_poll(url, d["device_code"], got["code"])
        except SyncError as e:
            sys.exit(f"Could not connect: {e}. Run /career-memory:connect again.")
        if not token:
            sys.exit("Approval didn't complete. Run /career-memory:connect again.")
        cfg.pop("pending_connect", None)
        _finish_connect(cfg, url, token)
    finally:
        srv.shutdown()


def cmd_sync(args) -> None:
    cfg = require_config()
    if not (cfg.get("api_url") and cfg.get("api_token")):
        sys.exit("Not connected. Run /career-memory:connect")
    try:
        res = sync(cfg, Store(os.path.join(HOME, "claims.db")))
    except SyncError as e:
        sys.exit(f"Sync failed: {e}")
    print(f"Sent {res['sent']} item(s) and {res['memories']} project memories, removed {res['deleted']}.")


def cmd_work(args) -> None:
    cfg = require_config()
    path = os.path.realpath(os.path.expanduser(args.path))
    cfg["work_paths"] = [w for w in cfg.get("work_paths", []) if os.path.realpath(os.path.expanduser(w["path"])) != path]
    if not args.remove:
        cfg["work_paths"].append({"path": path, "org": args.org})
    save_config(cfg)
    print("Work paths:", json.dumps(cfg["work_paths"], indent=2))


def cmd_instructions(args) -> None:
    p = guidance.ensure(HOME)
    active = guidance.load(HOME)
    print(f"Instructions file: {p}")
    print(f"Per-repo: add a {guidance.REPO_FILE} at a repo root (applies to sessions in that repo).")
    print("Active instructions:" if active else "No active instructions yet (only the commented examples).")
    if active:
        with open(p, encoding="utf-8") as f:
            print(guidance._clean(f.read()))


def cmd_uninstall(args) -> None:
    if not args.yes:
        sys.exit(f"This deletes {HOME} (all claims and settings). Re-run with --yes.")
    shutil.rmtree(HOME, ignore_errors=True)
    print(f"Deleted {HOME}. Remove the plugin with: /plugin uninstall career-memory")


def main() -> None:
    p = argparse.ArgumentParser(prog="cm.py", description="Career Memory local worker")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("setup"); s.add_argument("--allow", required=True)
    s.add_argument("--backfill-days", type=int); s.set_defaults(fn=cmd_setup)
    for name, key, add in (("allow", "allowed_paths", True), ("disallow", "allowed_paths", False),
                           ("exclude", "excluded_paths", True), ("unexclude", "excluded_paths", False)):
        s = sub.add_parser(name); s.add_argument("path"); s.set_defaults(fn=cmd_scope(key, add))
    s = sub.add_parser("run"); s.add_argument("--force", action="store_true")
    s.add_argument("--dry-run", action="store_true", help="print what would be sent to the LLM; change nothing")
    s.add_argument("--limit", type=int, default=0); s.set_defaults(fn=cmd_run)
    s = sub.add_parser("memory"); s.add_argument("project", nargs="?"); s.set_defaults(fn=cmd_memory)
    s = sub.add_parser("rebuild"); s.add_argument("project", nargs="?"); s.set_defaults(fn=cmd_rebuild)
    s = sub.add_parser("regroup"); s.add_argument("--dry-run", action="store_true"); s.set_defaults(fn=cmd_regroup)
    s = sub.add_parser("merge"); s.add_argument("source"); s.add_argument("into"); s.set_defaults(fn=cmd_merge)
    s = sub.add_parser("forget"); s.add_argument("ids", nargs="+", help="ids like 3 5,6 (from the timeline)")
    s.add_argument("--no-rebuild", action="store_true"); s.set_defaults(fn=cmd_forget)
    sub.add_parser("timeline").set_defaults(fn=cmd_timeline)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("pause").set_defaults(fn=cmd_pause(False))
    sub.add_parser("resume").set_defaults(fn=cmd_pause(True))
    s = sub.add_parser("connect"); s.add_argument("url", nargs="?"); s.add_argument("token", nargs="?"); s.set_defaults(fn=cmd_connect)
    sub.add_parser("sync").set_defaults(fn=cmd_sync)
    sub.add_parser("instructions").set_defaults(fn=cmd_instructions)
    s = sub.add_parser("work"); s.add_argument("path"); s.add_argument("--org", default=None)
    s.add_argument("--remove", action="store_true"); s.set_defaults(fn=cmd_work)
    s = sub.add_parser("uninstall"); s.add_argument("--yes", action="store_true"); s.set_defaults(fn=cmd_uninstall)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
