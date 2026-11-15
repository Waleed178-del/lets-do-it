#!/usr/bin/env python3
"""Hive engine: a four level chain of command for Claude Code.

Orchestrator (L0, your interactive session) -> Managers (L1) -> Supervisors (L2) -> Workers (L3).
Every level below the orchestrator runs as its own headless `claude -p` process. The engine sits
between every parent and child and enforces:

  * chain of command (a role may dispatch only its direct reports, workers dispatch nothing)
  * a schema validated report from every agent
  * independent re-verification of the brief's `verify` commands
  * path scope checks against the ownership map
  * automatic retry with failure feedback
  * one git commit per verified task, failed work stashed so the tree stays clean
  * a global spend ceiling

Standard library only. Python 3.8+.
"""
import argparse
import concurrent.futures
import contextlib
import datetime as dt
import fnmatch
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time

try:
    import fcntl  # POSIX
except ImportError:  # pragma: no cover  (Windows: no inter process locking, still works sequentially)
    fcntl = None

ENGINE = os.path.abspath(__file__)
ENGINE_DIR = os.path.dirname(ENGINE)
ROSTER_PATH = os.path.join(ENGINE_DIR, "roster.json")
PROMPT_DIR = os.path.join(ENGINE_DIR, "prompts")
SCHEMA_PATH = os.path.join(ENGINE_DIR, "schemas", "report.schema.json")

LEVEL_NAMES = {0: "orchestrator", 1: "manager", 2: "supervisor", 3: "worker"}
EXIT = {"PASS": 0, "FAIL": 1, "BLOCKED": 2}
ENGINE_ERROR = 3

DEFAULT_SHARED = [
    "package.json", "package-lock.json", "pnpm-lock.yaml", "pnpm-workspace.yaml", "yarn.lock",
    "bun.lockb", "bun.lock", "requirements*.txt", "pyproject.toml", "poetry.lock", "uv.lock",
    ".env.example", ".gitignore", "tsconfig*.json",
]

DEFAULT_CONFIG = {
    "models": {"manager": "opus", "supervisor": "sonnet", "worker": "sonnet"},
    "effort": {"manager": None, "supervisor": None, "worker": None},
    "budget_usd_per_dispatch": {"manager": 30, "supervisor": 12, "worker": 5},
    "max_total_usd": 400,
    "timeout_min": {"manager": 360, "supervisor": 180, "worker": 60},
    "retries": {"manager": 0, "supervisor": 1, "worker": 1},
    "verify_timeout_min": 20,
    "permission_mode": "acceptEdits",
    "allowed_tools": ["Bash", "Read", "Edit", "Write", "Glob", "Grep", "WebSearch", "WebFetch", "TodoWrite"],
    "disallowed_tools": ["Agent", "Task"],
    "on_fail": "stash",
    "auto_commit": True,
    "claude_bin": "claude",
}


# --------------------------------------------------------------------------- utilities

def now_iso():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def die(msg, code=ENGINE_ERROR):
    print(json.dumps({"engine_error": msg}), file=sys.stderr)
    sys.exit(code)


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def run(cmd, cwd, timeout=None, env=None, shell=False):
    """Run a command, return (exit_code, combined_output). Never raises on failure."""
    try:
        p = subprocess.run(cmd, cwd=cwd, shell=shell, env=env, timeout=timeout,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                           executable="/bin/bash" if shell and os.path.exists("/bin/bash") else None)
        return p.returncode, p.stdout
    except subprocess.TimeoutExpired as exc:
        out = exc.output.decode() if isinstance(exc.output, bytes) else (exc.output or "")
        return 124, out + "\n[hive] command timed out"
    except FileNotFoundError as exc:
        return 127, str(exc)


def git(args, cwd, check=False):
    code, out = run(["git"] + args, cwd)
    if check and code != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), out.strip()))
    return code, out.strip()


@contextlib.contextmanager
def file_lock(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fh = open(path, "a+")
    try:
        if fcntl:
            fcntl.flock(fh, fcntl.LOCK_EX)
        yield
    finally:
        if fcntl:
            fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


def tail(text, n=4000):
    text = text or ""
    return text if len(text) <= n else "...[truncated]...\n" + text[-n:]


# --------------------------------------------------------------------------- project and state

class Hive:
    """Paths and shared state. `workdir` is where code changes happen (may be a worktree);
    `state` is the single .hive directory shared by every process in the run."""

    def __init__(self, workdir=None, state=None):
        wd = workdir or os.environ.get("HIVE_WORKDIR") or os.getcwd()
        code, top = git(["rev-parse", "--show-toplevel"], wd)
        self.workdir = os.path.abspath(top if code == 0 and top else wd)
        self.state = os.path.abspath(state or os.environ.get("HIVE_STATE") or os.path.join(self.workdir, ".hive"))
        self.root = os.path.dirname(self.state)  # main project root (not the worktree)

    def p(self, *parts):
        return os.path.join(self.state, *parts)

    @property
    def config(self):
        return deep_merge(DEFAULT_CONFIG, read_json(self.p("config.json"), {}))

    def ledger(self, event):
        event = dict(event, ts=now_iso())
        with file_lock(self.p("locks", "ledger.lock")):
            with open(self.p("ledger.jsonl"), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")

    def events(self):
        out = []
        try:
            with open(self.p("ledger.jsonl"), encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        with contextlib.suppress(ValueError):
                            out.append(json.loads(line))
        except FileNotFoundError:
            pass
        return out

    def next_task_id(self, role):
        with file_lock(self.p("locks", "counter.lock")):
            counter = read_json(self.p("counter.json"), {"n": 0})
            counter["n"] += 1
            write_json(self.p("counter.json"), counter)
        return "T%04d-%s" % (counter["n"], role)

    def total_cost(self):
        return round(sum(e.get("cost_usd") or 0 for e in self.events() if e.get("event") == "attempt_end"), 4)

    def ownership(self):
        return read_json(os.path.join(self.workdir, "docs", "architecture", "ownership.json")) or \
            read_json(os.path.join(self.root, "docs", "architecture", "ownership.json")) or {}


# --------------------------------------------------------------------------- roster

class Roster:
    def __init__(self, path=ROSTER_PATH):
        data = read_json(path)
        if not data:
            die("roster not found at %s" % path)
        self.roles = {r["id"]: r for r in data["roles"]}

    def get(self, role_id):
        if role_id not in self.roles:
            die("unknown role '%s'. Run: hive.py roster" % role_id)
        return self.roles[role_id]

    def children(self, role_id):
        return [r for r in self.roles.values() if r.get("parent") == role_id]

    def descendants(self, role_id):
        out = []
        for c in self.children(role_id):
            out.append(c)
            out.extend(self.descendants(c["id"]))
        return out

    def validate(self):
        errors = []
        for r in self.roles.values():
            lvl, parent = r.get("level"), r.get("parent")
            if lvl not in LEVEL_NAMES:
                errors.append("%s: bad level %r" % (r["id"], lvl))
            if lvl == 0:
                if parent is not None:
                    errors.append("%s: orchestrator must have no parent" % r["id"])
                continue
            if parent not in self.roles:
                errors.append("%s: parent %r does not exist" % (r["id"], parent))
            elif self.roles[parent]["level"] != lvl - 1:
                errors.append("%s: parent %s is not exactly one level up" % (r["id"], parent))
            if lvl in (1, 2) and not self.children(r["id"]):
                errors.append("%s: commander with no direct reports" % r["id"])
            if not r.get("mission"):
                errors.append("%s: missing mission" % r["id"])
        roots = [r for r in self.roles.values() if r["level"] == 0]
        if len(roots) != 1:
            errors.append("exactly one orchestrator required, found %d" % len(roots))
        return errors

    def counts(self):
        c = {}
        for r in self.roles.values():
            c[LEVEL_NAMES[r["level"]]] = c.get(LEVEL_NAMES[r["level"]], 0) + 1
        c["total"] = len(self.roles)
        return c


def resolve_scope(hive, roster, role, brief):
    """Effective path globs a task may change. Returns (globs, source)."""
    if brief.get("scope"):
        return list(brief["scope"]), "brief"
    own = hive.ownership()

    def from_ownership(rid):
        if rid in own and own[rid]:
            return list(own[rid])
        globs = []
        for d in roster.descendants(rid):
            globs.extend(own.get(d["id"]) or [])
        return globs

    declared = role.get("scope") or ["**"]
    if "@ownership" in declared:
        globs = from_ownership(role["id"])
        if globs:
            return sorted(set(globs)), "ownership.json"
        return ["**"], "unrestricted (no ownership.json entry yet)"
    return declared, "roster"


def path_in_scope(path, globs):
    for g in globs:
        if g in ("**", "*") or fnmatch.fnmatch(path, g):
            return True
        if g.endswith("/**") and (path == g[:-3] or path.startswith(g[:-2])):
            return True
    return False


# --------------------------------------------------------------------------- git helpers

def ensure_identity(cwd):
    code, email = git(["config", "user.email"], cwd)
    if code != 0 or not email:
        git(["config", "user.email", "hive@localhost"], cwd)
        git(["config", "user.name", "Hive Engine"], cwd)


def head_sha(cwd):
    code, sha = git(["rev-parse", "HEAD"], cwd)
    return sha if code == 0 else None


def is_dirty(cwd):
    _, out = git(["status", "--porcelain", "--untracked-files=all"], cwd)
    return bool(out.strip())


def commit_all(cwd, message):
    ensure_identity(cwd)
    git(["add", "-A"], cwd)
    code, _ = git(["diff", "--cached", "--quiet"], cwd)
    if code == 0:
        return None  # nothing staged
    code, out = git(["commit", "-q", "--no-verify", "-m", message], cwd)
    if code != 0:
        raise RuntimeError("commit failed: " + out)
    return head_sha(cwd)


def changed_files(cwd, start_sha):
    files = set()
    if start_sha:
        _, out = git(["diff", "--name-only", start_sha], cwd)  # committed + working tree vs start
        files.update(l for l in out.splitlines() if l)
    _, out = git(["ls-files", "--others", "--exclude-standard"], cwd)
    files.update(l for l in out.splitlines() if l)
    return sorted(f for f in files if not f.startswith(".hive/"))


# --------------------------------------------------------------------------- prompt composition

def bullet(items):
    return "\n".join("* %s" % i for i in items) if items else "* (see brief)"


def render(template, values):
    return re.sub(r"\{\{(\w+)\}\}", lambda m: str(values.get(m.group(1), m.group(0))), template)


def load_prompt(name):
    with open(os.path.join(PROMPT_DIR, name), encoding="utf-8") as fh:
        return fh.read()


LEVEL_RULES = {
    1: "As a manager you never write product code. You plan, write briefs for your supervisors, dispatch them, "
       "audit their reports and integrate. Documentation you own (for example a phase summary) is the only thing "
       "you may write directly.",
    2: "As a supervisor you break your area into worker sized tasks, dispatch workers, review their output and run "
       "integration checks. You may make small glue fixes inside your scope yourself. Real feature work goes to workers.",
}


def compose_system_prompt(hive, roster, role, task_id, scope):
    lvl = role["level"]
    values = {
        "TITLE": role["title"], "ROLE_ID": role["id"], "LEVEL_NAME": LEVEL_NAMES[lvl],
        "PARENT": role.get("parent"), "TASK_ID": task_id, "MISSION": role["mission"],
        "DELIVERABLES": bullet(role.get("deliverables")), "DONE_WHEN": bullet(role.get("done_when")),
        "SCOPE": bullet(scope), "ENGINE": ENGINE, "BRIEF_DIR": ".hive/briefs/%s" % task_id,
        "LEVEL_RULE": LEVEL_RULES.get(lvl, ""),
        "CHILDREN": "\n".join("* `%s`: %s. %s" % (c["id"], c["title"], c["mission"]) for c in roster.children(role["id"])),
    }
    parts = [render(load_prompt("common.md"), values)]
    parts.append(render(load_prompt("commander.md" if lvl in (1, 2) else "worker.md"), values))
    return "\n\n".join(parts)


def compose_user_prompt(brief, task_id, feedback=None):
    lines = ["# TASK BRIEF %s" % task_id, "", "```json", json.dumps(brief, indent=2, ensure_ascii=False), "```"]
    if feedback:
        lines += ["", "# PREVIOUS ATTEMPT FAILED. FIX THESE ROOT CAUSES FIRST", feedback,
                  "", "Your earlier changes are still in the working tree. Build on them, do not start over unless they are wrong."]
    lines += ["", "Execute the task now. Finish with the JSON report."]
    return "\n".join(lines)


# --------------------------------------------------------------------------- running an agent

def agent_command(cfg, level_name, system_prompt):
    cmd = [cfg["claude_bin"], "-p", "--output-format", "stream-json", "--verbose",
           "--append-system-prompt", system_prompt,
           "--json-schema", json.dumps(read_json(SCHEMA_PATH)),
           "--permission-mode", cfg["permission_mode"],
           "--permission-prompts", "none"]
    if cfg["permission_mode"] != "bypassPermissions" and cfg.get("allowed_tools"):
        cmd += ["--allowedTools", " ".join(cfg["allowed_tools"])]
    if cfg.get("disallowed_tools"):
        cmd += ["--disallowedTools", " ".join(cfg["disallowed_tools"])]
    model = cfg["models"].get(level_name)
    if model:
        cmd += ["--model", model]
    effort = (cfg.get("effort") or {}).get(level_name)
    if effort:
        cmd += ["--effort", effort]
    budget = cfg["budget_usd_per_dispatch"].get(level_name)
    if budget:
        cmd += ["--max-budget-usd", str(budget)]
    return cmd


def extract_json(text):
    """Find the last JSON object in free text (fallback when structured output is missing)."""
    if not text:
        return None
    for m in reversed(list(re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S))):
        with contextlib.suppress(ValueError):
            return json.loads(m.group(1))
    start = text.rfind("{\"status\"")
    if start >= 0:
        with contextlib.suppress(ValueError):
            return json.loads(text[start:text.rfind("}") + 1])
    return None


def run_agent(hive, cmd, prompt, env, log_path, timeout_s, mock):
    """Run one agent process. Returns dict(report, cost_usd, error, session_id)."""
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    if mock:
        cmd = shlex.split(mock)
    result_event, err = None, None
    with open(log_path, "a", encoding="utf-8") as log:
        log.write(json.dumps({"hive": "attempt_start", "ts": now_iso()}) + "\n")
        proc = subprocess.Popen(cmd, cwd=hive.workdir, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, start_new_session=True)
        deadline = time.time() + timeout_s
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
        except BrokenPipeError:
            pass
        for line in proc.stdout:
            log.write(line)
            log.flush()
            s = line.strip()
            if s.startswith("{"):
                with contextlib.suppress(ValueError):
                    ev = json.loads(s)
                    if ev.get("type") == "result":
                        result_event = ev
            if time.time() > deadline:
                err = "timeout after %d min" % (timeout_s // 60)
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGTERM)
                break
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
    out = {"report": None, "cost_usd": 0.0, "error": err, "session_id": None}
    if result_event:
        out["cost_usd"] = float(result_event.get("total_cost_usd") or 0)
        out["session_id"] = result_event.get("session_id")
        report = result_event.get("structured_output")
        if not isinstance(report, dict):
            report = extract_json(result_event.get("result") or "")
        out["report"] = report
        if result_event.get("is_error") and not report:
            out["error"] = out["error"] or "agent error: %s" % (result_event.get("subtype") or result_event.get("result"))
    elif not err:
        out["error"] = "agent exited (code %s) without a result event, see %s" % (proc.returncode, log_path)
    return out


def normalize_report(rep):
    if not isinstance(rep, dict):
        return None
    status = str(rep.get("status", "")).upper()
    if status not in EXIT:
        return None
    rep["status"] = status
    for k in ("deliverables", "checks", "issues", "children"):
        if not isinstance(rep.get(k), list):
            rep[k] = []
    rep.setdefault("summary", "")
    return rep


def run_verify(hive, commands, timeout_s):
    results = []
    for c in commands or []:
        t0 = time.time()
        code, out = run(c, hive.workdir, timeout=timeout_s, shell=True, env=dict(os.environ, CI="1"))
        results.append({"command": c, "exit_code": code, "seconds": round(time.time() - t0, 1), "output_tail": tail(out, 2500)})
    return results


# --------------------------------------------------------------------------- dispatch

def load_brief(args):
    if args.brief:
        brief = read_json(args.brief)
        if brief is None:
            die("brief file not found: %s" % args.brief)
    elif args.brief_json:
        brief = json.loads(args.brief_json)
    else:
        die("provide --brief FILE or --brief-json STRING")
    if not isinstance(brief, dict) or not brief.get("role") or not brief.get("title"):
        die("brief must be a JSON object with at least 'role' and 'title'")
    return brief


def dispatch(hive, roster, brief, retries_override=None, dry_run=False):
    cfg = hive.config
    caller = os.environ.get("HIVE_CALLER_ROLE", "orchestrator")
    parent_task = os.environ.get("HIVE_TASK_ID")
    depth = int(os.environ.get("HIVE_DEPTH", "0"))
    role = roster.get(brief["role"])
    caller_role = roster.get(caller)

    # chain of command
    if role.get("parent") != caller:
        die("chain of command violation: '%s' (level %d) may dispatch only %s, not '%s' (reports to '%s')" % (
            caller, caller_role["level"], [c["id"] for c in roster.children(caller)] or "nobody",
            role["id"], role.get("parent")))
    if depth >= 3 or role["level"] != depth + 1:
        die("depth guard: caller depth %d cannot launch level %d role '%s'" % (depth, role["level"], role["id"]))
    if not os.path.isdir(hive.state):
        die("no .hive directory. Run: hive.py init --plan <file>")

    level_name = LEVEL_NAMES[role["level"]]
    task_id = hive.next_task_id(role["id"])
    scope, scope_source = resolve_scope(hive, roster, role, brief)
    shared = list(hive.ownership().get("_shared") or DEFAULT_SHARED)
    write_json(hive.p("tasks", task_id + ".json"), dict(brief, task_id=task_id, parent_task=parent_task, caller=caller))
    os.makedirs(hive.p("briefs", task_id), exist_ok=True)
    system_prompt = compose_system_prompt(hive, roster, role, task_id, scope)
    cmd = agent_command(cfg, level_name, system_prompt)

    if dry_run:
        return {"task_id": task_id, "role": role["id"], "scope": scope, "scope_source": scope_source,
                "command": [c if len(c) < 200 else c[:200] + "..." for c in cmd],
                "system_prompt": system_prompt, "user_prompt": compose_user_prompt(brief, task_id)}, 0

    # global spend ceiling
    spent = hive.total_cost()
    if cfg.get("max_total_usd") and spent >= cfg["max_total_usd"]:
        report = {"task_id": task_id, "role": role["id"], "title": brief["title"], "status": "BLOCKED",
                  "summary": "Global budget ceiling reached (%.2f of %.2f USD). Raise max_total_usd in .hive/config.json to continue." % (spent, cfg["max_total_usd"]),
                  "attempts": 0}
        write_json(hive.p("reports", task_id + ".json"), report)
        hive.ledger({"event": "task_end", "task_id": task_id, "role": role["id"], "status": "BLOCKED", "reason": "budget"})
        return report, EXIT["BLOCKED"]

    ensure_identity(hive.workdir)
    if is_dirty(hive.workdir) and cfg.get("auto_commit"):
        commit_all(hive.workdir, "hive(checkpoint): work in progress by %s before %s" % (caller, task_id))
    start_sha = head_sha(hive.workdir)

    env = dict(os.environ)
    env.update({
        "HIVE_ENGINE": ENGINE, "HIVE_STATE": hive.state, "HIVE_WORKDIR": hive.workdir,
        "HIVE_CALLER_ROLE": role["id"], "HIVE_TASK_ID": task_id, "HIVE_DEPTH": str(role["level"]),
        "BASH_DEFAULT_TIMEOUT_MS": str(cfg["timeout_min"]["worker"] * 60000),
        "BASH_MAX_TIMEOUT_MS": str(max(cfg["timeout_min"].values()) * 60000 + 600000),
        "CI": "1",
    })
    hive.ledger({"event": "task_start", "task_id": task_id, "role": role["id"], "level": role["level"],
                 "parent_task": parent_task, "caller": caller, "title": brief["title"], "start_sha": start_sha,
                 "workdir": hive.workdir})

    retries = cfg["retries"].get(level_name, 0) if retries_override is None else retries_override
    timeout_s = cfg["timeout_min"][level_name] * 60
    verify_timeout = cfg["verify_timeout_min"] * 60
    log_path = hive.p("logs", task_id + ".jsonl")
    mock = os.environ.get("HIVE_MOCK_AGENT")
    t0 = time.time()
    feedback, attempts, total_cost = None, 0, 0.0
    report, verification, reasons, session_id = None, [], [], None

    while True:
        attempts += 1
        hive.ledger({"event": "attempt_start", "task_id": task_id, "attempt": attempts})
        res = run_agent(hive, cmd, compose_user_prompt(brief, task_id, feedback), env, log_path, timeout_s, mock)
        total_cost += res["cost_usd"]
        session_id = res["session_id"] or session_id
        report = normalize_report(res["report"])
        reasons = []
        if res["error"]:
            reasons.append(res["error"])
        if report is None:
            reasons.append("agent returned no valid report")
            report = {"status": "FAIL", "summary": "No valid report returned.", "deliverables": [], "checks": [], "issues": [], "children": []}

        verification = run_verify(hive, brief.get("verify"), verify_timeout) if report["status"] != "BLOCKED" else []
        for v in verification:
            if v["exit_code"] != 0:
                reasons.append("engine verification failed: `%s` exited %d" % (v["command"], v["exit_code"]))
        files = changed_files(hive.workdir, start_sha)
        out_of_scope = [f for f in files if not path_in_scope(f, scope) and not path_in_scope(f, shared)]
        if out_of_scope:
            reasons.append("scope violation: changed files outside %s: %s" % (scope, out_of_scope[:20]))
        for d in report["deliverables"]:
            if report["status"] == "PASS" and d.get("done") is False:
                reasons.append("report claims PASS with an undone deliverable: %s" % d.get("item"))
        actual_children = {t["task_id"]: t for t in task_table(hive).values() if t.get("parent_task") == task_id}
        for c in report["children"]:
            real = actual_children.get(c.get("task_id"))
            if real is None:
                reasons.append("report lists child %s which this task never dispatched" % c.get("task_id"))
            elif str(c.get("status", "")).upper() != real["status"]:
                reasons.append("report misstates child %s as %s, engine recorded %s" % (c["task_id"], c.get("status"), real["status"]))

        if report["status"] == "BLOCKED":
            final = "BLOCKED"
        elif report["status"] == "PASS" and not reasons:
            final = "PASS"
        else:
            final = "FAIL"
        hive.ledger({"event": "attempt_end", "task_id": task_id, "attempt": attempts, "self_status": report["status"],
                     "final": final, "cost_usd": round(res["cost_usd"], 4), "reasons": reasons})

        if final != "FAIL" or attempts > retries:
            break
        if cfg.get("max_total_usd") and hive.total_cost() >= cfg["max_total_usd"]:
            reasons.append("retry skipped: global budget ceiling reached")
            break
        fb = ["* " + r for r in reasons]
        if report.get("issues"):
            fb += ["", "Issues you reported:"] + ["* " + i for i in report["issues"]]
        for v in verification:
            if v["exit_code"] != 0:
                fb += ["", "Output of `%s`:" % v["command"], "```", tail(v["output_tail"], 2000), "```"]
        feedback = "\n".join(fb)

    files = changed_files(hive.workdir, start_sha)
    end_sha, stash_ref = start_sha, None
    if final == "PASS" and cfg.get("auto_commit"):
        end_sha = commit_all(hive.workdir, "hive(%s): %s [%s]" % (role["id"], brief["title"], task_id)) or head_sha(hive.workdir)
    elif final != "PASS" and cfg.get("on_fail") == "stash" and is_dirty(hive.workdir):
        code, _ = git(["stash", "push", "-u", "-m", "hive-failed:%s" % task_id], hive.workdir)
        if code == 0:
            stash_ref = "hive-failed:%s" % task_id
        end_sha = head_sha(hive.workdir)

    final_report = {
        "task_id": task_id, "role": role["id"], "level": role["level"], "title": brief["title"],
        "parent_task": parent_task, "status": final, "attempts": attempts,
        "failure_reasons": reasons if final != "PASS" else [],
        "summary": report.get("summary"), "deliverables": report.get("deliverables"),
        "agent_checks": report.get("checks"), "issues": report.get("issues"), "children": report.get("children"),
        "accepted_child_failures": [t["task_id"] for t in task_table(hive).values()
                                    if t.get("parent_task") == task_id and t["status"] in ("FAIL", "BLOCKED")] if final == "PASS" else [],
        "verification": [{k: v[k] for k in ("command", "exit_code", "seconds")} for v in verification],
        "scope": scope, "scope_source": scope_source, "files_changed": files,
        "start_sha": start_sha, "end_sha": end_sha, "stash": stash_ref,
        "cost_usd": round(total_cost, 4), "duration_s": round(time.time() - t0, 1),
        "session_id": session_id, "log": os.path.relpath(log_path, hive.root),
    }
    write_json(hive.p("reports", task_id + ".json"), dict(final_report, verification_detail=verification))
    hive.ledger({"event": "task_end", "task_id": task_id, "role": role["id"], "status": final,
                 "cost_usd": final_report["cost_usd"], "duration_s": final_report["duration_s"]})
    return final_report, EXIT[final]


# --------------------------------------------------------------------------- batch (parallel in worktrees)

def batch(hive, roster, brief_paths, parallel):
    if parallel <= 1:
        results = []
        for bp in brief_paths:
            rep, _ = dispatch(hive, roster, read_json(bp))
            results.append(rep)
        return results

    briefs = []
    for bp in brief_paths:
        b = read_json(bp)
        if not b:
            die("brief not found: %s" % bp)
        briefs.append((bp, b))
    ensure_identity(hive.workdir)
    if is_dirty(hive.workdir):
        commit_all(hive.workdir, "hive(checkpoint): before parallel batch by %s" % os.environ.get("HIVE_CALLER_ROLE", "orchestrator"))
    base = head_sha(hive.workdir)
    stamp = "%d-%d" % (int(time.time()), os.getpid())
    wts = []
    for i, (bp, b) in enumerate(briefs):
        path = hive.p("wt", "%s-%d" % (stamp, i))
        branch = "hive/wt/%s-%d" % (stamp, i)
        code, out = git(["worktree", "add", "-q", "-b", branch, path, base], hive.workdir)
        if code != 0:
            die("worktree creation failed: " + out)
        wts.append((bp, b, path, branch))

    def work(item):
        bp, b, path, branch = item
        env = dict(os.environ, HIVE_WORKDIR=path, HIVE_STATE=hive.state)
        code, out = run([sys.executable, ENGINE, "dispatch", "--brief", os.path.abspath(bp)], path, env=env)
        try:
            return json.loads(out.strip().splitlines()[-1]) if out.strip() else {"status": "FAIL", "summary": "no output"}
        except (ValueError, IndexError):
            return {"status": "FAIL", "role": b.get("role"), "title": b.get("title"), "summary": tail(out, 1500)}

    with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as ex:
        results = list(ex.map(work, wts))

    for (bp, b, path, branch), rep in zip(wts, results):
        if rep.get("status") == "PASS":
            code, out = git(["merge", "--no-ff", "-q", "-m", "hive(merge): %s [%s]" % (b["title"], rep.get("task_id")), branch], hive.workdir)
            if code != 0:
                git(["merge", "--abort"], hive.workdir)
                rep["status"] = "FAIL"
                rep.setdefault("failure_reasons", []).append("merge conflict integrating parallel branch %s (kept for inspection): %s" % (branch, tail(out, 800)))
                if rep.get("task_id"):
                    full = read_json(hive.p("reports", rep["task_id"] + ".json"), {})
                    full.update(status="FAIL", failure_reasons=rep["failure_reasons"])
                    write_json(hive.p("reports", rep["task_id"] + ".json"), full)
                    hive.ledger({"event": "task_end", "task_id": rep["task_id"], "role": rep.get("role"), "status": "FAIL", "reason": "merge conflict"})
                git(["worktree", "remove", "--force", path], hive.workdir)
                continue
        git(["worktree", "remove", "--force", path], hive.workdir)
        if rep.get("status") == "PASS":
            git(["branch", "-D", branch], hive.workdir)
    git(["worktree", "prune"], hive.workdir)
    return results


# --------------------------------------------------------------------------- status and reports

def task_table(hive):
    tasks = {}
    for e in hive.events():
        tid = e.get("task_id")
        if not tid:
            continue
        t = tasks.setdefault(tid, {"task_id": tid, "status": "RUNNING", "cost_usd": 0.0, "attempts": 0})
        if e["event"] == "task_start":
            t.update({k: e.get(k) for k in ("role", "level", "parent_task", "title", "ts")})
        elif e["event"] == "attempt_start":
            t["attempts"] = e.get("attempt", t["attempts"])
        elif e["event"] == "attempt_end":
            t["cost_usd"] += e.get("cost_usd") or 0
        elif e["event"] == "task_end":
            t["status"] = e.get("status")
            t["role"] = t.get("role") or e.get("role")
            t["duration_s"] = e.get("duration_s")
    return tasks


def print_tree(hive):
    tasks = task_table(hive)
    kids = {}
    for t in tasks.values():
        kids.setdefault(t.get("parent_task"), []).append(t)
    mark = {"PASS": "PASS ", "FAIL": "FAIL ", "BLOCKED": "BLOCK", "RUNNING": "RUN  "}
    lines = []

    def walk(parent, indent):
        for t in sorted(kids.get(parent, []), key=lambda x: x["task_id"]):
            lines.append("%s[%s] %s  %s  (%s, $%.2f%s)" % (
                "    " * indent, mark.get(t["status"], t["status"]), t["task_id"], t.get("title") or "",
                t.get("role"), t["cost_usd"], ", %d attempts" % t["attempts"] if t["attempts"] > 1 else ""))
            walk(t["task_id"], indent + 1)

    walk(None, 0)
    counts = {}
    for t in tasks.values():
        counts[t["status"]] = counts.get(t["status"], 0) + 1
    lines.append("")
    lines.append("tasks: %d  %s  total spend: $%.2f" % (len(tasks), "  ".join("%s=%d" % kv for kv in sorted(counts.items())), hive.total_cost()))
    return "\n".join(lines) if tasks else "No tasks dispatched yet."


def final_report(hive):
    tasks = task_table(hive)
    reports = {tid: read_json(hive.p("reports", tid + ".json"), {}) for tid in tasks}
    blueprint = read_json(hive.p("blueprint.json"), {})
    gates = read_json(hive.p("gates.json"), [])
    by_level = {}
    for t in tasks.values():
        lvl = LEVEL_NAMES.get(t.get("level"), "?")
        by_level.setdefault(lvl, {}).setdefault(t["status"], 0)
        by_level[lvl][t["status"]] += 1
    md = ["# Hive Build Report", "", "Product: **%s**  " % blueprint.get("product_name", "unnamed"),
          "Platforms: %s  " % ", ".join(blueprint.get("platforms", ["web"])),
          "Generated: %s  " % now_iso(), "Total spend: **$%.2f** across %d dispatched tasks" % (hive.total_cost(), len(tasks)), "",
          "## Outcome by level", "", "| Level | PASS | FAIL | BLOCKED | RUNNING |", "|---|---|---|---|---|"]
    for lvl in ("manager", "supervisor", "worker"):
        c = by_level.get(lvl, {})
        md.append("| %s | %d | %d | %d | %d |" % (lvl, c.get("PASS", 0), c.get("FAIL", 0), c.get("BLOCKED", 0), c.get("RUNNING", 0)))
    if gates:
        md += ["", "## Phase gates", "", "| Phase | Result | Commands |", "|---|---|---|"]
        for g in gates:
            md.append("| %s | %s | %s |" % (g.get("phase"), "PASS" if g.get("passed") else "FAIL",
                                           ", ".join("`%s` %d" % (c["command"], c["exit_code"]) for c in g.get("results", []))))
    md += ["", "## Chain of command", "", "```", print_tree(hive), "```"]
    problems = [r for r in reports.values() if r.get("status") in ("FAIL", "BLOCKED")]
    if problems:
        md += ["", "## Failures and blockers", ""]
        for r in problems:
            md.append("### %s %s (%s)" % (r.get("status"), r.get("task_id"), r.get("role")))
            md.append(r.get("summary") or "")
            for reason in r.get("failure_reasons") or []:
                md.append("* %s" % reason)
            if r.get("stash"):
                md.append("* Failed work preserved in git stash `%s`" % r["stash"])
            md.append("")
    overrides = [r for r in reports.values() if r.get("accepted_child_failures")]
    if overrides:
        md += ["", "## Commander overrides (PASS accepted despite failed children)", ""]
        md += ["* `%s` accepted %s. Reason given: %s" % (r["task_id"], ", ".join(r["accepted_child_failures"]), r.get("summary") or "")
               for r in overrides]
    issues = [(r.get("task_id"), i) for r in reports.values() for i in (r.get("issues") or [])]
    if issues:
        md += ["", "## Assumptions and open issues reported by agents", ""]
        md += ["* `%s` %s" % (tid, i) for tid, i in issues[:200]]
    return "\n".join(md) + "\n"


# --------------------------------------------------------------------------- commands

def cmd_init(args):
    hive = Hive()
    for d in ("tasks", "reports", "logs", "briefs", "locks", "wt"):
        os.makedirs(hive.p(d), exist_ok=True)
    if args.plan:
        src = sys.stdin.read() if args.plan == "-" else open(args.plan, encoding="utf-8").read()
        with open(hive.p("plan.md"), "w", encoding="utf-8") as fh:
            fh.write(src)
    if not os.path.exists(hive.p("config.json")):
        write_json(hive.p("config.json"), DEFAULT_CONFIG)
    gi = os.path.join(hive.workdir, ".gitignore")
    existing = open(gi, encoding="utf-8").read() if os.path.exists(gi) else ""
    if ".hive/" not in existing.splitlines():
        with open(gi, "a", encoding="utf-8") as fh:
            fh.write(("" if existing.endswith("\n") or not existing else "\n") + ".hive/\n")
    code, _ = git(["rev-parse", "--git-dir"], hive.workdir)
    if code != 0:
        git(["init", "-q"], hive.workdir, check=True)
    ensure_identity(hive.workdir)
    if head_sha(hive.workdir) is None or is_dirty(hive.workdir):
        commit_all(hive.workdir, "hive(init): start of Hive build")
    print(json.dumps({"initialized": hive.state, "plan": os.path.exists(hive.p("plan.md")),
                      "config": hive.p("config.json"), "head": head_sha(hive.workdir)}, indent=2))


def cmd_doctor(_args):
    checks = []
    checks.append(("python >= 3.8", sys.version_info >= (3, 8), sys.version.split()[0]))
    checks.append(("git on PATH", shutil.which("git") is not None, shutil.which("git") or "missing"))
    claude = shutil.which(DEFAULT_CONFIG["claude_bin"])
    ver = run([claude, "--version"], os.getcwd())[1].strip() if claude else "missing"
    checks.append(("claude CLI on PATH", claude is not None, ver))
    errs = Roster().validate()
    checks.append(("roster valid", not errs, "; ".join(errs) or json.dumps(Roster().counts())))
    hive = Hive()
    checks.append(("inside git repo", git(["rev-parse", "--git-dir"], hive.workdir)[0] == 0, hive.workdir))
    checks.append((".hive initialized", os.path.isdir(hive.state), hive.state))
    ok = True
    for name, passed, detail in checks:
        print("%s  %-22s %s" % ("OK  " if passed else "FAIL", name, detail))
        ok = ok and (passed or name in ("inside git repo", ".hive initialized"))
    sys.exit(0 if ok else 1)


def cmd_roster(args):
    roster = Roster()
    if args.role:
        r = roster.get(args.role)
        print(json.dumps(dict(r, children=[c["id"] for c in roster.children(r["id"])]), indent=2))
        return

    def walk(rid, indent):
        r = roster.roles[rid]
        print("%s%s  %s" % ("    " * indent, rid, r["title"] + (" [conditional: %s]" % r["conditional"] if r.get("conditional") else "")))
        for c in roster.children(rid):
            walk(c["id"], indent + 1)

    walk("orchestrator", 0)
    print("\n" + json.dumps(roster.counts()))


def cmd_validate(_args):
    roster = Roster()
    errs = roster.validate()
    print(json.dumps({"valid": not errs, "errors": errs, "counts": roster.counts()}, indent=2))
    sys.exit(0 if not errs else 1)


def cmd_dispatch(args):
    hive, roster = Hive(), Roster()
    rep, code = dispatch(hive, roster, load_brief(args), args.retries, args.dry_run)
    print(json.dumps(rep, indent=None if not args.pretty else 2, ensure_ascii=False))
    sys.exit(code)


def cmd_batch(args):
    hive, roster = Hive(), Roster()
    results = batch(hive, roster, args.briefs, args.parallel)
    summary = [{k: r.get(k) for k in ("task_id", "role", "title", "status", "failure_reasons", "cost_usd")} for r in results]
    print(json.dumps({"results": summary}, indent=2, ensure_ascii=False))
    worst = max((EXIT.get(r.get("status"), 1) for r in results), default=0)
    sys.exit(worst)


def cmd_gate(args):
    hive = Hive()
    cmds_map = read_json(os.path.join(hive.workdir, "docs", "architecture", "commands.json"), {})
    keys = args.keys or ["install", "lint", "typecheck", "test", "build"]
    commands = [cmds_map[k] for k in keys if cmds_map.get(k)]
    if args.extra:
        commands += args.extra
    if not commands:
        print(json.dumps({"phase": args.phase, "passed": None, "note": "no commands.json yet, gate skipped"}))
        return
    results = run_verify(hive, commands, hive.config["verify_timeout_min"] * 60)
    passed = all(r["exit_code"] == 0 for r in results)
    gates = read_json(hive.p("gates.json"), [])
    gates.append({"phase": args.phase, "passed": passed, "ts": now_iso(),
                  "results": [{k: r[k] for k in ("command", "exit_code", "seconds")} for r in results]})
    write_json(hive.p("gates.json"), gates)
    print(json.dumps({"phase": args.phase, "passed": passed, "results": results}, indent=2))
    sys.exit(0 if passed else 1)


def cmd_status(args):
    hive = Hive()
    if args.json:
        print(json.dumps(list(task_table(hive).values()), indent=2))
    else:
        print(print_tree(hive))


def cmd_show(args):
    hive = Hive()
    rep = read_json(hive.p("reports", args.task_id + ".json"))
    if rep is None:
        die("no report for %s" % args.task_id)
    print(json.dumps(rep, indent=2, ensure_ascii=False))


def cmd_report(_args):
    hive = Hive()
    md = final_report(hive)
    path = hive.p("FINAL_REPORT.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(md)
    print(md)
    print("written: %s" % path)


def cmd_rollback(args):
    hive = Hive()
    rep = read_json(hive.p("reports", args.task_id + ".json"))
    if not rep or not rep.get("start_sha"):
        die("no start_sha recorded for %s" % args.task_id)
    if not args.yes:
        print("Would run: git reset --hard %s  (discards every commit after task %s started). Re-run with --yes." % (rep["start_sha"], args.task_id))
        return
    git(["reset", "--hard", rep["start_sha"]], hive.workdir, check=True)
    hive.ledger({"event": "rollback", "task_id": args.task_id, "to": rep["start_sha"]})
    print("rolled back to %s" % rep["start_sha"])


def main():
    ap = argparse.ArgumentParser(prog="hive.py", description="Hive multi level agent engine for Claude Code")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create .hive state, store the plan, prepare git")
    s.add_argument("--plan", help="path to the plan file, or - for stdin")
    s.set_defaults(fn=cmd_init)

    sub.add_parser("doctor", help="check prerequisites").set_defaults(fn=cmd_doctor)

    s = sub.add_parser("roster", help="print the chain of command")
    s.add_argument("--role")
    s.set_defaults(fn=cmd_roster)

    sub.add_parser("validate", help="validate roster integrity").set_defaults(fn=cmd_validate)

    s = sub.add_parser("dispatch", help="run one task on a direct report and return its verified report")
    s.add_argument("--brief")
    s.add_argument("--brief-json")
    s.add_argument("--retries", type=int)
    s.add_argument("--dry-run", action="store_true", help="compose prompts and command without running")
    s.add_argument("--pretty", action="store_true")
    s.set_defaults(fn=cmd_dispatch)

    s = sub.add_parser("batch", help="run several briefs, optionally in parallel git worktrees")
    s.add_argument("--briefs", nargs="+", required=True)
    s.add_argument("--parallel", type=int, default=1)
    s.set_defaults(fn=cmd_batch)

    s = sub.add_parser("gate", help="run the project's build/test commands as a phase gate")
    s.add_argument("--phase", required=True)
    s.add_argument("--keys", nargs="*")
    s.add_argument("--extra", nargs="*")
    s.set_defaults(fn=cmd_gate)

    s = sub.add_parser("status", help="print the live task tree")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("show", help="print one task's full report")
    s.add_argument("task_id")
    s.set_defaults(fn=cmd_show)

    sub.add_parser("report", help="write .hive/FINAL_REPORT.md").set_defaults(fn=cmd_report)

    s = sub.add_parser("rollback", help="reset the repository to the state before a task")
    s.add_argument("task_id")
    s.add_argument("--yes", action="store_true")
    s.set_defaults(fn=cmd_rollback)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
