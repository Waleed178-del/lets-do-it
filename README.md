# Hive

A hierarchical multi agent build system for Claude Code. You give it a plan. 88 specialized agents in a four level chain of command build a working website, plus Android and iOS apps when you ask for them, phase by phase. Every agent does one specific task and returns a verified PASS, FAIL or BLOCKED report.

```
Orchestrator (1)            your Claude Code session, runs /hive:build
└── Managers (7)            product, architecture, backend, frontend, mobile, quality, release
    └── Supervisors (18)    requirements, UX, system design, scaffold, data, API, auth, ...
        └── Workers (62)    plan parser, data modeler, endpoint builder, component builder, ...
```

Run `python3 plugins/hive/skills/build/engine/hive.py roster` to print the full tree.

Diagrams of the whole system (mind map, chain of command, phase workflow, dispatch lifecycle, verification, parallel execution, failure handling, data model, agent protocols) are in [`docs/system/`](docs/system/README.md).

## Install

Option A, as a plugin (recommended). Inside Claude Code:

```
/plugin marketplace add waleed178-del/lets-do-it
/plugin install hive@lets-do-it
```

Option B, as plain skills without the plugin system:

```bash
git clone https://github.com/waleed178-del/lets-do-it.git
mkdir -p ~/.claude/skills
cp -r lets-do-it/plugins/hive/skills/build  ~/.claude/skills/hive-build
cp -r lets-do-it/plugins/hive/skills/status ~/.claude/skills/hive-status
```
With option B the commands are `/hive-build` and `/hive-status`. Keep the two folders side by side: the status skill finds the engine in its sibling.

Prerequisites: Claude Code CLI on PATH, git, Python 3.8 or newer. Nothing to `pip install`.

## Use

Open Claude Code in an empty folder and run:

```
/hive:build path/to/plan.md
```
or paste the plan inline: `/hive:build Build a booking site for a dental clinic with an Android app ...`

Check progress at any time with `/hive:status`, or from a second terminal:

```bash
python3 <engine path>/hive.py status
```

## How it runs

| Phase | Manager | Output |
|---|---|---|
| 1 Discovery | mgr-product | requirements, user stories, acceptance criteria, sitemap, flows, design spec, copy in `docs/product/` |
| 2 Architecture | mgr-architecture | stack ADR, data model, OpenAPI contract, `ownership.json`, `commands.json`, a scaffold that builds |
| 3 Build | mgr-backend, then mgr-frontend and mgr-mobile in parallel | the product |
| 4 Quality | mgr-quality | tests mapped to acceptance criteria, security, accessibility and performance fixes |
| 5 Release | mgr-release | CI, container, deploy config, observability, README |

After each phase the orchestrator runs a gate (install, lint, typecheck, test, build from `commands.json`) and moves on only when it is green.

## What the engine enforces

`hive.py` sits between every parent and child. These rules are code, not prompt suggestions:

* **Chain of command.** A role can dispatch only its direct reports. Workers can dispatch nothing. Native subagent spawning is disabled inside the Hive so the chain cannot be bypassed.
* **Structured reports.** Every agent must return JSON matching `schemas/report.schema.json` (status, deliverables with evidence, checks run, issues, children).
* **Independent verification.** The engine re-runs each brief's `verify` commands itself. A PASS whose verification fails is recorded as a FAIL.
* **Scope.** After each task the engine diffs the repository. Files outside the role's ownership globs fail the task.
* **Retry with feedback.** Failed tasks are retried with the exact failure reasons and command output.
* **Version control.** One commit per verified task (`hive(<role>): <title> [<task id>]`). Failed work goes to a named git stash so the tree stays clean. `hive.py rollback <task id> --yes` resets to before any task.
* **Spend limits.** A per dispatch cap (`--max-budget-usd`) and a global ceiling (`max_total_usd`) after which new dispatches return BLOCKED.

## Configuration

`.hive/config.json` is created in your project on the first run:

| Key | Default | Meaning |
|---|---|---|
| `models` | manager opus, supervisor sonnet, worker sonnet | model per level |
| `budget_usd_per_dispatch` | 30 / 12 / 5 | cap for one agent process |
| `max_total_usd` | 400 | ceiling for the whole build |
| `timeout_min` | 360 / 180 / 60 | wall clock limit per agent |
| `retries` | 0 / 1 / 1 | automatic retries after a FAIL |
| `permission_mode` | acceptEdits | permission mode for headless agents |
| `allowed_tools` | Bash, Read, Edit, Write, Glob, Grep, WebSearch, WebFetch, TodoWrite | tools agents may use without prompting |
| `on_fail` | stash | `stash` failed work or `keep` it in the tree |

## Safety

Headless agents run shell commands without asking (`Bash` is pre-approved, prompts are auto denied rather than hanging). Run the Hive in a dedicated folder, ideally a container or VM, never in a directory holding credentials or unrelated work.

## Development

```bash
python3 -m unittest -v tests/test_engine.py     # engine tests with a mock agent, no API cost
python3 plugins/hive/skills/build/engine/hive.py validate
```

To change the organization, edit `engine/roster.json` (the single source of truth for every role, its parent, mission, deliverables and scope) and run `validate`. Level behavior lives in `engine/prompts/`.
