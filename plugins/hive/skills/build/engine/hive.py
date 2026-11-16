#!/usr/bin/env python3
"""Hive engine: a four level chain of command for Claude Code.

Orchestrator (L0, your interactive session) -> Managers (L1) -> Supervisors (L2) -> Workers (L3).
Every level below the orchestrator runs as its own headless `claude -p` process. The engine sits
between every parent and child and enforces:

  * chain of command (a role may dispatch only its direct reports, workers dispatch nothing)
  * per task model bidding (the assigned agent bids a model and effort, clamped by your policy,
    escalated automatically on retry, overridable per role at any time)
  * a schema validated report from every agent
  * independent re-verification of the brief's `verify` commands
  * path scope checks against the ownership map, child status cross checks for commanders
  * automatic retry with failure feedback, without redoing children that already passed
  * resume across runs, reuse of identical briefs that still verify, lessons from past failures
  * one git commit per verified task, failed or aborted work stashed so the tree stays clean
  * per dispatch and global spend ceilings, liveness tracking, `stop`, notifications

Standard library only. Python 3.8+. POSIX first class, Windows best effort.
"""
import argparse
import concurrent.futures
import contextlib
import datetime as dt
import fnmatch
import hashlib
import html
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time

try:
    import fcntl  # POSIX
except ImportError:  # pragma: no cover
    fcntl = None

IS_WINDOWS = os.name == "nt"
ENGINE = os.path.abspath(__file__)
ENGINE_DIR = os.path.dirname(ENGINE)
ROSTER_PATH = os.path.join(ENGINE_DIR, "roster.json")
PROMPT_DIR = os.path.join(ENGINE_DIR, "prompts")
SCHEMA_PATH = os.path.join(ENGINE_DIR, "schemas", "report.schema.json")
PY = '"%s"' % sys.executable if " " in sys.executable else sys.executable

LEVEL_NAMES = {0: "orchestrator", 1: "manager", 2: "supervisor", 3: "worker"}
EXIT = {"PASS": 0, "FAIL": 1, "BLOCKED": 2, "ABORTED": 4}
ENGINE_ERROR = 3
FINAL_STATES = ("PASS", "FAIL", "BLOCKED", "ABORTED", "DEAD")

DEFAULT_SHARED = [
    "package.json", "package-lock.json", "pnpm-lock.yaml", "pnpm-workspace.yaml", "yarn.lock",
    "bun.lockb", "bun.lock", "requirements*.txt", "pyproject.toml", "poetry.lock", "uv.lock",
    ".env.example", ".gitignore", "tsconfig*.json",
]
# Generated output never counts against a task's scope (and init git-ignores it).
DEFAULT_IGNORE = [
    "node_modules/**", "**/node_modules/**", "dist/**", "**/dist/**", "build/**", "**/build/**",
    ".next/**", "**/.next/**", "out/**", "coverage/**", "**/coverage/**", ".turbo/**", "**/.turbo/**",
    "**/__pycache__/**", "*.pyc", "**/*.pyc", ".expo/**", "**/.expo/**", "**/.gradle/**", "**/Pods/**",
    "**/DerivedData/**", "test-results/**", "**/test-results/**", "playwright-report/**",
    "**/playwright-report/**", "*.tsbuildinfo", "**/*.tsbuildinfo", "next-env.d.ts", "**/next-env.d.ts",
    ".venv/**", "venv/**", ".pytest_cache/**", "**/.pytest_cache/**",
]
GITIGNORE_DEFAULTS = [".hive/", "node_modules/", "dist/", ".next/", "coverage/", ".turbo/", "__pycache__/",
                      "*.pyc", ".expo/", ".gradle/", "test-results/", "playwright-report/", "*.tsbuildinfo",
                      ".venv/", ".env", ".DS_Store"]
REQUIRED_CLI_FLAGS = ["--output-format", "--json-schema", "--append-system-prompt", "--permission-mode",
                      "--permission-prompts", "--allowedTools", "--disallowedTools", "--model", "--effort",
                      "--max-budget-usd", "--tools", "--no-session-persistence", "--verbose"]

DEFAULT_CONFIG = {
    "models": {"manager": "opus", "supervisor": "sonnet", "worker": "sonnet"},
    "effort": {"manager": "high", "supervisor": "medium", "worker": "medium"},
    "model_selection": {
        "mode": "bid",
        "bidder_model": "haiku",
        "bidder_effort": "low",
        "tiers": ["haiku", "sonnet", "opus"],
        "efforts": ["low", "medium", "high", "xhigh", "max"],
        "allowed": {"manager": ["sonnet", "opus"], "supervisor": ["haiku", "sonnet", "opus"],
                    "worker": ["haiku", "sonnet", "opus"]},
        "max_effort": {"manager": "xhigh", "supervisor": "high", "worker": "high"},
        "escalate_on_retry": True,
        "overrides": {},
    },
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
    "reuse_verified_briefs": True,
    "approval_phases": [],
    "notify_command": None,
    "secrets_file": ".hive/secrets.env",
    "global_lessons": True,
    "cost_model_usd": {"haiku": 0.3, "sonnet": 1.5, "opus": 5.0, "fable": 5.0},
    "cost_level_multiplier": {"manager": 3.0, "supervisor": 1.5, "worker": 1.0},
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
    except (FileNotFoundError, NotADirectoryError):
        return default
    except ValueError:
        return default


def read_text(path, default=""):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except (FileNotFoundError, NotADirectoryError):
        return default


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def shell_exe():
    return "/bin/bash" if not IS_WINDOWS and os.path.exists("/bin/bash") else None


def run(cmd, cwd, timeout=None, env=None, shell=False):
    """Run a command, return (exit_code, combined_output). Never raises on failure."""
    try:
        p = subprocess.run(cmd, cwd=cwd, shell=shell, env=env, timeout=timeout, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, errors="replace",
                           executable=shell_exe() if shell else None)
        return p.returncode, p.stdout
    except subprocess.TimeoutExpired as exc:
        out = exc.output.decode(errors="replace") if isinstance(exc.output, bytes) else (exc.output or "")
        return 124, out + "\n[hive] command timed out"
    except (FileNotFoundError, NotADirectoryError) as exc:
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


def global_dir():
    return os.environ.get("HIVE_GLOBAL_DIR") or os.path.join(os.path.expanduser("~"), ".hive")


# --------------------------------------------------------------------------- process control (POSIX + Windows)

def popen_group_kwargs():
    if IS_WINDOWS:
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)}
    return {"start_new_session": True}


def kill_tree(pid, hard=False):
    """Terminate a process and its process group / tree."""
    if not pid:
        return
    if IS_WINDOWS:
        run(["taskkill", "/T", "/F", "/PID", str(pid)], os.getcwd())
        return
    sig = signal.SIGKILL if hard else signal.SIGTERM
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(pid, sig)
        return
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.kill(pid, sig)


def signal_pid(pid, hard=False):
    if not pid:
        return
    if IS_WINDOWS:
        kill_tree(pid, hard=True)
        return
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.kill(pid, signal.SIGKILL if hard else signal.SIGTERM)


def pid_alive(pid):
    if not pid:
        return False
    if IS_WINDOWS:  # pragma: no cover
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if not h:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return code.value == 259
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # a zombie still answers signal 0: treat it as dead
    stat = read_text("/proc/%d/stat" % int(pid))
    if stat and ") Z " in stat:
        return False
    return True


class AbortRequested(Exception):
    pass


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
        cfg = deep_merge(DEFAULT_CONFIG, read_json(os.path.join(global_dir(), "config.json"), {}))
        return deep_merge(cfg, read_json(self.p("config.json"), {}))

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
        return round(sum(e.get("cost_usd") or 0 for e in self.events() if e.get("event") in ("attempt_end", "bid")), 4)

    def ownership(self):
        return read_json(os.path.join(self.workdir, "docs", "architecture", "ownership.json")) or \
            read_json(os.path.join(self.root, "docs", "architecture", "ownership.json")) or {}

    def secrets(self):
        path = self.config.get("secrets_file") or ".hive/secrets.env"
        path = path if os.path.isabs(path) else os.path.join(self.root, path)
        return parse_env_file(read_text(path))


def parse_env_file(text):
    out = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:]
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k):
            out[k] = v
    return out


def redactor(secrets):
    values = sorted({v for v in secrets.values() if v and len(v) >= 6}, key=len, reverse=True)

    def redact(text):
        for v in values:
            text = text.replace(v, "***")
        return text
    return redact


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


def path_in_scope(path, globs, basename_match=False):
    base = path.rsplit("/", 1)[-1]
    for g in globs:
        if g in ("**", "*") or fnmatch.fnmatch(path, g):
            return True
        if g.endswith("/**") and (path == g[:-3] or path.startswith(g[:-2])):
            return True
        if basename_match and "/" not in g and fnmatch.fnmatch(base, g):
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


def is_ancestor(sha, cwd):
    if not sha:
        return False
    return git(["merge-base", "--is-ancestor", sha, "HEAD"], cwd)[0] == 0


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


# --------------------------------------------------------------------------- model bidding

def level_policy(cfg, level_name):
    ms = cfg["model_selection"]
    tiers = list(ms.get("tiers") or ["haiku", "sonnet", "opus"])
    efforts = list(ms.get("efforts") or ["low", "medium", "high", "xhigh", "max"])
    allowed = list((ms.get("allowed") or {}).get(level_name) or tiers)
    max_effort = (ms.get("max_effort") or {}).get(level_name) or efforts[-1]
    if max_effort not in efforts:
        max_effort = efforts[-1]
    default_model = cfg["models"].get(level_name) or allowed[-1]
    default_effort = (cfg.get("effort") or {}).get(level_name) or "medium"
    return {"tiers": tiers, "efforts": efforts, "allowed": allowed, "max_effort": max_effort,
            "default_model": default_model, "default_effort": default_effort}


def clamp_model(model, pol):
    allowed, tiers = pol["allowed"], pol["tiers"]
    if model in allowed:
        return model
    if model in tiers:
        idx = tiers.index(model)
        ranked = sorted((tiers.index(a), a) for a in allowed if a in tiers)
        above = [a for i, a in ranked if i >= idx]
        if above:
            return above[0]
        if ranked:
            return ranked[-1][1]
    return pol["default_model"] if pol["default_model"] in allowed else allowed[-1]


def clamp_effort(effort, pol):
    efforts = pol["efforts"]
    if effort not in efforts:
        effort = pol["default_effort"] if pol["default_effort"] in efforts else "medium"
    return efforts[min(efforts.index(effort), efforts.index(pol["max_effort"]))]


def escalate(sel, pol):
    tiers, allowed = pol["tiers"], pol["allowed"]
    model = sel["model"]
    if model in tiers:
        higher = [a for a in sorted(allowed, key=lambda a: tiers.index(a) if a in tiers else -1)
                  if a in tiers and tiers.index(a) > tiers.index(model)]
        if higher:
            model = higher[0]
    efforts = pol["efforts"]
    effort = sel["effort"]
    if effort in efforts:
        effort = efforts[min(efforts.index(effort) + 1, efforts.index(pol["max_effort"]))]
    return dict(sel, model=model, effort=effort, source=sel["source"] + "+escalated",
                rationale="Escalated after a failed attempt (was %s/%s)." % (sel["model"], sel["effort"]))


def find_override(cfg, role, level_name):
    ov = cfg["model_selection"].get("overrides") or {}
    for key in (role["id"], level_name, "*"):
        if key in ov and isinstance(ov[key], dict) and (ov[key].get("model") or ov[key].get("effort")):
            return key, ov[key]
    return None, None


def bid_schema(pol):
    efforts = pol["efforts"][:pol["efforts"].index(pol["max_effort"]) + 1]
    return {"type": "object", "additionalProperties": False,
            "required": ["model", "effort", "complexity", "rationale"],
            "properties": {"model": {"type": "string", "enum": pol["allowed"]},
                           "effort": {"type": "string", "enum": efforts},
                           "complexity": {"type": "integer", "minimum": 1, "maximum": 5},
                           "rationale": {"type": "string"}}}


BID_GUIDE = """You are bidding for the task below on behalf of the agent that will execute it.
Choose the CHEAPEST model and LOWEST effort that will very likely succeed on the FIRST attempt.
A failed attempt is retried on a stronger model, so under-bidding wastes the whole first attempt.

Model guide:
* haiku: mechanical, fully specified work. One or two files, formatting, config, simple docs, renames.
* sonnet: standard engineering. Multi file features, components, endpoints, tests, most bug fixes, specs.
* opus: hard reasoning. Architecture and stack decisions, cross cutting integration, subtle debugging,
  security review, commanders coordinating many children, anything ambiguous with high blast radius.
Effort guide: low for trivial, medium as the default, high for tricky logic or many constraints,
xhigh or max only for the hardest reasoning where a mistake is expensive.
Commanders (managers, supervisors) mostly plan, write briefs and audit reports: weigh how many children
and how much judgment they need.
Return only the bid."""


def run_bidder(hive, cfg, role, brief, pol, mock):
    ms = cfg["model_selection"]
    prompt = "\n".join([
        "# BID REQUEST", "", BID_GUIDE, "",
        "Role: %s (%s, level %d: %s)" % (role["title"], role["id"], role["level"], LEVEL_NAMES[role["level"]]),
        "Role mission: %s" % role["mission"],
        "Allowed models for this level: %s. Maximum effort: %s." % (", ".join(pol["allowed"]), pol["max_effort"]),
        "Parent's suggestion: model=%s effort=%s reason=%s" % (brief.get("model"), brief.get("effort"), brief.get("model_reason")),
        "", "Task brief:", "```json", json.dumps(brief if mock else {k: v for k, v in brief.items() if k != "mock"}, indent=2), "```"])
    stdin = prompt
    if mock:
        cmd = shlex.split(mock)
    else:
        cmd = [cfg["claude_bin"], "-p", "--output-format", "json", "--tools", "", "--no-session-persistence",
               "--permission-prompts", "none", "--model", ms.get("bidder_model") or "haiku",
               "--effort", ms.get("bidder_effort") or "low", "--max-budget-usd", "0.5",
               "--json-schema", json.dumps(bid_schema(pol))]
    try:
        p = subprocess.run(cmd, cwd=hive.workdir, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, errors="replace", timeout=180)
        out = p.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return None, 0.0, "bidder failed: %s" % exc
    result = None
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("{"):
            with contextlib.suppress(ValueError):
                ev = json.loads(s)
                if ev.get("type") == "result":
                    result = ev
    if result is None:
        with contextlib.suppress(ValueError):
            ev = json.loads(out[out.find("{"):out.rfind("}") + 1])
            if ev.get("type") == "result":
                result = ev
    if not result:
        return None, 0.0, "bidder returned no result"
    bid = result.get("structured_output")
    return (bid if isinstance(bid, dict) else None), float(result.get("total_cost_usd") or 0), None


def select_model(hive, cfg, role, brief, level_name, task_id, mock):
    """Return the model/effort selection for attempt 1."""
    pol = level_policy(cfg, level_name)
    key, ov = find_override(cfg, role, level_name)
    if ov:
        # a user override is honored exactly, even outside the level's allowed list
        return {"model": ov.get("model") or pol["default_model"],
                "effort": ov.get("effort") or pol["default_effort"], "source": "override:%s" % key,
                "rationale": "User override for %s." % key, "complexity": None, "locked": True}
    mode = cfg["model_selection"].get("mode", "bid")
    hint_model, hint_effort = brief.get("model"), brief.get("effort")
    if mode == "fixed":
        return {"model": clamp_model(pol["default_model"], pol), "effort": clamp_effort(pol["default_effort"], pol),
                "source": "fixed", "rationale": "Level default.", "complexity": None, "locked": False}
    if mode == "parent":
        return {"model": clamp_model(hint_model or pol["default_model"], pol),
                "effort": clamp_effort(hint_effort or pol["default_effort"], pol),
                "source": "parent" if hint_model else "default", "rationale": brief.get("model_reason") or "Parent choice.",
                "complexity": None, "locked": False}
    bid, cost, err = run_bidder(hive, cfg, role, brief, pol, mock)
    hive.ledger({"event": "bid", "task_id": task_id, "cost_usd": round(cost, 5), "bid": bid, "error": err})
    if not bid or not bid.get("model"):
        return {"model": clamp_model(hint_model or pol["default_model"], pol),
                "effort": clamp_effort(hint_effort or pol["default_effort"], pol),
                "source": "bid-fallback", "rationale": err or "invalid bid", "complexity": None, "locked": False}
    model, effort = clamp_model(bid.get("model"), pol), clamp_effort(bid.get("effort"), pol)
    clamped = model != bid.get("model") or effort != bid.get("effort")
    return {"model": model, "effort": effort, "source": "bid" + ("(clamped)" if clamped else ""),
            "rationale": str(bid.get("rationale", ""))[:500], "complexity": bid.get("complexity"), "locked": False}


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


def lessons_for(hive, role_id, limit=5):
    items = list(read_json(hive.p("lessons.json"), []))
    if hive.config.get("global_lessons"):
        items += read_json(os.path.join(global_dir(), "lessons.json"), [])
    seen, out = set(), []
    for it in sorted(items, key=lambda x: x.get("ts", ""), reverse=True):
        if it.get("role") != role_id:
            continue
        k = it.get("reason", "")[:120]
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
        if len(out) >= limit:
            break
    return out


def compose_system_prompt(hive, roster, role, task_id, scope):
    lvl = role["level"]
    values = {
        "TITLE": role["title"], "ROLE_ID": role["id"], "LEVEL_NAME": LEVEL_NAMES[lvl],
        "PARENT": role.get("parent"), "TASK_ID": task_id, "MISSION": role["mission"],
        "DELIVERABLES": bullet(role.get("deliverables")), "DONE_WHEN": bullet(role.get("done_when")),
        "SCOPE": bullet(scope), "ENGINE": ENGINE, "PY": PY, "BRIEF_DIR": ".hive/briefs/%s" % task_id,
        "LEVEL_RULE": LEVEL_RULES.get(lvl, ""),
        "CHILDREN": "\n".join("* `%s`: %s. %s" % (c["id"], c["title"], c["mission"]) for c in roster.children(role["id"])),
    }
    parts = [render(load_prompt("common.md"), values)]
    parts.append(render(load_prompt("commander.md" if lvl in (1, 2) else "worker.md"), values))
    lessons = lessons_for(hive, role["id"])
    if lessons:
        parts.append("# LESSONS FROM EARLIER RUNS OF YOUR ROLE\nThese caused failures before. Avoid them.\n" +
                     "\n".join("* %s" % l["reason"] for l in lessons))
    return "\n\n".join(parts)


def compose_user_prompt(brief, task_id, feedback=None, prior=None, selection=None):
    lines = ["# TASK BRIEF %s" % task_id, "", "```json",
             json.dumps({k: v for k, v in brief.items()}, indent=2, ensure_ascii=False), "```"]
    if selection:
        lines += ["", "You are running on model `%s` at effort `%s` (%s)." % (selection["model"], selection["effort"], selection["source"])]
    if prior:
        lines += ["", "# WORK ALREADY COMPLETED AND VERIFIED (do not redo)",
                  "An earlier run of this task was interrupted or failed after these children passed. Their commits are in the "
                  "repository. Dispatch only the remaining work. Re-dispatch one of these only if you find evidence it is now broken."]
        for p in prior:
            lines.append("* `%s` (%s) %s: %s. Files: %s" % (p["task_id"], p["role"], p["title"], p["summary"], ", ".join(p["files"]) or "none"))
    if feedback:
        lines += ["", "# PREVIOUS ATTEMPT FAILED. FIX THESE ROOT CAUSES FIRST", feedback,
                  "", "Your earlier changes are still in the working tree. Build on them, do not start over unless they are wrong."]
    lines += ["", "Execute the task now. Finish with the JSON report."]
    return "\n".join(lines)


# --------------------------------------------------------------------------- running an agent

def agent_command(cfg, level_name, system_prompt, sel):
    cmd = [cfg["claude_bin"], "-p", "--output-format", "stream-json", "--verbose",
           "--append-system-prompt", system_prompt,
           "--json-schema", json.dumps(read_json(SCHEMA_PATH)),
           "--permission-mode", cfg["permission_mode"],
           "--permission-prompts", "none"]
    if cfg["permission_mode"] != "bypassPermissions" and cfg.get("allowed_tools"):
        cmd += ["--allowedTools", " ".join(cfg["allowed_tools"])]
    if cfg.get("disallowed_tools"):
        cmd += ["--disallowedTools", " ".join(cfg["disallowed_tools"])]
    if sel.get("model"):
        cmd += ["--model", sel["model"]]
    if sel.get("effort"):
        cmd += ["--effort", sel["effort"]]
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


CURRENT = {"proc": None}


def run_agent(hive, task_id, cmd, prompt, env, log_path, timeout_s, mock, redact):
    """Run one agent process. Returns dict(report, cost_usd, error, session_id)."""
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    if mock:
        cmd = shlex.split(mock)
    result_event, err = None, None
    timed_out = threading.Event()
    with open(log_path, "a", encoding="utf-8") as log:
        log.write(json.dumps({"hive": "attempt_start", "ts": now_iso(), "model": cmd[cmd.index("--model") + 1] if "--model" in cmd else None}) + "\n")
        proc = subprocess.Popen(cmd, cwd=hive.workdir, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, errors="replace", **popen_group_kwargs())
        CURRENT["proc"] = proc
        write_json(hive.p("run", task_id + ".json"), {"engine_pid": os.getpid(), "agent_pid": proc.pid})

        def on_timeout():
            timed_out.set()
            kill_tree(proc.pid)
        timer = threading.Timer(timeout_s, on_timeout)
        timer.daemon = True
        timer.start()
        try:
            try:
                proc.stdin.write(prompt)
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            for line in proc.stdout:
                log.write(redact(line))
                log.flush()
                s = line.strip()
                if s.startswith("{"):
                    with contextlib.suppress(ValueError):
                        ev = json.loads(s)
                        if ev.get("type") == "result":
                            result_event = ev
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                kill_tree(proc.pid, hard=True)
                proc.wait()
        finally:
            timer.cancel()
            CURRENT["proc"] = None
    if timed_out.is_set():
        err = "timeout after %.1f min" % (timeout_s / 60.0)
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
    if status not in ("PASS", "FAIL", "BLOCKED"):
        return None
    rep["status"] = status
    for k in ("deliverables", "checks", "issues", "children"):
        if not isinstance(rep.get(k), list):
            rep[k] = []
    rep.setdefault("summary", "")
    return rep


def run_verify(hive, commands, timeout_s, extra_env=None):
    results = []
    env = dict(os.environ, CI="1")
    env.update(extra_env or {})
    redact = redactor(extra_env or {})
    for c in commands or []:
        t0 = time.time()
        code, out = run(c, hive.workdir, timeout=timeout_s, shell=True, env=env)
        results.append({"command": c, "exit_code": code, "seconds": round(time.time() - t0, 1), "output_tail": redact(tail(out, 2500))})
    return results


# --------------------------------------------------------------------------- notifications and lessons

def notify(hive, event, title, message, status=None, task_id=None):
    cmd = hive.config.get("notify_command")
    if not cmd:
        return
    env = dict(os.environ, HIVE_EVENT=event, HIVE_TITLE=title or "", HIVE_MESSAGE=message or "",
               HIVE_STATUS=status or "", HIVE_TASK_ID=task_id or "", HIVE_PROJECT=hive.root)
    with contextlib.suppress(Exception):
        subprocess.Popen(cmd, shell=True, cwd=hive.root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         executable=shell_exe())


def record_lessons(hive, role_id, attempts_log, final):
    first_fail = next((a for a in attempts_log if a["final"] == "FAIL" and a["reasons"]), None)
    if not first_fail:
        return
    entries = [{"role": role_id, "reason": r[:300], "outcome": final, "ts": now_iso()} for r in first_fail["reasons"][:3]]
    for path in [hive.p("lessons.json")] + ([os.path.join(global_dir(), "lessons.json")] if hive.config.get("global_lessons") else []):
        with contextlib.suppress(OSError):
            with file_lock(path + ".lock"):
                data = read_json(path, [])
                data = (data + entries)[-500:]
                write_json(path, data)


# --------------------------------------------------------------------------- task table, liveness, prior progress

def task_table(hive, events=None):
    tasks = {}
    for e in events if events is not None else hive.events():
        tid = e.get("task_id")
        if not tid:
            continue
        t = tasks.setdefault(tid, {"task_id": tid, "status": "RUNNING", "cost_usd": 0.0, "attempts": 0,
                                   "model": None, "effort": None, "source": None})
        ev = e.get("event")
        if ev == "task_start":
            t.update({k: e.get(k) for k in ("role", "level", "parent_task", "title", "ts", "engine_pid", "start_sha")})
        elif ev == "attempt_start":
            t["attempts"] = e.get("attempt", t["attempts"])
            t["model"], t["effort"], t["source"] = e.get("model"), e.get("effort"), e.get("source")
        elif ev in ("attempt_end", "bid"):
            t["cost_usd"] += e.get("cost_usd") or 0
        elif ev == "task_end":
            t["status"] = e.get("status")
            t["role"] = t.get("role") or e.get("role")
            t["duration_s"] = e.get("duration_s")
            if e.get("reused_from"):
                t["reused_from"] = e["reused_from"]
    for t in tasks.values():
        if t["status"] == "RUNNING" and t.get("engine_pid") and not pid_alive(t["engine_pid"]):
            t["status"] = "DEAD"
    return tasks


def descendants_of(tasks, task_id):
    out = []
    for t in tasks.values():
        if t.get("parent_task") == task_id:
            out.append(t)
            out.extend(descendants_of(tasks, t["task_id"]))
    return out


def prior_progress(hive, role_id, current_task, tasks):
    """PASSED descendants of the latest unfinished earlier run of this role, still present in git."""
    prior = [t for t in tasks.values() if t.get("role") == role_id and t["task_id"] != current_task
             and t["status"] in ("FAIL", "ABORTED", "DEAD", "BLOCKED")]
    if not prior:
        return []
    latest = sorted(prior, key=lambda t: t["task_id"])[-1]
    out = []
    for d in sorted(descendants_of(tasks, latest["task_id"]), key=lambda x: x["task_id"]):
        if d["status"] != "PASS":
            continue
        rep = read_json(hive.p("reports", d["task_id"] + ".json"), {})
        end = rep.get("end_sha")
        if end and end != rep.get("start_sha") and not is_ancestor(end, hive.workdir):
            continue  # rolled back since
        out.append({"task_id": d["task_id"], "role": d.get("role"), "title": d.get("title"),
                    "summary": (rep.get("summary") or "")[:300], "files": (rep.get("files_changed") or [])[:15]})
    return out


def brief_hash(brief):
    clean = {k: v for k, v in brief.items() if k not in ("task_id", "parent_task", "caller", "no_cache")}
    return hashlib.sha256(json.dumps(clean, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def find_reusable(hive, bhash):
    for e in reversed(hive.events()):
        if e.get("event") == "task_end" and e.get("status") == "PASS" and e.get("brief_hash") == bhash:
            rep = read_json(hive.p("reports", e["task_id"] + ".json"))
            if rep and (not rep.get("end_sha") or is_ancestor(rep["end_sha"], hive.workdir)):
                return rep
    return None


# --------------------------------------------------------------------------- dispatch

def load_brief(args):
    if args.brief:
        brief = read_json(args.brief)
        if brief is None:
            die("brief file not found or invalid JSON: %s" % args.brief)
    elif args.brief_json:
        brief = json.loads(args.brief_json)
    else:
        die("provide --brief FILE or --brief-json STRING")
    if not isinstance(brief, dict) or not brief.get("role") or not brief.get("title"):
        die("brief must be a JSON object with at least 'role' and 'title'")
    return brief


def install_abort_handlers():
    def handler(signum, _frame):
        proc = CURRENT.get("proc")
        if proc is not None:
            kill_tree(proc.pid)
        raise AbortRequested("signal %d" % signum)
    for name in ("SIGTERM", "SIGINT", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is not None:
            with contextlib.suppress(ValueError, OSError):
                signal.signal(sig, handler)


def stop_descendants(hive, task_id):
    tasks = task_table(hive)
    running = [t for t in descendants_of(tasks, task_id) if t["status"] == "RUNNING"]
    for t in sorted(running, key=lambda x: -(x.get("level") or 0)):
        signal_pid(t.get("engine_pid"))


def dispatch(hive, roster, brief, retries_override=None, dry_run=False):
    cfg = hive.config
    caller = os.environ.get("HIVE_CALLER_ROLE", "orchestrator")
    parent_task = os.environ.get("HIVE_TASK_ID")
    depth = int(os.environ.get("HIVE_DEPTH", "0"))
    role = roster.get(brief["role"])
    caller_role = roster.get(caller)

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
    bhash = brief_hash(brief)
    scope, scope_source = resolve_scope(hive, roster, role, brief)
    own = hive.ownership()
    shared = list(own.get("_shared") or DEFAULT_SHARED)
    ignore = DEFAULT_IGNORE + list(own.get("_ignore") or [])
    write_json(hive.p("tasks", task_id + ".json"), dict(brief, task_id=task_id, parent_task=parent_task, caller=caller))
    os.makedirs(hive.p("briefs", task_id), exist_ok=True)
    system_prompt = compose_system_prompt(hive, roster, role, task_id, scope)
    mock = os.environ.get("HIVE_MOCK_AGENT")
    secrets = hive.secrets()
    redact = redactor(secrets)

    if dry_run:
        pol = level_policy(cfg, level_name)
        key, ov = find_override(cfg, role, level_name)
        sel = {"model": (ov or {}).get("model") or pol["default_model"], "effort": (ov or {}).get("effort") or pol["default_effort"],
               "source": "dry-run (bid not run)"}
        cmd = agent_command(cfg, level_name, system_prompt, sel)
        return {"task_id": task_id, "role": role["id"], "scope": scope, "scope_source": scope_source,
                "policy": pol, "override": ov, "mode": cfg["model_selection"]["mode"],
                "command": [c if len(c) < 200 else c[:200] + "..." for c in cmd],
                "system_prompt": system_prompt, "user_prompt": compose_user_prompt(brief, task_id)}, 0

    start_event = {"event": "task_start", "task_id": task_id, "role": role["id"], "level": role["level"],
                   "parent_task": parent_task, "caller": caller, "title": brief["title"], "workdir": hive.workdir,
                   "engine_pid": os.getpid(), "brief_hash": bhash}

    # reuse an identical brief that already passed and still verifies
    if cfg.get("reuse_verified_briefs") and not brief.get("no_cache"):
        prev = find_reusable(hive, bhash)
        if prev:
            verification = run_verify(hive, brief.get("verify"), cfg["verify_timeout_min"] * 60, secrets)
            if all(v["exit_code"] == 0 for v in verification):
                hive.ledger(dict(start_event, start_sha=head_sha(hive.workdir)))
                rep = dict(prev, task_id=task_id, parent_task=parent_task, status="PASS", reused_from=prev["task_id"],
                           attempts=0, cost_usd=0.0, duration_s=0.0,
                           summary="Reused verified result of %s (identical brief, verify still green). %s" % (prev["task_id"], prev.get("summary") or ""),
                           verification=[{k: v[k] for k in ("command", "exit_code", "seconds")} for v in verification],
                           children=prev.get("children") or [], accepted_child_failures=[])
                write_json(hive.p("reports", task_id + ".json"), rep)
                hive.ledger({"event": "task_end", "task_id": task_id, "role": role["id"], "status": "PASS", "cost_usd": 0,
                             "duration_s": 0, "brief_hash": bhash, "reused_from": prev["task_id"]})
                return rep, 0

    spent = hive.total_cost()
    if cfg.get("max_total_usd") and spent >= cfg["max_total_usd"]:
        report = {"task_id": task_id, "role": role["id"], "title": brief["title"], "status": "BLOCKED", "parent_task": parent_task,
                  "summary": "Global budget ceiling reached (%.2f of %.2f USD). Raise it with: hive.py config set max_total_usd <amount>" % (spent, cfg["max_total_usd"]),
                  "attempts": 0, "failure_reasons": ["budget ceiling"]}
        hive.ledger(dict(start_event, start_sha=head_sha(hive.workdir)))
        write_json(hive.p("reports", task_id + ".json"), report)
        hive.ledger({"event": "task_end", "task_id": task_id, "role": role["id"], "status": "BLOCKED", "reason": "budget"})
        notify(hive, "budget", "Hive budget ceiling reached", report["summary"], "BLOCKED", task_id)
        return report, EXIT["BLOCKED"]

    ensure_identity(hive.workdir)
    if is_dirty(hive.workdir) and cfg.get("auto_commit"):
        commit_all(hive.workdir, "hive(checkpoint): work in progress by %s before %s" % (caller, task_id))
    start_sha = head_sha(hive.workdir)
    tasks_before = task_table(hive)
    prior = prior_progress(hive, role["id"], task_id, tasks_before) if role["level"] in (1, 2) else []

    env = dict(os.environ)
    env.update(secrets)
    env.update({
        "HIVE_ENGINE": ENGINE, "HIVE_STATE": hive.state, "HIVE_WORKDIR": hive.workdir,
        "HIVE_CALLER_ROLE": role["id"], "HIVE_TASK_ID": task_id, "HIVE_DEPTH": str(role["level"]),
        "BASH_DEFAULT_TIMEOUT_MS": str(int(cfg["timeout_min"]["worker"] * 60000)),
        "BASH_MAX_TIMEOUT_MS": str(int(max(cfg["timeout_min"].values()) * 60000 + 600000)),
        "CI": "1",
    })
    hive.ledger(dict(start_event, start_sha=start_sha, prior_progress=[p["task_id"] for p in prior]))
    install_abort_handlers()

    retries = cfg["retries"].get(level_name, 0) if retries_override is None else retries_override
    timeout_s = float(cfg["timeout_min"][level_name]) * 60
    verify_timeout = cfg["verify_timeout_min"] * 60
    log_path = hive.p("logs", task_id + ".jsonl")
    pol = level_policy(cfg, level_name)
    t0 = time.time()
    feedback, attempts, total_cost = None, 0, 0.0
    report, verification, reasons, session_id = None, [], [], None
    attempts_log, selections, sel = [], [], None
    final, aborted = "FAIL", None

    try:
        while True:
            attempts += 1
            if sel is None:
                sel = select_model(hive, cfg, role, brief, level_name, task_id, mock)
            elif not sel.get("locked") and cfg["model_selection"].get("escalate_on_retry", True):
                sel = escalate(sel, pol)
            selections.append({k: sel.get(k) for k in ("model", "effort", "source", "rationale", "complexity")})
            cmd = agent_command(cfg, level_name, system_prompt, sel)
            hive.ledger({"event": "attempt_start", "task_id": task_id, "attempt": attempts, "model": sel["model"],
                         "effort": sel["effort"], "source": sel["source"]})
            res = run_agent(hive, task_id, cmd, compose_user_prompt(brief, task_id, feedback, prior, sel), env,
                            log_path, timeout_s, mock, redact)
            total_cost += res["cost_usd"]
            session_id = res["session_id"] or session_id
            report = normalize_report(res["report"])
            reasons = []
            if res["error"]:
                reasons.append(res["error"])
            if report is None:
                reasons.append("agent returned no valid report")
                report = {"status": "FAIL", "summary": "No valid report returned.", "deliverables": [], "checks": [], "issues": [], "children": []}

            verification = run_verify(hive, brief.get("verify"), verify_timeout, secrets) if report["status"] != "BLOCKED" else []
            for v in verification:
                if v["exit_code"] != 0:
                    reasons.append("engine verification failed: `%s` exited %d" % (v["command"], v["exit_code"]))
            files = changed_files(hive.workdir, start_sha)
            out_of_scope = [f for f in files if not path_in_scope(f, scope) and not path_in_scope(f, shared, basename_match=True)
                            and not path_in_scope(f, ignore)]
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
            attempts_log.append({"attempt": attempts, "final": final, "reasons": list(reasons)})
            hive.ledger({"event": "attempt_end", "task_id": task_id, "attempt": attempts, "self_status": report["status"],
                         "final": final, "cost_usd": round(res["cost_usd"], 4), "reasons": reasons,
                         "model": sel["model"], "effort": sel["effort"]})

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
            done = [t for t in task_table(hive).values() if t.get("parent_task") == task_id and t["status"] == "PASS"]
            if done:
                fb += ["", "Children that already PASSED in your earlier attempt. Their work is committed. Do NOT dispatch them again:"]
                fb += ["* `%s` (%s) %s" % (t["task_id"], t.get("role"), t.get("title")) for t in sorted(done, key=lambda x: x["task_id"])]
                fb += ["List them in your final `children` array with their real status."]
            feedback = "\n".join(fb)
    except AbortRequested as exc:
        aborted = str(exc)
        final = "ABORTED"
        reasons = ["aborted by %s" % aborted]
        stop_descendants(hive, task_id)
        if report is None:
            report = {"status": "FAIL", "summary": "Aborted before a report was produced.", "deliverables": [], "checks": [], "issues": [], "children": []}

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
                                    if t.get("parent_task") == task_id and t["status"] in ("FAIL", "BLOCKED", "ABORTED", "DEAD")] if final == "PASS" else [],
        "model_selection": selections, "prior_progress": [p["task_id"] for p in prior],
        "verification": [{k: v[k] for k in ("command", "exit_code", "seconds")} for v in verification],
        "scope": scope, "scope_source": scope_source, "files_changed": files,
        "start_sha": start_sha, "end_sha": end_sha, "stash": stash_ref,
        "cost_usd": round(total_cost, 4), "duration_s": round(time.time() - t0, 1),
        "session_id": session_id, "log": os.path.relpath(log_path, hive.root),
    }
    write_json(hive.p("reports", task_id + ".json"), dict(final_report, verification_detail=verification))
    hive.ledger({"event": "task_end", "task_id": task_id, "role": role["id"], "status": final, "brief_hash": bhash,
                 "cost_usd": final_report["cost_usd"], "duration_s": final_report["duration_s"]})
    with contextlib.suppress(OSError):
        os.remove(hive.p("run", task_id + ".json"))
    record_lessons(hive, role["id"], attempts_log, final)
    if role["level"] == 1 or final in ("BLOCKED", "ABORTED"):
        notify(hive, "task_end", "%s %s" % (final, role["title"]), report.get("summary") or "", final, task_id)
    return final_report, EXIT[final]


# --------------------------------------------------------------------------- batch (parallel in worktrees)

def batch(hive, roster, brief_paths, parallel):
    if parallel <= 1:
        results = []
        for bp in brief_paths:
            b = read_json(bp)
            if not b:
                die("brief not found: %s" % bp)
            rep, _ = dispatch(hive, roster, b)
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

    procs = []

    def on_signal(signum, _frame):
        for p in procs:
            signal_pid(p.pid)
        raise AbortRequested("signal %d" % signum)
    for name in ("SIGTERM", "SIGINT", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is not None:
            with contextlib.suppress(ValueError, OSError):
                signal.signal(sig, on_signal)

    def work(item):
        bp, b, path, branch = item
        env = dict(os.environ, HIVE_WORKDIR=path, HIVE_STATE=hive.state)
        p = subprocess.Popen([sys.executable, ENGINE, "dispatch", "--brief", os.path.abspath(bp)], cwd=path, env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
        procs.append(p)
        out, _ = p.communicate()
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


# --------------------------------------------------------------------------- status, reports, estimate, trace

MARKS = {"PASS": "PASS ", "FAIL": "FAIL ", "BLOCKED": "BLOCK", "RUNNING": "RUN  ", "ABORTED": "ABORT", "DEAD": "DEAD "}


def tree_lines(hive, tasks=None):
    tasks = tasks if tasks is not None else task_table(hive)
    kids = {}
    for t in tasks.values():
        kids.setdefault(t.get("parent_task"), []).append(t)
    lines = []

    def walk(parent, indent):
        for t in sorted(kids.get(parent, []), key=lambda x: x["task_id"]):
            extra = []
            if t.get("model"):
                extra.append("%s/%s" % (t["model"], t.get("effort")))
            extra.append("$%.2f" % t["cost_usd"])
            if t["attempts"] > 1:
                extra.append("%d attempts" % t["attempts"])
            if t.get("reused_from"):
                extra.append("reused %s" % t["reused_from"])
            lines.append((indent, t, "%s[%s] %s  %s  (%s, %s)" % ("    " * indent, MARKS.get(t["status"], t["status"]),
                                                                  t["task_id"], t.get("title") or "", t.get("role"), ", ".join(extra))))
            walk(t["task_id"], indent + 1)
    walk(None, 0)
    return lines, tasks


def print_tree(hive):
    lines, tasks = tree_lines(hive)
    if not tasks:
        return "No tasks dispatched yet."
    counts = {}
    for t in tasks.values():
        counts[t["status"]] = counts.get(t["status"], 0) + 1
    cfg = hive.config
    out = [l[2] for l in lines] + ["", "tasks: %d  %s  total spend: $%.2f of $%s ceiling" % (
        len(tasks), "  ".join("%s=%d" % kv for kv in sorted(counts.items())), hive.total_cost(), cfg.get("max_total_usd"))]
    return "\n".join(out)


def model_usage(hive):
    usage = {}
    for e in hive.events():
        if e.get("event") == "attempt_end":
            k = "%s/%s" % (e.get("model"), e.get("effort"))
            u = usage.setdefault(k, {"attempts": 0, "pass": 0, "cost_usd": 0.0})
            u["attempts"] += 1
            u["pass"] += 1 if e.get("final") == "PASS" else 0
            u["cost_usd"] += e.get("cost_usd") or 0
        elif e.get("event") == "bid":
            u = usage.setdefault("bidder", {"attempts": 0, "pass": 0, "cost_usd": 0.0})
            u["attempts"] += 1
            u["cost_usd"] += e.get("cost_usd") or 0
    return usage


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
          "## Outcome by level", "", "| Level | PASS | FAIL | BLOCKED | ABORTED | DEAD | RUNNING |", "|---|---|---|---|---|---|---|"]
    for lvl in ("manager", "supervisor", "worker"):
        c = by_level.get(lvl, {})
        md.append("| %s | %s |" % (lvl, " | ".join(str(c.get(s, 0)) for s in ("PASS", "FAIL", "BLOCKED", "ABORTED", "DEAD", "RUNNING"))))
    usage = model_usage(hive)
    if usage:
        md += ["", "## Model usage", "", "| Model/effort | Attempts | Passed | Spend |", "|---|---|---|---|"]
        for k, u in sorted(usage.items(), key=lambda kv: -kv[1]["cost_usd"]):
            md.append("| %s | %d | %d | $%.2f |" % (k, u["attempts"], u["pass"], u["cost_usd"]))
    if gates:
        md += ["", "## Phase gates", "", "| Phase | Result | Commands |", "|---|---|---|"]
        for g in gates:
            md.append("| %s | %s | %s |" % (g.get("phase"), "PASS" if g.get("passed") else "FAIL",
                                           ", ".join("`%s` %d" % (c["command"], c["exit_code"]) for c in g.get("results", []))))
    md += ["", "## Chain of command", "", "```", print_tree(hive), "```"]
    problems = [r for r in reports.values() if r.get("status") in ("FAIL", "BLOCKED", "ABORTED")]
    dead = [t for t in tasks.values() if t["status"] == "DEAD"]
    if problems or dead:
        md += ["", "## Failures and blockers", ""]
        for r in problems:
            md.append("### %s %s (%s)" % (r.get("status"), r.get("task_id"), r.get("role")))
            md.append(r.get("summary") or "")
            for reason in r.get("failure_reasons") or []:
                md.append("* %s" % reason)
            if r.get("stash"):
                md.append("* Failed work preserved in git stash `%s`" % r["stash"])
            md.append("")
        for t in dead:
            md.append("* DEAD `%s` (%s): its process ended without reporting. Re-dispatch or run `hive stop` to clean up." % (t["task_id"], t.get("role")))
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


def active_roles(hive, roster):
    bp = read_json(hive.p("blueprint.json"), {})
    platforms = set(bp.get("platforms") or ["web"])
    managers = [m for ph in bp.get("phases", []) for m in ph.get("managers", [])] or \
        [r["id"] for r in roster.children("orchestrator") if r["id"] != "mgr-mobile"]
    out = []
    for mid in managers:
        if mid not in roster.roles:
            continue
        out.append(roster.roles[mid])
        for d in roster.descendants(mid):
            anc = d if d["level"] == 2 else roster.roles.get(d.get("parent"))
            if anc and anc["id"] == "sup-android" and "android" not in platforms:
                continue
            if anc and anc["id"] == "sup-ios" and "ios" not in platforms:
                continue
            out.append(d)
    return out, managers


def estimate(hive, roster):
    cfg = hive.config
    roles, managers = active_roles(hive, roster)
    observed = {}
    for t in task_table(hive).values():
        if t["status"] in FINAL_STATES and t.get("level"):
            observed.setdefault(LEVEL_NAMES[t["level"]], []).append(t["cost_usd"])
    rows, total = {}, 0.0
    for r in roles:
        lvl = LEVEL_NAMES[r["level"]]
        pol = level_policy(cfg, lvl)
        _, ov = find_override(cfg, r, lvl)
        model = (ov or {}).get("model") or pol["default_model"]
        if len(observed.get(lvl, [])) >= 3:
            per = sum(observed[lvl]) / len(observed[lvl])
            basis = "observed"
        else:
            per = cfg["cost_model_usd"].get(model, 1.5) * cfg["cost_level_multiplier"].get(lvl, 1.0)
            basis = "model table"
        tasks_per_role = 1.5 if lvl == "worker" else 1.0
        cost = per * tasks_per_role * 1.2
        mgr = r["id"] if r["level"] == 1 else next((m for m in managers if r in roster.descendants(m)), "?")
        row = rows.setdefault(mgr, {"roles": 0, "usd": 0.0, "basis": set()})
        row["roles"] += 1
        row["usd"] += cost
        row["basis"].add(basis)
        total += cost
    total += 0.003 * len(roles) * 1.5 if cfg["model_selection"]["mode"] == "bid" else 0
    return {"managers": {k: {"roles": v["roles"], "usd": round(v["usd"], 2), "basis": sorted(v["basis"])} for k, v in rows.items()},
            "expected_usd": round(total, 2), "low_usd": round(total * 0.6, 2), "high_usd": round(total * 2.0, 2),
            "ceiling_usd": cfg.get("max_total_usd"), "spent_usd": hive.total_cost(),
            "note": "Rough. Real cost depends on plan size, retries and bids. Becomes observed per level after 3 finished tasks."}


def trace(hive):
    ac_text = read_text(os.path.join(hive.workdir, "docs", "product", "acceptance-criteria.md"))
    tr_path = os.path.join(hive.workdir, "docs", "quality", "traceability.md")
    tr_text = read_text(tr_path)
    ids = sorted(set(re.findall(r"\bAC-\d{1,4}\b", ac_text)), key=lambda x: int(x[3:]))
    if not ids:
        return {"ok": False, "error": "no AC-### ids found in docs/product/acceptance-criteria.md", "ids": 0}
    if not tr_text:
        return {"ok": False, "error": "docs/quality/traceability.md is missing", "ids": len(ids), "missing": ids}
    missing = [i for i in ids if not re.search(r"\b%s\b" % re.escape(i), tr_text)]
    broken = []
    for line in tr_text.splitlines():
        if not re.search(r"\bAC-\d", line):
            continue
        for ref in re.findall(r"`([^`\s]+)`", line):
            path = ref.split("::")[0].split(":")[0]
            if ("/" in path or re.search(r"\.\w{1,5}$", path)) and not re.match(r"^(npm|npx|pnpm|yarn|python|pytest|node)\b", path):
                if not os.path.exists(os.path.join(hive.workdir, path)):
                    broken.append(path)
    return {"ok": not missing and not broken, "ids": len(ids), "missing": missing, "broken_test_refs": sorted(set(broken))}


def toolchain_check(hive):
    tc = read_json(os.path.join(hive.workdir, "docs", "architecture", "toolchain.json"), {})
    results = []
    for req in tc.get("requires", []):
        binary = req.get("bin")
        path = shutil.which(binary) if binary else None
        ok, version = bool(path), None
        if path and req.get("version_cmd"):
            code, out = run(req["version_cmd"], hive.workdir, timeout=30, shell=True)
            m = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", out or "")
            version = ".".join(x for x in m.groups() if x) if m else None
            if req.get("min") and version:
                have = tuple(int(x) for x in version.split("."))
                need = tuple(int(x) for x in re.findall(r"\d+", req["min"]))
                ok = have[:len(need)] >= need
        results.append({"bin": binary, "found": bool(path), "version": version, "min": req.get("min"), "ok": ok,
                        "install_hint": req.get("install_hint"), "optional": bool(req.get("optional"))})
    return results


def cli_flag_check(cfg):
    claude = shutil.which(cfg["claude_bin"])
    if not claude:
        return None, ["claude CLI not found"]
    code, out = run([claude, "--help"], os.getcwd(), timeout=60)
    missing = [f for f in REQUIRED_CLI_FLAGS if f not in out]
    return run([claude, "--version"], os.getcwd(), timeout=60)[1].strip(), missing


# --------------------------------------------------------------------------- dashboard

def dashboard_html(hive):
    lines, tasks = tree_lines(hive)
    cfg = hive.config
    bp = read_json(hive.p("blueprint.json"), {})
    colors = {"PASS": "#16a34a", "FAIL": "#dc2626", "BLOCKED": "#d97706", "RUNNING": "#2563eb", "ABORTED": "#6b7280", "DEAD": "#7f1d1d"}
    rows = []
    for indent, t, _ in lines:
        rows.append('<tr><td style="padding-left:%dpx"><span class="b" style="background:%s">%s</span> %s</td><td>%s</td><td>%s</td><td>$%.2f</td><td>%s</td></tr>' % (
            12 + indent * 24, colors.get(t["status"], "#444"), t["status"], html.escape(t.get("title") or ""),
            html.escape(t.get("role") or ""), html.escape("%s/%s" % (t.get("model"), t.get("effort")) if t.get("model") else ""),
            t["cost_usd"], t["attempts"]))
    gates = read_json(hive.p("gates.json"), [])
    gate_html = " ".join('<span class="b" style="background:%s">%s</span>' % ("#16a34a" if g.get("passed") else "#dc2626", html.escape(g.get("phase", ""))) for g in gates)
    counts = {}
    for t in tasks.values():
        counts[t["status"]] = counts.get(t["status"], 0) + 1
    return """<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="refresh" content="5">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Hive Dashboard</title>
<style>body{font:14px system-ui,sans-serif;margin:0;padding:16px;background:#0b1020;color:#e5e7eb}
h1{font-size:20px;margin:0 0 4px}.m{color:#9ca3af;margin-bottom:12px}table{width:100%%;border-collapse:collapse}
td,th{padding:6px 8px;border-bottom:1px solid #1f2937;text-align:left;white-space:nowrap}th{color:#9ca3af;font-weight:500}
.b{display:inline-block;padding:1px 6px;border-radius:4px;color:#fff;font-size:11px;font-weight:600}
.wrap{overflow-x:auto}</style></head><body>
<h1>Hive: %s</h1><div class="m">%s &middot; spend $%.2f of $%s &middot; %s &middot; refreshed %s</div>
<div class="m">Gates: %s</div><div class="wrap"><table><tr><th>Task</th><th>Role</th><th>Model</th><th>Spend</th><th>Attempts</th></tr>%s</table></div>
</body></html>""" % (html.escape(bp.get("product_name", "build")), ", ".join("%s %d" % kv for kv in sorted(counts.items())) or "no tasks",
                     hive.total_cost(), cfg.get("max_total_usd"), html.escape(cfg["model_selection"]["mode"] + " mode"),
                     now_iso(), gate_html or "none yet", "".join(rows) or '<tr><td colspan="5">No tasks yet</td></tr>')


# --------------------------------------------------------------------------- config helpers

def config_path(hive, global_scope):
    return os.path.join(global_dir(), "config.json") if global_scope else hive.p("config.json")


def set_dotted(data, key, value):
    parts = key.split(".")
    cur = data
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


def unset_dotted(data, key):
    parts = key.split(".")
    cur = data
    for p in parts[:-1]:
        cur = cur.get(p, {})
    cur.pop(parts[-1], None)


def parse_value(v):
    try:
        return json.loads(v)
    except ValueError:
        return v


def edit_config(hive, global_scope, fn):
    path = config_path(hive, global_scope)
    with file_lock(path + ".lock"):
        data = read_json(path, {})
        fn(data)
        write_json(path, data)
    return path


# --------------------------------------------------------------------------- commands

def cmd_init(args):
    hive = Hive()
    for d in ("tasks", "reports", "logs", "briefs", "locks", "wt", "run"):
        os.makedirs(hive.p(d), exist_ok=True)
    if args.plan:
        src = sys.stdin.read() if args.plan == "-" else read_text(args.plan)
        if not src:
            die("plan is empty or not found: %s" % args.plan)
        with open(hive.p("plan.md"), "w", encoding="utf-8") as fh:
            fh.write(src)
    if not os.path.exists(hive.p("config.json")):
        write_json(hive.p("config.json"), {})
    gi = os.path.join(hive.workdir, ".gitignore")
    existing = read_text(gi)
    have = set(existing.splitlines())
    add = [l for l in GITIGNORE_DEFAULTS if l not in have]
    if add:
        with open(gi, "a", encoding="utf-8") as fh:
            fh.write(("" if existing.endswith("\n") or not existing else "\n") + "\n".join(add) + "\n")
    if git(["rev-parse", "--git-dir"], hive.workdir)[0] != 0:
        git(["init", "-q"], hive.workdir, check=True)
    ensure_identity(hive.workdir)
    if head_sha(hive.workdir) is None or is_dirty(hive.workdir):
        commit_all(hive.workdir, "hive(init): start of Hive build")
    print(json.dumps({"initialized": hive.state, "plan": os.path.exists(hive.p("plan.md")),
                      "config": hive.p("config.json"), "head": head_sha(hive.workdir)}, indent=2))


def cmd_doctor(args):
    hive = Hive()
    cfg = hive.config
    checks = [("python >= 3.8", sys.version_info >= (3, 8), sys.version.split()[0], True),
              ("git on PATH", shutil.which("git") is not None, shutil.which("git") or "missing", True)]
    ver, missing = cli_flag_check(cfg)
    checks.append(("claude CLI on PATH", ver is not None, ver or "missing", True))
    if ver is not None:
        checks.append(("claude CLI flags", not missing, "all %d required flags present" % len(REQUIRED_CLI_FLAGS) if not missing else "missing: %s" % missing, True))
    errs = Roster().validate()
    checks.append(("roster valid", not errs, "; ".join(errs) or json.dumps(Roster().counts()), True))
    checks.append(("inside git repo", git(["rev-parse", "--git-dir"], hive.workdir)[0] == 0, hive.workdir, False))
    checks.append((".hive initialized", os.path.isdir(hive.state), hive.state, False))
    if IS_WINDOWS:
        checks.append(("platform", True, "Windows: best effort support (tested on Linux and macOS)", False))
    if args.project:
        tc = toolchain_check(hive)
        if not tc:
            checks.append(("project toolchain", True, "no docs/architecture/toolchain.json yet", False))
        for t in tc:
            detail = "%s %s%s" % ("found" if t["found"] else "MISSING", t["version"] or "", " (need >= %s)" % t["min"] if t["min"] else "")
            if not t["ok"] and t.get("install_hint"):
                detail += "  install: %s" % t["install_hint"]
            checks.append(("tool: %s" % t["bin"], t["ok"], detail, not t["optional"]))
    ok = True
    for name, passed, detail, required in checks:
        print("%s  %-22s %s" % ("OK  " if passed else ("FAIL" if required else "WARN"), name, detail))
        ok = ok and (passed or not required)
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
    try:
        results = batch(hive, roster, args.briefs, args.parallel)
    except AbortRequested:
        print(json.dumps({"results": [], "aborted": True}))
        sys.exit(EXIT["ABORTED"])
    summary = [{k: r.get(k) for k in ("task_id", "role", "title", "status", "failure_reasons", "cost_usd")} for r in results]
    print(json.dumps({"results": summary}, indent=2, ensure_ascii=False))
    sys.exit(max((EXIT.get(r.get("status"), 1) for r in results), default=0))


def cmd_gate(args):
    hive = Hive()
    cmds_map = read_json(os.path.join(hive.workdir, "docs", "architecture", "commands.json"), {})
    keys = args.keys or ["install", "lint", "typecheck", "test", "build"]
    commands = [cmds_map[k] for k in keys if isinstance(cmds_map.get(k), str) and cmds_map.get(k)]
    commands += args.extra or []
    tc = toolchain_check(hive)
    tc_fail = [t for t in tc if not t["ok"] and not t["optional"]]
    tr = trace(hive) if args.trace else None
    if not commands and not tc and not tr:
        print(json.dumps({"phase": args.phase, "passed": None, "note": "no commands.json yet, gate skipped"}))
        return
    results = run_verify(hive, commands, hive.config["verify_timeout_min"] * 60, hive.secrets())
    if tc:
        results.insert(0, {"command": "toolchain check", "exit_code": 1 if tc_fail else 0, "seconds": 0,
                           "output_tail": json.dumps(tc_fail or "all required tools present")})
    if tr is not None:
        results.append({"command": "acceptance traceability", "exit_code": 0 if tr["ok"] else 1, "seconds": 0, "output_tail": json.dumps(tr)})
    passed = all(r["exit_code"] == 0 for r in results)
    gates = read_json(hive.p("gates.json"), [])
    gates.append({"phase": args.phase, "passed": passed, "ts": now_iso(),
                  "results": [{k: r[k] for k in ("command", "exit_code", "seconds")} for r in results]})
    write_json(hive.p("gates.json"), gates)
    notify(hive, "gate", "Gate %s %s" % (args.phase, "PASS" if passed else "FAIL"),
           ", ".join("%s=%d" % (r["command"], r["exit_code"]) for r in results), "PASS" if passed else "FAIL")
    print(json.dumps({"phase": args.phase, "passed": passed, "results": results}, indent=2))
    sys.exit(0 if passed else 1)


def cmd_status(args):
    hive = Hive()
    if args.json:
        print(json.dumps(list(task_table(hive).values()), indent=2))
        return
    if not args.watch:
        print(print_tree(hive))
        return
    try:
        while True:
            sys.stdout.write("\033[2J\033[H" + "Hive status  (%s, Ctrl+C to exit)\n\n" % now_iso() + print_tree(hive) + "\n")
            sys.stdout.flush()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass


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


def cmd_stop(args):
    hive = Hive()
    tasks = task_table(hive)
    if args.task_id:
        if args.task_id not in tasks:
            die("unknown task %s" % args.task_id)
        targets = [tasks[args.task_id]] + descendants_of(tasks, args.task_id)
    else:
        targets = list(tasks.values())
    running = [t for t in targets if t["status"] == "RUNNING"]
    dead = [t for t in targets if t["status"] == "DEAD"]
    for t in sorted(running, key=lambda x: -(x.get("level") or 0)):
        signal_pid(t.get("engine_pid"))
    deadline = time.time() + args.grace
    while time.time() < deadline and any(pid_alive(t.get("engine_pid")) for t in running):
        time.sleep(0.3)
    forced = []
    for t in running:
        info = read_json(hive.p("run", t["task_id"] + ".json"), {})
        if pid_alive(t.get("engine_pid")) or pid_alive(info.get("agent_pid")):
            kill_tree(info.get("agent_pid"), hard=True)
            signal_pid(t.get("engine_pid"), hard=True)
            forced.append(t["task_id"])
    after = task_table(hive)
    for t in running + dead:
        if after.get(t["task_id"], {}).get("status") in ("RUNNING", "DEAD"):
            hive.ledger({"event": "task_end", "task_id": t["task_id"], "role": t.get("role"), "status": "ABORTED", "reason": "hive stop"})
    print(json.dumps({"stopped": [t["task_id"] for t in running], "forced": forced, "cleaned_dead": [t["task_id"] for t in dead]}, indent=2))


def cmd_models(args):
    hive, roster = Hive(), Roster()
    sub = args.models_cmd or "show"
    if sub == "set":
        if not args.model and not args.effort:
            die("give --model and/or --effort")
        target = "*" if args.target == "all" else args.target
        if target not in ("*", "manager", "supervisor", "worker"):
            roster.get(target)
        ov = {k: v for k, v in (("model", args.model), ("effort", args.effort)) if v}
        path = edit_config(hive, args.global_scope, lambda d: d.setdefault("model_selection", {}).setdefault("overrides", {}).__setitem__(target, ov))
        print(json.dumps({"override": {target: ov}, "written": path}))
        return
    if sub == "unset":
        target = "*" if args.target == "all" else args.target
        path = edit_config(hive, args.global_scope, lambda d: d.get("model_selection", {}).get("overrides", {}).pop(target, None))
        print(json.dumps({"removed_override": target, "written": path}))
        return
    if sub == "mode":
        path = edit_config(hive, args.global_scope, lambda d: set_dotted(d, "model_selection.mode", args.mode))
        print(json.dumps({"mode": args.mode, "written": path}))
        return
    if sub == "policy":
        def fn(d):
            if args.allowed:
                set_dotted(d, "model_selection.allowed.%s" % args.level, args.allowed)
            if args.max_effort:
                set_dotted(d, "model_selection.max_effort.%s" % args.level, args.max_effort)
            if args.default:
                set_dotted(d, "models.%s" % args.level, args.default)
            if args.default_effort:
                set_dotted(d, "effort.%s" % args.level, args.default_effort)
        path = edit_config(hive, args.global_scope, fn)
        print(json.dumps({"policy": level_policy(hive.config, args.level), "written": path}))
        return
    # show
    cfg = hive.config
    last = {}
    for t in task_table(hive).values():
        if t.get("model"):
            last[t.get("role")] = "%s/%s (%s)" % (t["model"], t.get("effort"), t.get("source"))
    mode = cfg["model_selection"]["mode"]
    print("mode: %s   (bid = each agent bids per task, parent = commander's choice in the brief, fixed = level default)" % mode)
    for lvl in ("manager", "supervisor", "worker"):
        pol = level_policy(cfg, lvl)
        print("%-10s allowed %-24s max effort %-6s default %s/%s" % (lvl, ",".join(pol["allowed"]), pol["max_effort"], pol["default_model"], pol["default_effort"]))
    print("escalate on retry: %s   bidder: %s/%s" % (cfg["model_selection"].get("escalate_on_retry"), cfg["model_selection"].get("bidder_model"), cfg["model_selection"].get("bidder_effort")))
    print("")
    print("%-28s %-11s %-26s %s" % ("role", "level", "policy", "last pick"))
    for r in sorted(roster.roles.values(), key=lambda x: (x["level"], x["id"])):
        if r["level"] == 0:
            continue
        lvl = LEVEL_NAMES[r["level"]]
        key, ov = find_override(cfg, r, lvl)
        policy = "override(%s) %s/%s" % (key, ov.get("model") or "-", ov.get("effort") or "-") if ov else mode
        print("%-28s %-11s %-26s %s" % (r["id"], lvl, policy, last.get(r["id"], "")))


def cmd_config(args):
    hive = Hive()
    if args.config_cmd == "set":
        path = edit_config(hive, args.global_scope, lambda d: set_dotted(d, args.key, parse_value(args.value)))
        print(json.dumps({"set": args.key, "value": parse_value(args.value), "written": path}))
    elif args.config_cmd == "unset":
        path = edit_config(hive, args.global_scope, lambda d: unset_dotted(d, args.key))
        print(json.dumps({"unset": args.key, "written": path}))
    else:
        print(json.dumps(hive.config, indent=2))


def cmd_secrets(args):
    hive = Hive()
    _, files = git(["ls-files", "--cached", "--others", "--exclude-standard"], hive.workdir)
    examples = [f for f in files.splitlines() if f.rsplit("/", 1)[-1] in (".env.example", ".env.sample", ".env.template")]
    required = {}
    for f in examples:
        for k in parse_env_file(read_text(os.path.join(hive.workdir, f))):
            required.setdefault(k, []).append(f)
    have = hive.secrets()
    rows = [{"key": k, "source_files": v, "present": bool(have.get(k) or os.environ.get(k))} for k, v in sorted(required.items())]
    if args.template:
        path = hive.config.get("secrets_file") or ".hive/secrets.env"
        path = path if os.path.isabs(path) else os.path.join(hive.root, path)
        existing = read_text(path)
        missing = [r["key"] for r in rows if not r["present"] and r["key"] not in parse_env_file(existing)]
        if missing:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(("" if existing.endswith("\n") or not existing else "\n") +
                         "# Fill in values. This file is git ignored and only passed to Hive agents as environment variables.\n" +
                         "\n".join("%s=" % k for k in missing) + "\n")
        print(json.dumps({"template": path, "added_keys": missing}))
        return
    print(json.dumps({"required": len(rows), "missing": [r["key"] for r in rows if not r["present"]], "keys": rows}, indent=2))


def cmd_estimate(_args):
    hive, roster = Hive(), Roster()
    est = estimate(hive, roster)
    print(json.dumps(est, indent=2))


def cmd_trace(_args):
    hive = Hive()
    tr = trace(hive)
    print(json.dumps(tr, indent=2))
    sys.exit(0 if tr["ok"] else 1)


def cmd_retro(_args):
    hive = Hive()
    tasks = task_table(hive)
    events = hive.events()
    by_role = {}
    for e in events:
        if e.get("event") == "attempt_end" and e.get("final") == "FAIL":
            role = tasks.get(e["task_id"], {}).get("role")
            by_role.setdefault(role, []).extend(e.get("reasons") or [])
    retried = [t for t in tasks.values() if t["attempts"] > 1]
    md = ["# Hive Retrospective", "", "Generated %s" % now_iso(), "",
          "Tasks: %d. Retried: %d. Spend: $%.2f." % (len(tasks), len(retried), hive.total_cost()), "",
          "## Failure reasons by role", ""]
    for role, reasons in sorted(by_role.items(), key=lambda kv: -len(kv[1])):
        md.append("### %s (%d failed attempts)" % (role, len(reasons)))
        counts = {}
        for r in reasons:
            k = re.sub(r"T\d{4}-[\w-]+", "<task>", r)[:200]
            counts[k] = counts.get(k, 0) + 1
        md += ["* %dx %s" % (n, k) for k, n in sorted(counts.items(), key=lambda kv: -kv[1])[:8]]
        md.append("")
    usage = model_usage(hive)
    if usage:
        md += ["## Model and effort outcomes", "", "| Model/effort | Attempts | Pass rate | Spend |", "|---|---|---|---|"]
        for k, u in sorted(usage.items()):
            rate = "%.0f%%" % (100.0 * u["pass"] / u["attempts"]) if u["attempts"] and k != "bidder" else "-"
            md.append("| %s | %d | %s | $%.2f |" % (k, u["attempts"], rate, u["cost_usd"]))
        md.append("")
    md += ["## Suggested actions", "",
           "* Roles with repeated identical failures: tighten their mission in roster.json or add a note to the brief template.",
           "* Model/effort rows with low pass rates: raise that level's floor with `hive models policy`.",
           "* Lessons are injected automatically into future prompts of the same role (.hive/lessons.json and ~/.hive/lessons.json)."]
    out = "\n".join(md) + "\n"
    with open(hive.p("RETRO.md"), "w", encoding="utf-8") as fh:
        fh.write(out)
    print(out)


def cmd_serve(args):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    hive = Hive()

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/api/status"):
                body = json.dumps({"tasks": list(task_table(hive).values()), "spend": hive.total_cost(),
                                   "gates": read_json(hive.p("gates.json"), [])}).encode()
                ctype = "application/json"
            else:
                body = dashboard_html(hive).encode()
                ctype = "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer((args.host, args.port), H)
    print("Hive dashboard on http://%s:%d  (Ctrl+C to stop)" % (args.host, srv.server_address[1]), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


def main():
    ap = argparse.ArgumentParser(prog="hive.py", description="Hive multi level agent engine for Claude Code")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create .hive state, store the plan, prepare git")
    s.add_argument("--plan", help="path to the plan file, or - for stdin")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("doctor", help="check prerequisites")
    s.add_argument("--project", action="store_true", help="also check the project toolchain from toolchain.json")
    s.set_defaults(fn=cmd_doctor)

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
    s.add_argument("--trace", action="store_true", help="also require acceptance criteria traceability")
    s.set_defaults(fn=cmd_gate)

    s = sub.add_parser("status", help="print the live task tree")
    s.add_argument("--json", action="store_true")
    s.add_argument("--watch", action="store_true")
    s.add_argument("--interval", type=float, default=5)
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("show", help="print one task's full report")
    s.add_argument("task_id")
    s.set_defaults(fn=cmd_show)

    sub.add_parser("report", help="write .hive/FINAL_REPORT.md").set_defaults(fn=cmd_report)

    s = sub.add_parser("rollback", help="reset the repository to the state before a task")
    s.add_argument("task_id")
    s.add_argument("--yes", action="store_true")
    s.set_defaults(fn=cmd_rollback)

    s = sub.add_parser("stop", help="stop running tasks (all, or one task and its descendants)")
    s.add_argument("task_id", nargs="?")
    s.add_argument("--grace", type=float, default=15)
    s.set_defaults(fn=cmd_stop)

    s = sub.add_parser("models", help="view or adjust model bidding, policies and per role overrides")
    ms = s.add_subparsers(dest="models_cmd")
    ms.add_parser("show")
    m = ms.add_parser("set", help="override model/effort for a role id, a level, or all")
    m.add_argument("target")
    m.add_argument("--model")
    m.add_argument("--effort", choices=DEFAULT_CONFIG["model_selection"]["efforts"])
    m.add_argument("--global", dest="global_scope", action="store_true")
    m = ms.add_parser("unset")
    m.add_argument("target")
    m.add_argument("--global", dest="global_scope", action="store_true")
    m = ms.add_parser("mode")
    m.add_argument("mode", choices=["bid", "parent", "fixed"])
    m.add_argument("--global", dest="global_scope", action="store_true")
    m = ms.add_parser("policy")
    m.add_argument("level", choices=["manager", "supervisor", "worker"])
    m.add_argument("--allowed", nargs="+")
    m.add_argument("--max-effort", choices=DEFAULT_CONFIG["model_selection"]["efforts"])
    m.add_argument("--default")
    m.add_argument("--default-effort", choices=DEFAULT_CONFIG["model_selection"]["efforts"])
    m.add_argument("--global", dest="global_scope", action="store_true")
    s.set_defaults(fn=cmd_models, global_scope=False)

    s = sub.add_parser("config", help="show or change configuration (project, or --global)")
    cs = s.add_subparsers(dest="config_cmd")
    cs.add_parser("show")
    c = cs.add_parser("set")
    c.add_argument("key")
    c.add_argument("value")
    c.add_argument("--global", dest="global_scope", action="store_true")
    c = cs.add_parser("unset")
    c.add_argument("key")
    c.add_argument("--global", dest="global_scope", action="store_true")
    s.set_defaults(fn=cmd_config, global_scope=False)

    s = sub.add_parser("secrets", help="list required environment keys and which are missing")
    s.add_argument("--template", action="store_true", help="append missing keys to the secrets file")
    s.set_defaults(fn=cmd_secrets)

    sub.add_parser("estimate", help="rough cost estimate for the blueprint").set_defaults(fn=cmd_estimate)
    sub.add_parser("trace", help="check acceptance criteria traceability").set_defaults(fn=cmd_trace)
    sub.add_parser("retro", help="write a retrospective of failures and model outcomes").set_defaults(fn=cmd_retro)

    s = sub.add_parser("serve", help="live web dashboard")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--host", default="127.0.0.1")
    s.set_defaults(fn=cmd_serve)

    args = ap.parse_args()
    if not hasattr(args, "global_scope"):
        args.global_scope = False
    args.fn(args)


if __name__ == "__main__":
    main()
