#!/usr/bin/env python3
"""Regenerate 02-chain-of-command.md from the roster. Run from the repo root."""
import json, os
HERE = os.path.dirname(os.path.abspath(__file__))
R = json.load(open(os.path.join(HERE, "..", "..", "plugins", "hive", "skills", "build", "engine", "roster.json")))["roles"]
kids = lambda p: [r for r in R if r.get("parent") == p]
nid = lambda i: i.replace("-", "_")
L = ["# 02. Chain of Command (all %d roles)\n" % len(R),
     "Generated from `plugins/hive/skills/build/engine/roster.json`, the single source of truth. Regenerate after editing the roster with `python3 docs/system/generate_hierarchy.py`.\n",
     "## Top level\n\n```mermaid\nflowchart TD\n    O[\"Orchestrator<br/>your Claude Code session\"]"]
for m in kids("orchestrator"):
    cond = " (conditional)" if m.get("conditional") else ""
    L.append('    O --> %s["%s%s<br/>phase %s"]' % (nid(m["id"]), m["title"], cond, m["phase"]))
L.append("    classDef orch fill:#1f2937,color:#fff,stroke:#111\n    classDef mgr fill:#2563eb,color:#fff,stroke:#1e40af\n    class O orch")
L.append("    class " + ",".join(nid(m["id"]) for m in kids("orchestrator")) + " mgr\n```\n")
L.append("## Headcount\n\n| Level | Role | Count |\n|---|---|---|")
for lv, name in [(0, "Orchestrator"), (1, "Manager"), (2, "Supervisor"), (3, "Worker")]:
    L.append("| %d | %s | %d |" % (lv, name, sum(1 for r in R if r["level"] == lv)))
L.append("| | **Total** | **%d** |\n" % len(R))
for m in kids("orchestrator"):
    L.append("## %s (`%s`)\n\n%s\n\n```mermaid\nflowchart LR" % (m["title"], m["id"], m["mission"]))
    L.append('    %s["%s"]' % (nid(m["id"]), m["title"]))
    for s in kids(m["id"]):
        L.append('    %s --> %s["%s"]' % (nid(m["id"]), nid(s["id"]), s["title"]))
        for w in kids(s["id"]):
            L.append('    %s --> %s(["%s"])' % (nid(s["id"]), nid(w["id"]), w["title"]))
    L.append("    classDef mgr fill:#2563eb,color:#fff,stroke:#1e40af\n    classDef sup fill:#7c3aed,color:#fff,stroke:#5b21b6\n    classDef wrk fill:#ecfdf5,color:#064e3b,stroke:#059669")
    L.append("    class %s mgr" % nid(m["id"]))
    L.append("    class " + ",".join(nid(s["id"]) for s in kids(m["id"])) + " sup")
    L.append("    class " + ",".join(nid(w["id"]) for s in kids(m["id"]) for w in kids(s["id"])) + " wrk\n```\n")
    L.append("| Supervisor | Worker | Task |\n|---|---|---|")
    for s in kids(m["id"]):
        for w in kids(s["id"]):
            L.append("| `%s` | `%s` | %s |" % (s["id"], w["id"], w["mission"]))
    L.append("")
open(os.path.join(HERE, "02-chain-of-command.md"), "w").write("\n".join(L))
print("wrote 02-chain-of-command.md")
