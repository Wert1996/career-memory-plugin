import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "worker"))

from scrub import scrub  # noqa: E402
from transcript import condense, read_entries, split_episodes  # noqa: E402


def line(role, ts, content, **extra):
    obj = {"type": role, "timestamp": ts, "cwd": "/repo", "sessionId": "s1",
           "message": {"role": role, "content": content}, **extra}
    return json.dumps(obj) + "\n"


class ScrubTest(unittest.TestCase):
    def test_redacts_secrets_and_pii(self):
        text = ("key AKIAABCDEFGHIJKLMNOP and ghp_abcdefghijklmnopqrstuvwxyz123456 "
                "password=hunter2 mail me@corp.com at 10.0.0.1 see https://internal.corp/x "
                "token sk-ant-api03-abcdefghijklmnopqrstuvwx")
        out, n = scrub(text)
        for leaked in ("AKIA", "ghp_", "hunter2", "me@corp.com", "10.0.0.1", "internal.corp", "sk-ant"):
            self.assertNotIn(leaked, out)
        self.assertGreaterEqual(n, 7)

    def test_keeps_normal_prose(self):
        text = "Refactored the retry_queue_consumer_service_handler to use exponential backoff"
        self.assertEqual(scrub(text)[0], text)

    def test_high_entropy(self):
        out, _ = scrub("value 9f8a7b6c5d4e3f2a1b0c9d8e7f6a5b4c3d2e1f0a")
        self.assertIn("[REDACTED]", out)


class TranscriptTest(unittest.TestCase):
    def setUp(self):
        self.f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        self.f.write(json.dumps({"type": "session-owner"}) + "\n")
        self.f.write(line("user", "2026-11-01T10:00:00Z", "Fix the flaky login test"))
        self.f.write(line("assistant", "2026-11-01T10:01:00Z", [
            {"type": "thinking", "thinking": "secret thoughts"},
            {"type": "text", "text": "Found it:\n```py\nSECRET_CODE()\n```"},
            {"type": "tool_use", "name": "Edit", "input": {"file_path": "/repo/test_login.py"}},
        ]))
        self.f.write(line("user", "2026-11-01T10:02:00Z", [
            {"type": "tool_result", "content": "raw file contents"}]))
        self.f.write(line("user", "2026-11-01T15:00:00Z", "<system-reminder>x</system-reminder>Now add docs"))
        self.f.write(line("assistant", "2026-11-01T15:00:30Z", "sub", isSidechain=True))
        self.f.write('{"type": "user", "partial')  # line still being written
        self.f.close()

    def tearDown(self):
        os.unlink(self.f.name)

    def test_read_split_condense(self):
        entries, eof = read_entries(self.f.name, 0)
        self.assertEqual(len(entries), 4)  # sidechain and partial line skipped
        with open(self.f.name, "rb") as fh:
            self.assertTrue(fh.read()[:eof].endswith(b"\n"))

        eps = split_episodes(self.f.name, entries, 45 * 60)
        self.assertEqual(len(eps), 2)
        text = condense(eps[0])
        self.assertIn("Fix the flaky login test", text)
        self.assertIn("[ACTION] Edit /repo/test_login.py", text)
        for dropped in ("SECRET_CODE", "secret thoughts", "raw file contents"):
            self.assertNotIn(dropped, text)
        self.assertEqual(condense(eps[1]), "[USER] Now add docs")

    def test_incremental_offset(self):
        entries, _ = read_entries(self.f.name, 0)
        rest, _ = read_entries(self.f.name, entries[1].end)
        self.assertEqual(len(rest), 2)


if __name__ == "__main__":
    unittest.main()
