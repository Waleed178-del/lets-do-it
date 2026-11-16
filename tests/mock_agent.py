#!/usr/bin/env python3
"""Stand-in for `claude -p` used by the engine tests (HIVE_MOCK_AGENT).

Reads the prompt on stdin, pulls the brief's optional "mock" block and acts it out:
  write:        {path: content}            files to write
  write_retry:  {path: content}            files to write only on a retry attempt
  dispatch:     [brief, ...]               child briefs to dispatch through the real engine
  status:       PASS | FAIL | BLOCKED      self reported status (default PASS)
  claim_child_status: STATUS               lie about every child's status (tests the cross check)
  sleep:        seconds                    sleep before answering (timeouts, stop)
  bid:          {model, effort, ...}       answer to a bid request (default sonnet/medium)
  fail_until_attempt: N                    report FAIL on attempts below N
Records each call in $HIVE_STATE/mock_calls.jsonl. Prints a stream-json style result event.
"""
import json
import os
import re
import subprocess
import sys
import time

prompt = sys.stdin.read()
brief = json.loads(re.search(r"```json\n(.*?)\n```", prompt, re.S).group(1))
mock = brief.get("mock", {})
state = os.environ.get("HIVE_STATE", ".hive")


def log_call(kind, extra=None):
    with open(os.path.join(state, "mock_calls.jsonl"), "a") as fh:
        fh.write(json.dumps(dict({"kind": kind, "role": brief.get("role"), "title": brief.get("title"),
                                  "task": os.environ.get("HIVE_TASK_ID")}, **(extra or {}))) + "\n")


if prompt.startswith("# BID REQUEST"):
    bid = dict({"model": "sonnet", "effort": "medium", "complexity": 3, "rationale": "mock bid"}, **mock.get("bid", {}))
    log_call("bid")
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.002, "structured_output": bid}))
    sys.exit(0)

m = re.search(r"You are running on model `([^`]+)` at effort `([^`]+)`", prompt)
attempt_retry = "PREVIOUS ATTEMPT FAILED" in prompt
log_call("run", {"model": m.group(1) if m else None, "effort": m.group(2) if m else None, "retry": attempt_retry,
                 "prior": "WORK ALREADY COMPLETED" in prompt, "already_passed_note": "already PASSED" in prompt})

if mock.get("sleep"):
    time.sleep(float(mock["sleep"]))

for path, content in mock.get("write", {}).items():
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    open(path, "w").write(content)
if attempt_retry:
    for path, content in mock.get("write_retry", {}).items():
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        open(path, "w").write(content)

if mock.get("git_commit"):
    subprocess.run(["git", "add", "-A"], stdout=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-qm", mock["git_commit"]], stdout=subprocess.DEVNULL)

children, child_fail = [], False
task_id = os.environ["HIVE_TASK_ID"]
skip_passed = attempt_retry and mock.get("skip_passed_on_retry", True)
passed_titles = set(re.findall(r"\* `T\d{4}-[\w-]+` \([\w-]+\) (.+)", prompt)) if skip_passed else set()
for i, child in enumerate(mock.get("dispatch", [])):
    if child["title"] in passed_titles:
        continue
    bdir = os.path.join(".hive", "briefs", task_id)
    os.makedirs(bdir, exist_ok=True)
    bpath = os.path.join(bdir, "%02d-%s-%d.json" % (i, child["role"], int(time.time() * 1000)))
    child = dict(child)
    child.setdefault("objective", "mock objective")
    json.dump(child, open(bpath, "w"))
    p = subprocess.run([sys.executable, os.environ["HIVE_ENGINE"], "dispatch", "--brief", bpath],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode == 3:
        children.append({"task_id": "none", "role": child["role"], "status": "REFUSED: " + p.stderr.strip()})
        child_fail = True
        continue
    rep = json.loads(p.stdout)
    children.append({"task_id": rep["task_id"], "role": rep["role"], "status": rep["status"]})
    child_fail = child_fail or rep["status"] != "PASS"

if "claim_child_status" in mock:
    for c in children:
        c["status"] = mock["claim_child_status"]
status = mock.get("status", "FAIL" if child_fail else "PASS")
if mock.get("fail_until_attempt") and not attempt_retry:
    status = "FAIL"
report = {
    "status": status,
    "summary": "mock %s did %s" % (os.environ.get("HIVE_CALLER_ROLE"), brief["title"]),
    "deliverables": [{"item": d, "done": status == "PASS", "evidence": "mock"} for d in brief.get("deliverables", [])],
    "checks": [], "issues": ["retry attempt"] if attempt_retry else [], "children": children,
}
secret = os.environ.get("MOCK_SECRET_ECHO")
if secret:
    print("agent saw secret %s" % os.environ.get(secret, ""))
print(json.dumps({"type": "system", "subtype": "init"}))
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "session_id": "mock-" + task_id,
                  "total_cost_usd": 0.05, "structured_output": report}))
