"""End to end tests for the Hive engine using the mock agent. Run: python3 -m unittest -v tests/test_engine.py"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(HERE, "..", "plugins", "hive", "skills", "build", "engine", "hive.py")
MOCK = "%s %s" % (sys.executable, os.path.join(HERE, "mock_agent.py"))


def dump(obj, path):
    with open(path, "w") as fh:
        json.dump(obj, fh)


def write_text(path, text):
    with open(path, "w") as fh:
        fh.write(text)


def open_text(path):
    with open(path) as fh:
        return fh.read()


def load(path):
    with open(path) as fh:
        return json.load(fh)


class HiveCase(unittest.TestCase):
    """Shared fixture: a fresh initialized project per test. Holds no tests itself."""
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="hive-test-")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("HIVE_")}
        self.env["HIVE_MOCK_AGENT"] = MOCK
        self.global_dir = tempfile.mkdtemp(prefix="hive-global-")
        self.env["HIVE_GLOBAL_DIR"] = self.global_dir
        plan = os.path.join(self.dir, "plan.md")
        write_text(plan, "# Plan\nBuild a todo app.\n")
        self.hive("init", "--plan", plan, ok=True)
        # most tests exercise one mechanism with childless commanders; delegation has dedicated tests
        self.hive("config", "set", "require_delegation", "false", ok=True)

    def hive(self, *args, ok=None):
        p = subprocess.run([sys.executable, ENGINE] + list(args), cwd=self.dir, env=self.env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if ok:
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p

    def brief(self, data, name="b.json"):
        path = os.path.join(self.dir, ".hive", "briefs", name)
        dump(data, path)
        return path

    def git(self, *args):
        return subprocess.run(["git"] + list(args), cwd=self.dir, stdout=subprocess.PIPE, text=True).stdout

    def set_config(self, **kw):
        path = os.path.join(self.dir, ".hive", "config.json")
        cfg = load(path)
        cfg.update(kw)
        dump(cfg, path)


class HiveTest(HiveCase):
    def test_roster_is_valid_and_sized(self):
        out = json.loads(self.hive("validate", ok=True).stdout)
        self.assertTrue(out["valid"], out["errors"])
        self.assertEqual(out["counts"], {"orchestrator": 1, "manager": 7, "supervisor": 18, "worker": 65, "total": 91})

    def test_orchestrator_cannot_skip_levels(self):
        p = self.hive("dispatch", "--brief", self.brief({"role": "wrk-plan-parser", "title": "x"}))
        self.assertEqual(p.returncode, 3)
        self.assertIn("chain of command violation", p.stderr)

    def test_full_four_level_chain_commits_and_reports(self):
        worker = {"role": "wrk-plan-parser", "title": "Parse plan",
                  "verify": ["test -s docs/product/requirements.md"],
                  "mock": {"write": {"docs/product/requirements.md": "REQ-001 todo list\n"}}}
        sup = {"role": "sup-requirements", "title": "Requirements", "mock": {"dispatch": [worker]}}
        mgr = {"role": "mgr-product", "title": "Discovery", "mock": {"dispatch": [sup]}}
        p = self.hive("dispatch", "--brief", self.brief(mgr), ok=True)
        rep = json.loads(p.stdout)
        self.assertEqual(rep["status"], "PASS")
        self.assertEqual(rep["children"][0]["role"], "sup-requirements")
        tree = self.hive("status", ok=True).stdout
        self.assertRegex(tree, r"\[PASS \] T0001-mgr-product")
        self.assertRegex(tree, r"\n    \[PASS \] T0002-sup-requirements")
        self.assertRegex(tree, r"\n        \[PASS \] T0003-wrk-plan-parser")
        self.assertIn("hive(wrk-plan-parser): Parse plan [T0003-wrk-plan-parser]", self.git("log", "--oneline"))
        self.assertEqual(self.git("status", "--porcelain").strip(), "")

    def test_worker_cannot_dispatch(self):
        bad_worker = {"role": "wrk-plan-parser", "title": "rogue", "mock": {"dispatch": [{"role": "wrk-user-stories", "title": "x"}]}}
        sup = {"role": "sup-requirements", "title": "Req", "mock": {"dispatch": [bad_worker]}}
        mgr = {"role": "mgr-product", "title": "Discovery", "mock": {"dispatch": [sup]}}
        rep = json.loads(self.hive("dispatch", "--brief", self.brief(mgr)).stdout)
        self.assertEqual(rep["status"], "FAIL")
        worker_rep = json.loads(self.hive("show", "T0003-wrk-plan-parser", ok=True).stdout)
        self.assertIn("REFUSED", worker_rep["children"][0]["status"])

    def test_engine_verification_overrides_false_pass_then_retry_fixes(self):
        sup = {"role": "sup-requirements", "title": "Req", "mock": {"dispatch": [{
            "role": "wrk-acceptance-criteria", "title": "AC", "verify": ["grep -q AC-001 docs/product/acceptance-criteria.md"],
            "mock": {"write": {"docs/product/acceptance-criteria.md": "nothing yet\n"},
                     "write_retry": {"docs/product/acceptance-criteria.md": "AC-001 Given...\n"}}}]}}
        mgr = {"role": "mgr-product", "title": "D", "mock": {"dispatch": [sup]}}
        self.hive("dispatch", "--brief", self.brief(mgr), ok=True)
        w = json.loads(self.hive("show", "T0003-wrk-acceptance-criteria", ok=True).stdout)
        self.assertEqual(w["status"], "PASS")
        self.assertEqual(w["attempts"], 2)
        self.assertIn("engine verification failed", open_text(os.path.join(self.dir, ".hive", "ledger.jsonl")))

    def test_false_pass_without_retry_is_failed_and_stashed(self):
        sup = {"role": "sup-requirements", "title": "Req", "mock": {"dispatch": [{
            "role": "wrk-scope-risk", "title": "Risks", "verify": ["false"],
            "mock": {"write": {"docs/product/risks.md": "r\n"}}}]}}
        mgr = {"role": "mgr-product", "title": "D", "mock": {"dispatch": [sup]}}
        self.set_config(retries={"manager": 0, "supervisor": 0, "worker": 0})
        self.hive("dispatch", "--brief", self.brief(mgr))
        w = json.loads(self.hive("show", "T0003-wrk-scope-risk", ok=True).stdout)
        self.assertEqual(w["status"], "FAIL")
        self.assertEqual(w["stash"], "hive-failed:T0003-wrk-scope-risk")
        self.assertIn("hive-failed:T0003-wrk-scope-risk", self.git("stash", "list"))

    def test_commander_cannot_misstate_child_results(self):
        self.set_config(retries={"manager": 0, "supervisor": 0, "worker": 0})
        sup = {"role": "sup-requirements", "title": "Req", "mock": {"status": "PASS", "claim_child_status": "PASS",
               "dispatch": [{"role": "wrk-scope-risk", "title": "Risks", "verify": ["false"]}]}}
        mgr = {"role": "mgr-product", "title": "D", "mock": {"dispatch": [sup]}}
        self.hive("dispatch", "--brief", self.brief(mgr))
        s = json.loads(self.hive("show", "T0002-sup-requirements", ok=True).stdout)
        self.assertEqual(s["status"], "FAIL")
        self.assertTrue(any("misstates child T0003-wrk-scope-risk as PASS, engine recorded FAIL" in r for r in s["failure_reasons"]))

    def test_commander_override_is_recorded_and_reported(self):
        self.set_config(retries={"manager": 0, "supervisor": 0, "worker": 0})
        sup = {"role": "sup-requirements", "title": "Req", "mock": {"status": "PASS",
               "dispatch": [{"role": "wrk-scope-risk", "title": "Risks", "verify": ["false"]}]}}
        mgr = {"role": "mgr-product", "title": "D", "mock": {"dispatch": [sup]}}
        self.hive("dispatch", "--brief", self.brief(mgr), ok=True)
        s = json.loads(self.hive("show", "T0002-sup-requirements", ok=True).stdout)
        self.assertEqual(s["status"], "PASS")
        self.assertEqual(s["accepted_child_failures"], ["T0003-wrk-scope-risk"])
        self.assertIn("Commander overrides", self.hive("report", ok=True).stdout)

    def test_scope_violation_fails(self):
        self.set_config(retries={"manager": 0, "supervisor": 0, "worker": 0})
        sup = {"role": "sup-requirements", "title": "Req", "mock": {"dispatch": [{
            "role": "wrk-plan-parser", "title": "Parse", "mock": {"write": {"src/server.js": "hack\n"}}}]}}
        mgr = {"role": "mgr-product", "title": "D", "mock": {"dispatch": [sup]}}
        self.hive("dispatch", "--brief", self.brief(mgr))
        w = json.loads(self.hive("show", "T0003-wrk-plan-parser", ok=True).stdout)
        self.assertEqual(w["status"], "FAIL")
        self.assertTrue(any("scope violation" in r for r in w["failure_reasons"]))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "src", "server.js")))

    def test_ownership_map_controls_code_scope(self):
        os.makedirs(os.path.join(self.dir, "docs", "architecture"))
        dump({"wrk-page-builder": ["web/pages/**"]}, os.path.join(self.dir, "docs", "architecture", "ownership.json"))
        self.git("add", "-A"); self.git("commit", "-qm", "ownership")
        out = json.loads(self.hive("dispatch", "--dry-run", "--brief", self.brief({"role": "mgr-frontend", "title": "FE"})).stdout)
        self.assertEqual(out["scope"], ["web/pages/**"])
        self.assertEqual(out["scope_source"], "ownership.json")
        self.assertIn("`sup-pages`", out["system_prompt"])
        self.assertIn("--max-budget-usd", out["command"])

    def test_global_budget_ceiling_blocks(self):
        self.set_config(max_total_usd=0.05)
        self.hive("dispatch", "--brief", self.brief({"role": "mgr-product", "title": "first"}), ok=True)
        p = self.hive("dispatch", "--brief", self.brief({"role": "mgr-product", "title": "second"}))
        self.assertEqual(p.returncode, 2)
        self.assertIn("budget ceiling", json.loads(p.stdout)["summary"])

    def test_parallel_batch_merges_worktrees(self):
        a = self.brief({"role": "mgr-frontend", "title": "FE", "scope": ["web/**"], "mock": {"write": {"web/index.html": "<h1>hi</h1>\n"}}}, "a.json")
        b = self.brief({"role": "mgr-mobile", "title": "Mobile", "scope": ["mobile/**"], "mock": {"write": {"mobile/App.tsx": "export {}\n"}}}, "b.json")
        p = self.hive("batch", "--parallel", "2", "--briefs", a, b, ok=True)
        statuses = [r["status"] for r in json.loads(p.stdout)["results"]]
        self.assertEqual(statuses, ["PASS", "PASS"])
        self.assertTrue(os.path.exists(os.path.join(self.dir, "web", "index.html")))
        self.assertTrue(os.path.exists(os.path.join(self.dir, "mobile", "App.tsx")))
        self.assertNotIn("hive/wt/", self.git("branch"))

    def test_gate_and_final_report(self):
        os.makedirs(os.path.join(self.dir, "docs", "architecture"))
        dump({"build": "true", "test": "true"}, os.path.join(self.dir, "docs", "architecture", "commands.json"))
        self.hive("gate", "--phase", "2-architecture", ok=True)
        self.hive("dispatch", "--brief", self.brief({"role": "mgr-product", "title": "D"}), ok=True)
        md = self.hive("report", ok=True).stdout
        self.assertIn("| 2-architecture | PASS |", md)
        self.assertIn("| manager | 1 | 0 | 0 | 0 |", md)


if __name__ == "__main__":
    unittest.main()
