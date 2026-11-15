---
name: status
description: Show the live Hive chain of command tree for the current project, with every task's PASS, FAIL, BLOCKED or RUNNING state, attempts and spend. Use when the user asks how the Hive build is going, what failed, or how much it has cost.
argument-hint: "[task_id to inspect]"
---

# Hive Status

The engine lives in the sibling build skill: `<this skill's base directory>/../build/engine/hive.py` (plugin install) or `<this skill's base directory>/../hive-build/engine/hive.py` (plain skills install). Check which exists and use its absolute path.

1. If there is no `.hive/` directory in the project, say no Hive build has run here and stop.
2. If `$ARGUMENTS` names a task ID, run `python3 <engine> show $ARGUMENTS` and explain that task's status, failure reasons, verification results and changed files.
3. Otherwise run `python3 <engine> status` and present the tree. Then summarize: tasks by status, total spend against `max_total_usd` in `.hive/config.json`, and for each FAIL or BLOCKED task its first failure reason (from `python3 <engine> show <task_id>`).
4. Name the single most important next action.
