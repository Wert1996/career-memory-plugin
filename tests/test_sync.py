import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "worker"))

from store import Store  # noqa: E402
from sync import SyncError, sync  # noqa: E402
import cm  # noqa: E402


class FakeEpisode:
    id = "s1:1"; session_id = "s1"; path = "/t.jsonl"; cwd = "/repo"
    start_ts = 1762000000.0; end_ts = 1762003600.0; entries = [1, 2, 3, 4]


class Handler(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *a):
        pass

    def _reply(self, code, body=None):
        self.send_response(code)
        self.end_headers()
        if body is not None:
            self.wfile.write(json.dumps(body).encode())

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Handler.calls.append(("POST", self.path, self.headers["Authorization"], body))
        if self.headers["Authorization"] != "Bearer cm_good":
            return self._reply(401, {"error": "bad"})
        self._reply(200, {"upserted": len(body.get("claims") or body.get("memories")), "ids": {}})

    def do_DELETE(self):
        Handler.calls.append(("DELETE", self.path, self.headers["Authorization"], None))
        self._reply(204)


class SyncTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        Handler.calls.clear()
        self.store = Store(os.path.join(tempfile.mkdtemp(), "c.db"))
        claim = {"text": "Built X", "category": "contribution", "skills": ["Go"], "confidence": "observed", "evidence": "e"}
        self.store.save_episode(FakeEpisode(), "proj", 0, "done", "", [claim, dict(claim, text="Built Y")], "work", "Acme")

    def test_sync_sends_once_then_deletes_rejected(self):
        cfg = {"api_url": self.url, "api_token": "cm_good"}
        self.assertEqual(sync(cfg, self.store), {"sent": 2, "deleted": 0, "memories": 0})
        sent = Handler.calls[0][3]["claims"]
        self.assertEqual(sent[0]["organization"], "Acme")
        self.assertEqual(sent[0]["context"], "work")
        self.assertTrue(sent[0]["client_id"].startswith("local:"))
        self.assertEqual(sync(cfg, self.store), {"sent": 0, "deleted": 0, "memories": 0})  # idempotent

        ids = [r["id"] for r in self.store.claims()]
        self.store.set_status([ids[0]], "rejected")
        self.store.set_status([ids[1]], "approved")
        self.assertEqual(sync(cfg, self.store), {"sent": 1, "deleted": 1, "memories": 0})
        self.assertEqual(Handler.calls[-1][0], "DELETE")

    def test_memory_merge_and_sync(self):
        low = {"text": "Fixed a typo", "category": "contribution", "skills": [], "confidence": "observed",
               "evidence": "", "significance": 1}
        ep = FakeEpisode(); ep.id = "s1:2"
        self.store.save_episode(ep, "proj", 0, "done", "", [low], "work", "Acme")
        prompts = []

        def fake_call(prompt):
            prompts.append(prompt)
            return json.dumps({"memory": "## Built\n- Built X and Y", "one_liner": "Built X"})

        from memory import merge
        claims = self.store.unmerged("proj", 3)
        self.assertEqual([c["text"] for c in claims], ["Built X", "Built Y"])  # typo filtered out
        res = merge("", claims, "proj", "Acme", fake_call)
        self.assertNotIn("Fixed a typo", prompts[0])
        self.store.mark_merged([c["id"] for c in claims])
        self.store.save_memory("proj", res["memory"], res["one_liner"])
        self.assertEqual(self.store.unmerged_projects(), [])
        cfg = {"api_url": self.url, "api_token": "cm_good"}
        self.assertEqual(sync(cfg, self.store)["memories"], 1)
        post = [c for c in Handler.calls if c[1] == "/api/v1/memory"][0][3]["memories"][0]
        self.assertEqual((post["project"], post["organization"], post["claim_count"]), ("proj", "Acme", 2))
        self.assertNotIn("typo", json.dumps([c[3] for c in Handler.calls]))  # low-significance never leaves

    def test_new_workspace_joins_existing_project(self):
        cfg = {"api_url": self.url, "api_token": "cm_good", "min_significance": 3}
        # "proj" already has a memory; a second repo shows up with related work.
        self.store.mark_merged([c["id"] for c in self.store.claims()])
        self.store.save_memory("proj", "## Built\n- Built X (2025-11)", "Built X")
        self.store.set_alias("proj", "proj")
        sync(cfg, self.store)
        ep = FakeEpisode(); ep.id = "s2:1"
        self.store.save_episode(ep, "proj-api", 0, "done", "", [
            {"text": "Built the API for X", "category": "contribution", "skills": [], "confidence": "observed",
             "evidence": "", "significance": 4}], "work", "Acme")
        self.assertEqual(self.store.unresolved_workspaces(), ["proj-api"])
        prompts = []
        cm.resolve_workspaces(self.store, 3, lambda p: (prompts.append(p), json.dumps({"project": "proj"}))[1])
        self.assertIn("proj-api", prompts[0]); self.assertIn("Built X", prompts[0])
        self.assertEqual(self.store.alias("proj-api"), "proj")
        self.assertEqual(self.store.unmerged_projects(), ["proj"])  # merges into the existing memory
        # A later session in the same repo goes straight to the project, no new question.
        ep2 = FakeEpisode(); ep2.id = "s3:1"
        self.store.save_episode(ep2, "proj-api", 0, "done", "", [
            {"text": "Added rate limiting", "category": "contribution", "skills": [], "confidence": "observed",
             "evidence": "", "significance": 3}], "work", "Acme")
        self.assertEqual(self.store.unresolved_workspaces(), [])
        self.assertEqual({c["project"] for c in self.store.claims()}, {"proj"})

    def test_merge_moves_claims_and_removes_old_memory_remotely(self):
        cfg = {"api_url": self.url, "api_token": "cm_good"}
        ep = FakeEpisode(); ep.id = "s2:1"
        self.store.save_episode(ep, "other", 0, "done", "", [
            {"text": "Built Z", "category": "contribution", "skills": [], "confidence": "observed",
             "evidence": "", "significance": 4}], "work", "Acme")
        for p in ("proj", "other"):
            self.store.mark_merged([c["id"] for c in self.store.unmerged(p, 3)])
            self.store.save_memory(p, f"memory of {p}", p)
        sync(cfg, self.store); Handler.calls.clear()
        for w in self.store.workspaces_of("other"):
            self.store.set_alias(w, "proj", manual=True)
        self.store.drop_memory("other")
        res = sync(cfg, self.store)
        self.assertEqual(res["sent"], 1)  # the moved claim re-syncs under its new project
        claims = [c for c in Handler.calls if c[1] == "/api/v1/claims/sync"][0][3]["claims"]
        self.assertEqual(claims[0]["project"], "proj")
        mem = [c for c in Handler.calls if c[1] == "/api/v1/memory"][0][3]["memories"]
        self.assertEqual([(m["key"], m["content_md"]) for m in mem], [("local:other", "")])  # deleted on the server

    def test_regroup_mapping(self):
        from memory import regroup
        summaries = [{"workspace": w, "count": 2, "first_at": None, "last_at": None, "organization": None,
                      "items": ["x"], "manual": m} for w, m in (("a", None), ("b", None), ("c", "Pinned"))]
        out = regroup(summaries, lambda p: json.dumps({"projects": [{"name": "AB", "workspaces": ["a", "b", "c"]}]}))
        self.assertEqual(out, {"a": "AB", "b": "AB", "c": "Pinned"})  # pinned (manual) choices win

    def test_bad_token(self):
        with self.assertRaises(SyncError):
            sync({"api_url": self.url, "api_token": "cm_bad"}, self.store)
        self.assertEqual(len(self.store.unsynced(3)), 2)  # nothing marked


class ContextTest(unittest.TestCase):
    def test_work_paths(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "work", "api"))
        cfg = {"work_paths": [{"path": os.path.join(d, "work"), "org": "Acme"}]}
        self.assertEqual(cm.context_of(os.path.join(d, "work", "api"), cfg), ("work", "Acme"))
        self.assertEqual(cm.context_of(d, cfg), ("personal", None))


if __name__ == "__main__":
    unittest.main()
