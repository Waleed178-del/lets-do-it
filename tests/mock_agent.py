#!/usr/bin/env python3
"""Stand-in for `claude -p` used by the engine tests (HIVE_MOCK_AGENT).

Reads the prompt on stdin, pulls the brief's optional "mock" block and acts it out:
  write:        {path: content}            files to write
  write_retry:  {path: content}            files to write only on a retry attempt
  dispatch:     [brief, ...]               child briefs to dispatch through the real engine
  status:       PASS | FAIL | BLOCKED      self reported status (default PASS)
Then prints a stream-json style result event like the real CLI.
"""
import json
import os
import re
import subprocess
import sys

prompt = sys.stdin.read()
brief = json.loads(re.search(r"```json\n(.*?)\n```", prompt, re.S).group(1))
mock = brief.get("mock", {})
retry = "PREVIOUS ATTEMPT FAILED" in prompt

for path, content in mock.get("write", {}).items():
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    open(path, "w").write(content)
if retry:
    for path, content in mock.get("write_retry", {}).items():
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        open(path, "w").write(content)

children, child_fail = [], False
task_id = os.environ["HIVE_TASK_ID"]
for i, child in enumerate(mock.get("dispatch", [])):
    bdir = os.path.join(".hive", "briefs", task_id)
    os.makedirs(bdir, exist_ok=True)
    bpath = os.path.join(bdir, "%02d-%s.json" % (i, child["role"]))
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

status = mock.get("status", "FAIL" if child_fail else "PASS")
report = {
    "status": status,
    "summary": "mock %s did %s" % (os.environ.get("HIVE_CALLER_ROLE"), brief["title"]),
    "deliverables": [{"item": d, "done": status == "PASS", "evidence": "mock"} for d in brief.get("deliverables", [])],
    "checks": [], "issues": ["retry attempt"] if retry else [], "children": children,
}
print(json.dumps({"type": "system", "subtype": "init"}))
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "session_id": "mock-" + task_id,
                  "total_cost_usd": 0.05, "structured_output": report}))
