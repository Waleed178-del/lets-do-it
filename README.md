# Hive

A hierarchical multi agent build system for Claude Code. You give it a plan. 91 specialized agents in a four level chain of command build a working website, plus Android and iOS apps when you ask for them, phase by phase. Every agent does one specific task, bids for the model and effort level that task needs, and returns a verified PASS, FAIL or BLOCKED report.

```
Orchestrator (1)            your Claude Code session, runs /hive:build
└── Managers (7)            product, architecture, backend, frontend, mobile, quality, release
    └── Supervisors (18)    requirements, UX, system design, scaffold, data, API, auth, ...
        └── Workers (65)    plan parser, data modeler, endpoint builder, component builder, deployer, ...
```

Diagrams of the whole system (52 Mermaid diagrams: mind map, chain of command, phase workflow, dispatch lifecycle, verification, parallel execution, failure handling, data model, agent protocols, model bidding, operations) are in [`docs/system/`](docs/system/README.md).

## Install

Inside Claude Code:

```
/plugin marketplace add waleed178-del/lets-do-it#ccr-ddafa57e-0ytx73
/plugin install hive@lets-do-it
```

The `#branch` suffix installs straight from the development branch. Once it is merged into the default branch, drop the suffix.

Without the plugin system:

```bash
git clone -b ccr-ddafa57e-0ytx73 https://github.com/waleed178-del/lets-do-it.git
mkdir -p ~/.claude/skills
for s in build status models; do cp -r lets-do-it/plugins/hive/skills/$s ~/.claude/skills/hive-$s; done
```
Commands are then `/hive-build`, `/hive-status`, `/hive-models`.

Prerequisites: Claude Code CLI, git, Python 3.8 or newer. Nothing to `pip install`. Linux and macOS are fully supported, Windows is best effort.

## Use

| Command | What it does |
|---|---|
| `/hive:build plan.md` | Build from a plan file, or paste the plan text instead of a path |
| `/hive:build plan.md --review` | Pause for your approval after Discovery and Architecture |
| `/hive:status` | Live task tree with model, effort, attempts and spend |
| `/hive:status stop` | Stop every running agent at every level |
| `/hive:models` | See and change how models are chosen, in plain words: "use opus for the architects", "make workers cheaper" |

From a terminal, `hive.py` is at `plugins/hive/skills/build/engine/hive.py`. `hive.py status --watch` gives a refreshing terminal view and `hive.py serve` a live dashboard at http://127.0.0.1:8765.

## How it runs

| Phase | Manager | Output |
|---|---|---|
| 1 Discovery | mgr-product | requirements, user stories, acceptance criteria, sitemap, flows, design spec, copy |
| 2 Architecture | mgr-architecture | stack ADR, data model, OpenAPI contract, ownership, commands and toolchain maps, provisioned tools, a scaffold that builds |
| 3 Build | mgr-backend, then mgr-frontend and mgr-mobile in parallel | the product |
| 4 Quality | mgr-quality | tests traced to every acceptance criterion, security, accessibility, performance and visual review |
| 5 Release | mgr-release | CI, container, observability, README, and a live deployment when you provide hosting credentials |

After each phase the orchestrator runs a gate (toolchain, install, lint, typecheck, test, build, and in phase 4 acceptance traceability) and continues only when it is green.

## Model bidding

Before each task, the agent assigned to it bids for a model and effort level. A fast haiku call reads the role and the brief and picks the cheapest model likely to pass first time (about $0.002 per bid).

* **Policy** bounds every bid: allowed models and maximum effort per level.
* **Escalation**: a failed attempt is retried one model tier and one effort level higher.
* **Overrides** win over everything: pin a role, a level or all agents, for one project or globally.
* **Modes**: `bid` (default), `parent` (the commander's choice in the brief), `fixed` (level defaults).
* Changes apply to the next dispatch, including while a build is running.

```bash
hive.py models                                          # policy, overrides, last pick per role
hive.py models set mgr-architecture --model opus --effort xhigh
hive.py models set worker --model sonnet --global       # every project
hive.py models policy worker --allowed haiku sonnet --max-effort high
hive.py models mode parent
```

## What the engine enforces

These rules are code, not prompt suggestions:

* **Chain of command.** A role can dispatch only its direct reports. Workers dispatch nothing. Native subagents are disabled.
* **Mandatory delegation.** Managers and supervisors that dispatch no children fail. Managers may not edit project files. Supervisors are limited to small glue fixes.
* **Structured reports** matching `schemas/report.schema.json`.
* **Independent verification.** The engine re-runs each brief's `verify` commands (`@commands` resolves to the project's own install, lint, test and build).
* **Scope.** Files outside a role's ownership globs fail the task. Shared files such as `web/package.json` and generated output are handled.
* **Honesty checks.** PASS with an undone deliverable fails. A commander misstating a child's result fails.
* **Retry with feedback** on a stronger model, without redoing children that already passed.
* **Resume.** Rerunning `/hive:build` continues from the first unfinished phase, and commanders are told which work already passed. Identical briefs that still verify are reused at no cost.
* **Version control.** One commit per verified task. Failed or aborted work is stashed. `hive.py rollback <task> --yes` resets to before any task.
* **Spend limits.** Per dispatch caps, a global ceiling, and `hive.py estimate` before you start.
* **Liveness.** Crashed tasks show as DEAD. `hive.py stop` cascades through every level.
* **Learning.** First attempt failures become lessons injected into the same role's future prompts. `hive.py retro` reports pass rates per model.

## Secrets, environment, notifications

* `hive.py secrets` lists keys required by `.env.example` files and which are missing. `hive.py secrets --template` adds them to `.hive/secrets.env` (git ignored). Values reach agents as environment variables and are redacted from logs.
* The Environment Provisioner installs the tools listed in `docs/architecture/toolchain.json`. `hive.py doctor --project` checks them.
* `hive.py config set notify_command 'curl -d "$HIVE_TITLE: $HIVE_MESSAGE" ntfy.sh/your-topic'` pings you on manager completion, BLOCKED, ABORTED, gates and budget stops.

## Configuration

`hive.py config show` prints the effective configuration. Layers: built in defaults, then `~/.hive/config.json` (`--global`), then the project's `.hive/config.json`.

| Key | Default | Meaning |
|---|---|---|
| `model_selection.mode` | bid | bid, parent or fixed |
| `model_selection.allowed` | manager sonnet,opus; others haiku,sonnet,opus | models each level may use |
| `model_selection.max_effort` | manager xhigh; others high | effort ceiling per level |
| `budget_usd_per_dispatch` | 30 / 12 / 5 | cap for one agent process |
| `max_total_usd` | 400 | ceiling for the whole build |
| `timeout_min` | 360 / 180 / 60 | wall clock limit per agent |
| `retries` | 0 / 1 / 1 | automatic retries after a FAIL |
| `approval_phases` | none | phases that pause for your approval |
| `require_delegation` | true | enforce the chain of command on commanders |

## Safety

Headless agents run shell commands without asking (Bash is pre-approved, prompts are auto denied rather than hanging). Run the Hive in a dedicated folder, ideally a container or VM, never in a directory holding credentials or unrelated work.

## Development

```bash
python3 -m unittest -v tests/test_engine.py tests/test_features.py   # 43 tests, mock agent, no API cost
python3 plugins/hive/skills/build/engine/hive.py validate
python3 docs/system/generate_hierarchy.py                            # after editing roster.json
```

CI runs the tests on Linux and macOS (Python 3.8 and 3.12) and checks that every Claude CLI flag the engine depends on still exists.
