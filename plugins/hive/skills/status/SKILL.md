---
name: status
description: Show the live Hive chain of command tree for the current project, with every task's PASS, FAIL, BLOCKED, ABORTED, DEAD or RUNNING state, the model and effort it ran on, attempts and spend. Also stops a running build. Use when the user asks how the Hive build is going, what failed, how much it cost, or to stop it.
argument-hint: "[task_id to inspect | stop | dashboard]"
---

# Hive Status

The engine lives in the sibling build skill: `<this skill's base directory>/../build/engine/hive.py` (plugin install) or `<this skill's base directory>/../hive-build/engine/hive.py` (plain skills install). Check which exists and use its absolute path. Below, `hive` means `python3 <that path>`.

1. If there is no `.hive/` directory in the project, say no Hive build has run here and stop.
2. If `$ARGUMENTS` is `stop`: run `hive stop`, then `hive status`, and confirm every task is no longer RUNNING. Explain that uncommitted work of stopped tasks is in git stashes named `hive-failed:<task id>` and that re-running `/hive:build` resumes from the first unfinished phase.
3. If `$ARGUMENTS` is `dashboard`: tell the user to run `hive serve` in a terminal and open http://127.0.0.1:8765 (it refreshes every 5 seconds), or `hive status --watch` for a terminal view.
4. If `$ARGUMENTS` names a task ID, run `hive show <task_id>` and explain its status, failure reasons, model selection per attempt, verification results and changed files.
5. Otherwise run `hive status` and present the tree. Then summarize: tasks by status, total spend against the ceiling, and for each FAIL, BLOCKED or DEAD task its first failure reason. DEAD means the process ended without reporting: recommend `hive stop` to clean it up and a re-run to resume.
6. Name the single most important next action.
