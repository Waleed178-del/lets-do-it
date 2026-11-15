# 03. Phase Workflow: from plan to shippable product

The Orchestrator runs five phases in a fixed order. No phase starts until the previous one has a PASS report and a green gate.

## End to end flow

```mermaid
flowchart TD
    Start(["/hive:build plan.md"]) --> Doctor["hive doctor<br/>python, git, claude CLI, roster"]
    Doctor -->|missing prerequisite| Stop1(["Stop and tell the user"])
    Doctor --> Init["hive init<br/>.hive state, plan copy, config, first commit"]
    Init --> BP["Orchestrator writes blueprint.json<br/>platforms, phases, assumptions"]
    BP --> P1

    subgraph P1["Phase 1 Discovery"]
        M1["mgr-product"]
    end
    P1 --> G1{"Report PASS?"}
    G1 -->|yes| P2

    subgraph P2["Phase 2 Architecture"]
        M2["mgr-architecture"]
    end
    P2 --> G2{"PASS and gate green?"}
    G2 -->|yes| P3

    subgraph P3["Phase 3 Build"]
        M3a["mgr-backend"] --> Par{{"parallel worktrees"}}
        Par --> M3b["mgr-frontend"]
        Par --> M3c["mgr-mobile<br/>only if android or ios"]
    end
    P3 --> G3{"PASS and gate green?"}
    G3 -->|yes| P4

    subgraph P4["Phase 4 Quality"]
        M4["mgr-quality"]
    end
    P4 --> G4{"PASS and gate green?"}
    G4 -->|yes| P5

    subgraph P5["Phase 5 Release"]
        M5["mgr-release"]
    end
    P5 --> G5{"PASS and gate green?"}
    G5 -->|yes| Report["hive report<br/>FINAL_REPORT.md"]
    Report --> Done(["Deliver to user"])

    G1 & G2 & G3 & G4 & G5 -->|no| Fix["Corrective brief<br/>max 2 per manager"]
    Fix -->|still failing or BLOCKED| Halt(["Halt with exact blocker<br/>resume later from this phase"])
```

## Phase timeline

```mermaid
gantt
    title Typical build sequence (relative, not real durations)
    dateFormat X
    axisFormat %s
    section Orchestrator
    Preflight and blueprint     :o1, 0, 1
    section Phase 1
    mgr-product                 :p1, 1, 4
    section Phase 2
    mgr-architecture            :p2, 4, 8
    Gate 2                      :milestone, 8, 0
    section Phase 3
    mgr-backend                 :p3a, 8, 14
    mgr-frontend                :p3b, 14, 21
    mgr-mobile                  :p3c, 14, 21
    Gate 3                      :milestone, 21, 0
    section Phase 4
    mgr-quality                 :p4, 21, 26
    section Phase 5
    mgr-release                 :p5, 26, 29
    Final report                :milestone, 29, 0
```

## What each phase hands to the next

```mermaid
flowchart LR
    Plan[".hive/plan.md"] --> D["Discovery"]
    D -->|"docs/product/*<br/>requirements, stories, AC,<br/>sitemap, flows, design, copy"| A["Architecture"]
    A -->|"docs/architecture/*<br/>stack, data model, OpenAPI,<br/>ownership.json, commands.json<br/>plus a building scaffold"| B["Build"]
    B -->|"working backend, web app,<br/>mobile apps"| Q["Quality"]
    Q -->|"tests mapped to AC,<br/>security and a11y reports,<br/>fixes"| R["Release"]
    R -->|"CI, Dockerfile, deploy guide,<br/>README"| Out(["Shippable repository"])
```

## Gate contents

A gate is `hive gate --phase <id>`. It runs these keys from `docs/architecture/commands.json` and records the result in `.hive/gates.json`.

```mermaid
flowchart LR
    G["hive gate"] --> I["install"] --> L["lint"] --> T["typecheck"] --> U["test"] --> Bd["build"]
    Bd --> R{"all exit 0?"}
    R -->|yes| Pass(["phase passes"])
    R -->|no| Fail(["phase blocked<br/>orchestrator writes corrective brief"])
```

Phase 1 has no gate because no code exists yet. Phase 2 is the first gated phase: the empty scaffold must already install, lint, test and build.
