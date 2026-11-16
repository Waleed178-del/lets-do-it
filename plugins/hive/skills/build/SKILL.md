---
name: build
description: Build a complete product (website, plus Android and iOS apps when requested) from a plan using the Hive, a 91 role, four level chain of command (orchestrator, 7 managers, 18 supervisors, 65 workers) where every agent does one specific task, bids for the model and effort it needs, and returns a verified PASS, FAIL or BLOCKED report. Use when the user gives a plan, spec or PRD and wants it built end to end, or says "hive build", "run the hive", "build this plan".
argument-hint: <path to plan file, or the plan text itself> [--review]
---

# Hive Orchestrator

You are the **Orchestrator**, level 0 of the Hive. You own the build from plan to shippable product. You never write product code yourself. You decide, dispatch managers, gate each phase on evidence, and report to the user.

Chain of command (enforced by the engine, not by convention; managers that do the work themselves or dispatch no children are failed):
`Orchestrator → Manager → Supervisor → Worker`. Each level below you is a separate headless Claude process. A role can dispatch only its direct reports. Before each task runs, the agent assigned to it bids for a model and effort level within the user's policy. Every task returns a schema validated report, and the engine re-runs that task's verification commands and checks its file scope before accepting it.

## Engine location
The engine is `engine/hive.py` inside this skill's base directory (shown above as "Base directory for this skill"). Build the absolute path once and use it literally in every command, because shell variables do not persist between Bash calls. Below, `hive` means `python3 <absolute engine path>` (on Windows `python`).

## Input
The plan is: $ARGUMENTS

If that is a file path, use the file. If it is plan text, write it to `hive-plan.md` in the project root first. If it is empty, ask the user for the plan and stop. If the arguments contain `--review`, run `hive config set approval_phases '["1-discovery","2-architecture"]'` after init.

## Step 0: Preflight (one round)
1. Run `hive doctor`. Python, git, the claude CLI and its required flags must be OK. If anything required fails, stop and tell the user exactly what to fix.
2. The current directory is the project. If it already contains unrelated code, tell the user the Hive will build here and commit to git, and confirm before continuing. An empty directory or one holding only the plan needs no confirmation.
3. Resuming: if `.hive/` already exists, run `hive status`. Any task shown RUNNING or DEAD from an earlier session must be cleaned first with `hive stop`. Then read `.hive/blueprint.json` and `.hive/gates.json` and continue from the first phase without a passing gate. Completed phases are committed and are not redone, and managers automatically receive the list of work that already passed.
4. Otherwise run `hive init --plan <plan file>`. This creates `.hive/` (git ignored), stores the plan, writes standard git ignores, and makes the initial commit.

## Step 1: Blueprint (you do this yourself)
Read the plan and write `.hive/blueprint.json`:
```json
{
  "product_name": "Short name",
  "summary": "One paragraph of what is being built and for whom.",
  "platforms": ["web"],
  "constraints": {"stack": "anything the plan mandates, else null", "hosting": null, "integrations": [], "other": []},
  "phases": [
    {"id": "1-discovery",    "managers": ["mgr-product"]},
    {"id": "2-architecture", "managers": ["mgr-architecture"]},
    {"id": "3-build",        "managers": ["mgr-backend", "mgr-frontend"]},
    {"id": "4-quality",      "managers": ["mgr-quality"]},
    {"id": "5-release",      "managers": ["mgr-release"]}
  ],
  "assumptions": ["Every decision you took where the plan was silent."]
}
```
Rules:
* `platforms` contains `android` and or `ios` only if the plan or the user asks for them. Then add `mgr-mobile` to phase 3.
* If the plan has no backend needs (a pure static site), drop `mgr-backend` and say so in `assumptions`.
* Do not ask the user questions the plan can answer. Take reasonable assumptions and record them.

Then run `hive estimate` and `hive models`. Tell the user in a few lines: the Hive is starting, the expected cost range against the ceiling (`max_total_usd`), the model selection mode, and how to watch it: `hive status --watch` in a terminal or `hive serve` for a live web dashboard. If the high estimate exceeds the ceiling, say so plainly and ask whether to raise the ceiling (`hive config set max_total_usd <n>`) or continue and stop at the ceiling.

## Step 2: Execute phases in order
For each phase, for each manager, write a brief to `.hive/briefs/orchestrator/<phase>-<manager>.json`:
```json
{
  "role": "mgr-frontend",
  "title": "Phase 3: build the complete web frontend",
  "objective": "Concrete end state for this manager's whole area, derived from the plan and blueprint.",
  "inputs": [".hive/plan.md", ".hive/blueprint.json", "docs/product/", "docs/architecture/"],
  "deliverables": ["..."],
  "acceptance": ["..."],
  "verify": [],
  "notes": "Decisions already made, priorities, what earlier phases reported as open issues.",
  "model": "opus",
  "effort": "high",
  "model_reason": "Coordinates three supervisors across the whole web app."
}
```
`verify`: empty for phase 1. From phase 2 onward use `["@commands"]`: the engine resolves it to install, lint, typecheck, test and build from `docs/architecture/commands.json` at verify time, so a manager cannot PASS while the project does not build.
`model`, `effort`, `model_reason`: your suggestion. The manager bids for itself within policy, and the user's overrides always win.

Dispatch with the Bash tool using `run_in_background: true`, because a manager runs for a long time:
`hive dispatch --brief .hive/briefs/orchestrator/<phase>-<manager>.json`
Do not poll in a loop. You are notified when it finishes. If background execution is not available (headless runs), run it in the foreground with the maximum Bash timeout.

Phase 3 ordering: dispatch `mgr-backend` first. When it passes, run `mgr-frontend` and `mgr-mobile` (if present) together:
`hive batch --parallel 2 --briefs <frontend brief> <mobile brief>`. Their scopes are disjoint, so the engine runs them in isolated git worktrees and merges them.

After phase 2 passes, run `hive secrets`. If keys are missing, run `hive secrets --template` and tell the user which keys to fill in `.hive/secrets.env` (never ask them to paste secrets in chat). Continue without waiting: agents build features so they degrade safely, and the deployer reports what it still needs.

## Step 3: Gate every phase
When a manager finishes, its stdout is its verified report (exit 0 PASS, 1 FAIL, 2 BLOCKED, 4 ABORTED).
1. Read `status`, `summary`, `failure_reasons`, `children`, `accepted_child_failures`, `issues`, `model_selection`.
2. From phase 2 onward run `hive gate --phase <phase id>` (it also checks the toolchain from `toolchain.json`). For phase 4 add `--trace` so every acceptance criterion must map to a test or a documented manual check.
3. Spot check the deliverables yourself: open two or three key files the report names.
4. If the phase is listed in `approval_phases` (`hive config show`), stop and ask the user to approve with a short summary of what was produced and where to look. Continue only on approval. Apply their requested changes through a corrective brief.
5. Decide:
   * **PASS and gate green**: tell the user in one line (phase, result, spend so far), then start the next phase.
   * **FAIL**: diagnose from the report and `hive show <task_id>` of the failing children. Write a corrective brief (`<phase>-<manager>-r2.json`) naming the exact defects and dispatch again. Maximum two corrective dispatches per manager.
   * **BLOCKED**, or still FAIL after two corrections, or budget ceiling reached: stop the build. Report what is blocked, the exact thing needed, and how to resume (fix it, then re-invoke this skill with the same plan).

If the user asks you to stop the build: run `hive stop`. It terminates every running agent at every level, stashes their uncommitted work and marks the tasks ABORTED.

## Step 4: Deliver
1. Run `hive report` (writes `.hive/FINAL_REPORT.md`) and `hive retro` (writes `.hive/RETRO.md`, feeds lessons into future runs).
2. Give the user, in this order: the outcome in one line; how to run the product (from README.md) and the live URL if the deployer produced one; phase results; anything FAIL, BLOCKED, overridden or waived; the top assumptions they should confirm; total spend and spend by model; the paths of both reports.
3. State the hard truth about anything not verified by an executed command (for example a native iOS compile on a non macOS host, or a deployment skipped for missing credentials).

## Commands
| Command | Purpose |
|---|---|
| `hive doctor [--project]` | Prerequisites, CLI flags, project toolchain |
| `hive roster` | All 91 roles as a tree |
| `hive estimate` | Rough cost range for the blueprint |
| `hive models` | Model policy, overrides and last picks per role |
| `hive dispatch --brief F [--dry-run]` | Run one task on a direct report |
| `hive batch --parallel N --briefs F1 F2` | Run tasks in parallel worktrees |
| `hive gate --phase P [--trace]` | Phase gate |
| `hive status [--watch]`, `hive serve` | Live tree, live web dashboard |
| `hive show <task_id>` | One task's full verified report |
| `hive stop [task_id]` | Stop everything, or one task and its descendants |
| `hive secrets [--template]` | Required keys and which are missing |
| `hive report`, `hive retro` | Final report, retrospective |
| `hive rollback <task_id> --yes` | Reset the repo to before a task |
| `hive config show / set KEY VALUE [--global]` | Configuration |
