"""Tests for model bidding, resume, reuse, stop, liveness, secrets, trace, estimate, lessons, dashboard, doctor.
Run: python3 -m unittest -v tests/test_features.py"""
import json
import os
import signal
import subprocess
import sys
import time
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_engine import ENGINE, HiveCase, dump, load, open_text, write_text  # noqa: E402

NO_RETRY = {"manager": 0, "supervisor": 0, "worker": 0}


def mgr_sup_wrk(worker, sup_extra=None, mgr_extra=None):
    sup = dict({"role": "sup-requirements", "title": "Req", "mock": {"dispatch": [worker]}}, **(sup_extra or {}))
    return dict({"role": "mgr-product", "title": "Discovery", "mock": {"dispatch": [sup]}}, **(mgr_extra or {}))


class FeatureTest(HiveCase):
    def calls(self, kind="run", title=None):
        path = os.path.join(self.dir, ".hive", "mock_calls.jsonl")
        if not os.path.exists(path):
            return []
        rows = [json.loads(l) for l in open_text(path).splitlines() if l.strip()]
        return [r for r in rows if r["kind"] == kind and (title is None or r["title"] == title)]

    def report(self, task_id):
        return json.loads(self.hive("show", task_id, ok=True).stdout)

    # ------------------------------------------------------------------ model bidding

    def test_agent_bid_picks_model_and_effort(self):
        w = {"role": "wrk-plan-parser", "title": "Parse", "mock": {"bid": {"model": "haiku", "effort": "low"}}}
        self.hive("dispatch", "--brief", self.brief(mgr_sup_wrk(w)), ok=True)
        run = self.calls("run", "Parse")[0]
        self.assertEqual((run["model"], run["effort"]), ("haiku", "low"))
        sel = self.report("T0003-wrk-plan-parser")["model_selection"][0]
        self.assertEqual(sel["source"], "bid")
        self.assertEqual(len(self.calls("bid")), 3)  # one bid per task at every level
        self.assertIn("haiku/low", self.hive("status", ok=True).stdout)

    def test_bid_is_clamped_by_level_policy(self):
        mgr = {"role": "mgr-product", "title": "D", "mock": {"bid": {"model": "haiku", "effort": "max"}}}
        self.hive("dispatch", "--brief", self.brief(mgr), ok=True)
        sel = self.report("T0001-mgr-product")["model_selection"][0]
        self.assertEqual((sel["model"], sel["effort"]), ("sonnet", "xhigh"))
        self.assertEqual(sel["source"], "bid(clamped)")

    def test_failed_attempt_escalates_model_and_effort(self):
        w = {"role": "wrk-acceptance-criteria", "title": "AC", "verify": ["test -f docs/product/ok.md"],
             "mock": {"bid": {"model": "haiku", "effort": "low"}, "write_retry": {"docs/product/ok.md": "ok"}}}
        self.hive("dispatch", "--brief", self.brief(mgr_sup_wrk(w)), ok=True)
        runs = self.calls("run", "AC")
        self.assertEqual([(r["model"], r["effort"]) for r in runs], [("haiku", "low"), ("sonnet", "medium")])
        self.assertEqual(self.report("T0003-wrk-acceptance-criteria")["model_selection"][1]["source"], "bid+escalated")

    def test_user_override_wins_skips_bid_and_is_not_escalated(self):
        self.hive("models", "set", "wrk-acceptance-criteria", "--model", "opus", "--effort", "max", ok=True)
        w = {"role": "wrk-acceptance-criteria", "title": "AC", "verify": ["test -f docs/product/ok.md"],
             "mock": {"bid": {"model": "haiku"}, "write_retry": {"docs/product/ok.md": "ok"}}}
        self.hive("dispatch", "--brief", self.brief(mgr_sup_wrk(w)), ok=True)
        self.assertEqual([(r["model"], r["effort"]) for r in self.calls("run", "AC")], [("opus", "max"), ("opus", "max")])
        self.assertEqual([b["title"] for b in self.calls("bid")], ["Discovery", "Req"])
        self.assertIn("override(wrk-acceptance-criteria) opus/max", self.hive("models", ok=True).stdout)
        self.hive("models", "unset", "wrk-acceptance-criteria", ok=True)
        self.assertNotIn("override(wrk-acceptance-criteria)", self.hive("models", ok=True).stdout)

    def test_level_override_and_global_layering(self):
        self.hive("models", "set", "worker", "--model", "haiku", "--global", ok=True)
        self.hive("models", "set", "wrk-plan-parser", "--model", "opus", ok=True)  # project beats global
        a = {"role": "wrk-plan-parser", "title": "A"}
        b = {"role": "wrk-user-stories", "title": "B"}
        sup = {"role": "sup-requirements", "title": "Req", "mock": {"dispatch": [a, b]}}
        self.hive("dispatch", "--brief", self.brief({"role": "mgr-product", "title": "D", "mock": {"dispatch": [sup]}}), ok=True)
        self.assertEqual(self.calls("run", "A")[0]["model"], "opus")
        self.assertEqual(self.calls("run", "B")[0]["model"], "haiku")
        self.assertTrue(os.path.exists(os.path.join(self.global_dir, "config.json")))

    def test_parent_and_fixed_modes(self):
        self.hive("models", "mode", "parent", ok=True)
        w = {"role": "wrk-plan-parser", "title": "P", "model": "haiku", "effort": "high"}
        self.hive("dispatch", "--brief", self.brief(mgr_sup_wrk(w)), ok=True)
        self.assertEqual((self.calls("run", "P")[0]["model"], self.calls("run", "P")[0]["effort"]), ("haiku", "high"))
        self.assertEqual(self.calls("bid"), [])
        self.hive("models", "mode", "fixed", ok=True)
        w2 = {"role": "wrk-user-stories", "title": "F", "model": "haiku"}
        self.hive("dispatch", "--brief", self.brief(mgr_sup_wrk(w2, mgr_extra={"title": "D2"})), ok=True)
        self.assertEqual((self.calls("run", "F")[0]["model"], self.calls("run", "F")[0]["effort"]), ("sonnet", "medium"))

    def test_policy_command_changes_allowed_models(self):
        self.hive("models", "policy", "worker", "--allowed", "sonnet", "opus", "--max-effort", "medium", ok=True)
        w = {"role": "wrk-plan-parser", "title": "P", "mock": {"bid": {"model": "haiku", "effort": "high"}}}
        self.hive("dispatch", "--brief", self.brief(mgr_sup_wrk(w)), ok=True)
        r = self.calls("run", "P")[0]
        self.assertEqual((r["model"], r["effort"]), ("sonnet", "medium"))

    # ------------------------------------------------------------------ reuse, retry, resume

    def test_identical_brief_is_reused_when_still_green(self):
        b = {"role": "mgr-product", "title": "Same", "verify": ["true"]}
        self.hive("dispatch", "--brief", self.brief(b), ok=True)
        rep = json.loads(self.hive("dispatch", "--brief", self.brief(b), ok=True).stdout)
        self.assertEqual(rep["reused_from"], "T0001-mgr-product")
        self.assertEqual(rep["cost_usd"], 0.0)
        self.assertEqual(len(self.calls("run", "Same")), 1)
        b["no_cache"] = True
        self.hive("dispatch", "--brief", self.brief(b), ok=True)
        self.assertEqual(len(self.calls("run", "Same")), 2)

    def test_commander_retry_does_not_redo_passed_children(self):
        self.set_config(retries={"manager": 0, "supervisor": 1, "worker": 0})
        good = {"role": "wrk-plan-parser", "title": "Good", "mock": {"write": {"docs/product/req.md": "r"}}}
        bad = {"role": "wrk-user-stories", "title": "Bad", "verify": ["false"]}
        sup = {"role": "sup-requirements", "title": "Req", "mock": {"dispatch": [good, bad]}}
        self.hive("dispatch", "--brief", self.brief({"role": "mgr-product", "title": "D", "mock": {"dispatch": [sup]}}))
        self.assertEqual(len(self.calls("run", "Good")), 1)
        self.assertEqual(len(self.calls("run", "Bad")), 2)
        sup_runs = self.calls("run", "Req")
        self.assertTrue(sup_runs[1]["already_passed_note"])

    def test_resume_injects_completed_work_into_next_run(self):
        self.set_config(retries=NO_RETRY)
        w = {"role": "wrk-plan-parser", "title": "Parse", "mock": {"write": {"docs/product/req.md": "r"}}}
        self.hive("dispatch", "--brief", self.brief(mgr_sup_wrk(w, mgr_extra={"mock": {"dispatch": [{"role": "sup-requirements", "title": "Req", "mock": {"dispatch": [w]}}], "status": "FAIL"}})))
        self.hive("dispatch", "--brief", self.brief({"role": "mgr-product", "title": "Discovery again"}), ok=True)
        self.assertTrue(self.calls("run", "Discovery again")[0]["prior"])
        rep = self.report("T0004-mgr-product")
        self.assertEqual(rep["prior_progress"], ["T0002-sup-requirements", "T0003-wrk-plan-parser"])

    # ------------------------------------------------------------------ timeouts, stop, liveness

    def test_timeout_kills_agent_and_fails(self):
        self.set_config(timeout_min={"manager": 0.02, "supervisor": 1, "worker": 1}, retries=NO_RETRY)
        t0 = time.time()
        p = self.hive("dispatch", "--brief", self.brief({"role": "mgr-product", "title": "Slow", "mock": {"sleep": 60}}))
        self.assertEqual(p.returncode, 1)
        self.assertLess(time.time() - t0, 30)
        self.assertTrue(any("timeout" in r for r in json.loads(p.stdout)["failure_reasons"]))

    def test_stop_aborts_the_whole_running_tree(self):
        w = {"role": "wrk-plan-parser", "title": "Sleepy", "mock": {"sleep": 120}}
        proc = subprocess.Popen([sys.executable, ENGINE, "dispatch", "--brief", self.brief(mgr_sup_wrk(w))], cwd=self.dir,
                                env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.time() + 30
        while time.time() < deadline and not self.calls("run", "Sleepy"):
            time.sleep(0.2)
        self.assertTrue(self.calls("run", "Sleepy"), "worker never started")
        out = json.loads(self.hive("stop", ok=True).stdout)
        self.assertEqual(len(out["stopped"]), 3)
        proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 4)
        statuses = {t["task_id"]: t["status"] for t in json.loads(self.hive("status", "--json", ok=True).stdout)}
        self.assertEqual(set(statuses.values()), {"ABORTED"})
        self.assertEqual(self.git("status", "--porcelain").strip(), "")

    def test_dead_task_detected_and_cleaned_by_stop(self):
        p = subprocess.Popen(["true"])
        p.wait()
        with open(os.path.join(self.dir, ".hive", "ledger.jsonl"), "a") as fh:
            fh.write(json.dumps({"event": "task_start", "task_id": "T0099-mgr-product", "role": "mgr-product", "level": 1,
                                 "engine_pid": p.pid, "title": "ghost"}) + "\n")
        self.assertIn("[DEAD ] T0099-mgr-product", self.hive("status", ok=True).stdout)
        out = json.loads(self.hive("stop", ok=True).stdout)
        self.assertEqual(out["cleaned_dead"], ["T0099-mgr-product"])
        self.assertIn("[ABORT] T0099-mgr-product", self.hive("status", ok=True).stdout)

    # ------------------------------------------------------------------ scope, secrets, toolchain

    def test_scope_allows_nested_shared_files_and_generated_output(self):
        os.makedirs(os.path.join(self.dir, "docs", "architecture"))
        dump({"wrk-page-builder": ["web/src/**"]}, os.path.join(self.dir, "docs", "architecture", "ownership.json"))
        self.git("add", "-A"); self.git("commit", "-qm", "own")
        w = {"role": "wrk-page-builder", "title": "Page", "mock": {"write": {
            "web/src/page.tsx": "x", "web/package.json": "{}", "web/next-env.d.ts": "x"}}}
        sup = {"role": "sup-pages", "title": "Pages", "mock": {"dispatch": [w]}}
        self.set_config(retries=NO_RETRY)
        rep = json.loads(self.hive("dispatch", "--brief", self.brief({"role": "mgr-frontend", "title": "FE", "mock": {"dispatch": [sup]}}), ok=True).stdout)
        self.assertEqual(rep["status"], "PASS")
        self.assertIn("node_modules/", open_text(os.path.join(self.dir, ".gitignore")))

    def test_secrets_reach_agents_and_verify_but_never_logs(self):
        write_text(os.path.join(self.dir, ".hive", "secrets.env"), "SECRET_TOKEN=supersecretvalue123\n")
        self.env["MOCK_SECRET_ECHO"] = "SECRET_TOKEN"
        b = {"role": "mgr-product", "title": "S", "verify": ['test "$SECRET_TOKEN" = supersecretvalue123']}
        self.hive("dispatch", "--brief", self.brief(b), ok=True)
        log = open_text(os.path.join(self.dir, ".hive", "logs", "T0001-mgr-product.jsonl"))
        self.assertIn("agent saw secret ***", log)
        self.assertNotIn("supersecretvalue123", log)
        write_text(os.path.join(self.dir, ".env.example"), "SECRET_TOKEN=\nSTRIPE_KEY=\n")
        out = json.loads(self.hive("secrets", ok=True).stdout)
        self.assertEqual(out["missing"], ["STRIPE_KEY"])
        self.hive("secrets", "--template", ok=True)
        self.assertIn("STRIPE_KEY=", open_text(os.path.join(self.dir, ".hive", "secrets.env")))

    def test_doctor_project_toolchain(self):
        os.makedirs(os.path.join(self.dir, "docs", "architecture"))
        dump({"requires": [{"bin": "python3", "version_cmd": "python3 --version", "min": "3.0"},
                           {"bin": "surely-missing-tool-xyz", "install_hint": "brew install xyz", "optional": True}]},
             os.path.join(self.dir, "docs", "architecture", "toolchain.json"))
        fake = os.path.join(self.dir, "fakeclaude")
        write_text(fake, "#!/bin/sh\nif [ \"$1\" = --version ]; then echo 9.9; else echo '--model --effort'; fi\n")
        os.chmod(fake, 0o755)
        self.hive("config", "set", "claude_bin", fake, ok=True)
        p = self.hive("doctor", "--project")
        self.assertIn("WARN  tool: surely-missing-tool-xyz", p.stdout)
        self.assertIn("OK    tool: python3", p.stdout)
        self.assertIn("FAIL  claude CLI flags", p.stdout)
        self.assertEqual(p.returncode, 1)

    # ------------------------------------------------------------------ trace, estimate, lessons, retro

    def test_trace_and_gate_trace(self):
        d = self.dir
        os.makedirs(os.path.join(d, "docs", "product")); os.makedirs(os.path.join(d, "docs", "quality")); os.makedirs(os.path.join(d, "tests"))
        write_text(os.path.join(d, "docs", "product", "acceptance-criteria.md"), "AC-001 login\nAC-002 logout\n")
        write_text(os.path.join(d, "tests", "a.test.js"), "")
        write_text(os.path.join(d, "docs", "quality", "traceability.md"), "| AC-001 | `tests/a.test.js` | automated |\n| AC-002 | `tests/missing.test.js` | automated |\n")
        tr = json.loads(self.hive("trace").stdout)
        self.assertEqual(tr["broken_test_refs"], ["tests/missing.test.js"])
        write_text(os.path.join(d, "docs", "quality", "traceability.md"), "| AC-001 | `tests/a.test.js` | automated |\n| AC-002 | manual: click logout | manual |\n")
        self.hive("trace", ok=True)
        self.hive("gate", "--phase", "4-quality", "--trace", ok=True)

    def test_estimate_scales_with_blueprint(self):
        dump({"product_name": "X", "platforms": ["web"], "phases": [{"id": "1", "managers": ["mgr-product"]}]},
             os.path.join(self.dir, ".hive", "blueprint.json"))
        small = json.loads(self.hive("estimate", ok=True).stdout)
        dump({"product_name": "X", "platforms": ["web", "android", "ios"], "phases": [{"id": "1", "managers": ["mgr-product", "mgr-backend", "mgr-mobile"]}]},
             os.path.join(self.dir, ".hive", "blueprint.json"))
        big = json.loads(self.hive("estimate", ok=True).stdout)
        self.assertLess(small["expected_usd"], big["expected_usd"])
        self.assertLess(big["low_usd"], big["expected_usd"])
        self.assertLess(big["expected_usd"], big["high_usd"])
        self.assertIn("mgr-mobile", big["managers"])

    def test_lessons_are_recorded_and_injected(self):
        w = {"role": "wrk-acceptance-criteria", "title": "AC", "verify": ["test -f docs/product/ok.md"],
             "mock": {"write_retry": {"docs/product/ok.md": "ok"}}}
        self.hive("dispatch", "--brief", self.brief(mgr_sup_wrk(w)), ok=True)
        lessons = load(os.path.join(self.dir, ".hive", "lessons.json"))
        self.assertEqual(lessons[0]["role"], "wrk-acceptance-criteria")
        self.assertTrue(os.path.exists(os.path.join(self.global_dir, "lessons.json")))
        retro = self.hive("retro", ok=True).stdout
        self.assertIn("### wrk-acceptance-criteria", retro)
        self.assertIn("Model and effort outcomes", retro)

    def test_lesson_text_appears_in_system_prompt(self):
        dump([{"role": "mgr-product", "reason": "engine verification failed: `npm test` exited 1", "ts": "2026-01-01T00:00:00Z"}],
             os.path.join(self.dir, ".hive", "lessons.json"))
        out = json.loads(self.hive("dispatch", "--dry-run", "--brief", self.brief({"role": "mgr-product", "title": "x"}), ok=True).stdout)
        self.assertIn("LESSONS FROM EARLIER RUNS", out["system_prompt"])
        self.assertIn("`npm test` exited 1", out["system_prompt"])

    # ------------------------------------------------------------------ notifications and dashboard

    def test_notify_command_fires_on_manager_end(self):
        sink = os.path.join(self.dir, "notify.txt")
        self.hive("config", "set", "notify_command", 'echo "$HIVE_EVENT|$HIVE_STATUS|$HIVE_TITLE" >> %s' % sink, ok=True)
        self.hive("dispatch", "--brief", self.brief({"role": "mgr-product", "title": "N"}), ok=True)
        deadline = time.time() + 10
        while time.time() < deadline and not os.path.exists(sink):
            time.sleep(0.1)
        self.assertIn("task_end|PASS|PASS Product & Discovery Manager", open_text(sink))

    def test_dashboard_serves_html_and_json(self):
        self.hive("dispatch", "--brief", self.brief({"role": "mgr-product", "title": "Dash"}), ok=True)
        proc = subprocess.Popen([sys.executable, ENGINE, "serve", "--port", "0"], cwd=self.dir, env=self.env,
                                stdout=subprocess.PIPE, text=True)
        try:
            line = proc.stdout.readline()
            port = int(line.split("http://127.0.0.1:")[1].split()[0])
            page = urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=10).read().decode()
            self.assertIn("Dash", page)
            self.assertIn('http-equiv="refresh"', page)
            api = json.loads(urllib.request.urlopen("http://127.0.0.1:%d/api/status" % port, timeout=10).read())
            self.assertEqual(api["tasks"][0]["status"], "PASS")
        finally:
            proc.send_signal(signal.SIGINT)
            proc.wait(timeout=10)
            proc.stdout.close()

    # ------------------------------------------------------------------ delegation enforcement

    def enforce(self):
        self.hive("config", "unset", "require_delegation", ok=True)
        self.set_config(retries=NO_RETRY)

    def test_delegation_is_on_by_default(self):
        self.enforce()
        cfg = json.loads(self.hive("config", "show", ok=True).stdout)
        self.assertTrue(cfg["require_delegation"])

    def test_commander_without_children_fails(self):
        self.enforce()
        p = self.hive("dispatch", "--brief", self.brief({"role": "mgr-product", "title": "Lazy"}))
        self.assertEqual(p.returncode, 1)
        self.assertTrue(any("dispatched no children" in r for r in json.loads(p.stdout)["failure_reasons"]))

    def test_waiver_allows_review_only_task_and_is_reported(self):
        self.enforce()
        self.hive("dispatch", "--brief", self.brief({"role": "mgr-product", "title": "Review", "allow_no_children": True}), ok=True)
        self.assertIn("Delegation waived", self.hive("report", ok=True).stdout)

    def test_manager_editing_files_itself_fails(self):
        self.enforce()
        mgr = mgr_sup_wrk({"role": "wrk-plan-parser", "title": "P"})
        mgr["mock"]["write"] = {"docs/product/requirements.md": "manager wrote this itself"}
        p = self.hive("dispatch", "--brief", self.brief(mgr))
        self.assertEqual(p.returncode, 1)
        self.assertTrue(any("manager edited files directly" in r and "requirements.md" in r for r in json.loads(p.stdout)["failure_reasons"]))

    def test_manager_summary_file_is_allowed(self):
        self.enforce()
        mgr = mgr_sup_wrk({"role": "wrk-plan-parser", "title": "P"})
        mgr["scope"] = ["docs/**"]
        mgr["mock"]["write"] = {"docs/summaries/mgr-product.md": "summary"}
        self.hive("dispatch", "--brief", self.brief(mgr), ok=True)

    def test_manager_edits_before_dispatch_are_attributed_via_checkpoint(self):
        self.enforce()
        w = {"role": "wrk-plan-parser", "title": "P"}
        sup = {"role": "sup-requirements", "title": "Req", "mock": {"dispatch": [w]}}
        mgr = {"role": "mgr-product", "title": "D", "mock": {"write": {"docs/product/sneaky.md": "x"}, "dispatch": [sup]}}
        p = self.hive("dispatch", "--brief", self.brief(mgr))
        self.assertTrue(any("sneaky.md" in r for r in json.loads(p.stdout)["failure_reasons"]))

    def test_supervisor_glue_limit(self):
        self.enforce()
        self.hive("config", "set", "supervisor_glue_max_files", "2", ok=True)
        w = {"role": "wrk-plan-parser", "title": "P"}
        sup = {"role": "sup-requirements", "title": "Req", "mock": {"dispatch": [w], "write": {
            "docs/product/a.md": "a", "docs/product/b.md": "b", "docs/product/c.md": "c"}}}
        self.hive("dispatch", "--brief", self.brief({"role": "mgr-product", "title": "D", "mock": {"dispatch": [sup]}}))
        s = self.report("T0002-sup-requirements")
        self.assertTrue(any("glue limit 2" in r for r in s["failure_reasons"]))

    def test_at_commands_verify_expands_from_commands_json(self):
        b = {"role": "mgr-product", "title": "V", "verify": ["@commands:test"]}
        p = self.hive("dispatch", "--brief", self.brief(b))
        self.assertEqual(p.returncode, 1)  # commands.json missing
        os.makedirs(os.path.join(self.dir, "docs", "architecture"))
        dump({"test": "true", "build": "false"}, os.path.join(self.dir, "docs", "architecture", "commands.json"))
        self.git("add", "-A"); self.git("commit", "-qm", "cmds")
        b["title"] = "V2"
        rep = json.loads(self.hive("dispatch", "--brief", self.brief(b), ok=True).stdout)
        self.assertEqual(rep["verification"][0]["command"], "true")
        b["verify"], b["title"] = ["@commands"], "V3"
        self.assertEqual(self.hive("dispatch", "--brief", self.brief(b)).returncode, 1)  # build is false


if __name__ == "__main__":
    unittest.main()
