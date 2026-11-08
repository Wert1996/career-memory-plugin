import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "worker"))

import guidance  # noqa: E402
from extract import PROMPT  # noqa: E402
from memory import REGROUP_PROMPT, regroup  # noqa: E402


class GuidanceTest(unittest.TestCase):
    def test_template_alone_is_inactive(self):
        with tempfile.TemporaryDirectory() as home:
            guidance.ensure(home)
            self.assertEqual(guidance.load(home), "")

    def test_global_and_repo_instructions(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as repo:
            with open(guidance.ensure(home), "a") as f:
                f.write("\n## Projects\n- api and web are one project called Checkout.\n")
            with open(os.path.join(repo, guidance.REPO_FILE), "w") as f:
                f.write("Skip anything about the CI config.\n")
            g = guidance.load(home, repo)
            self.assertIn("one project called Checkout", g)
            self.assertIn("For this repository:\nSkip anything about the CI config.", g)
            self.assertNotIn("Examples:", g)
            self.assertIn("Checkout", PROMPT.format(categories="x", project="p", log="", guidance=g))

    def test_regroup_prompt_carries_instructions(self):
        seen = {}

        def call(prompt):
            seen["p"] = prompt
            return '{"projects": [{"name": "Checkout", "workspaces": ["api", "web"]}]}'

        ws = [{"workspace": w, "organization": None, "first_at": 0, "last_at": 0, "count": 1, "items": ["x"], "manual": None}
              for w in ("api", "web")]
        out = regroup(ws, call, guidance="\nTHE ENGINEER'S OWN INSTRUCTIONS: api and web are Checkout\n")
        self.assertEqual(out, {"api": "Checkout", "web": "Checkout"})
        self.assertIn("api and web are Checkout", seen["p"])
        self.assertIn("{guidance}", REGROUP_PROMPT)


if __name__ == "__main__":
    unittest.main()
