# 04. Dispatch Lifecycle: how one level calls the next

Every delegation, at every level, goes through the same command: `python3 hive.py dispatch --brief <file>`. The engine sits between parent and child.

## Four level call chain

```mermaid
sequenceDiagram
    autonumber
    actor U as You
    participant O as Orchestrator<br/>(your session)
    participant E as Engine hive.py
    participant M as Manager<br/>(claude -p)
    participant S as Supervisor<br/>(claude -p)
    participant W as Worker<br/>(claude -p)
    participant G as Git repo

    U->>O: /hive:build plan.md
    O->>E: dispatch mgr brief
    E->>E: check chain of command, budget
    E->>G: checkpoint commit, record start SHA
    E->>M: spawn with role prompt and brief
    M->>E: dispatch sup brief (via Bash)
    E->>S: spawn
    S->>E: dispatch wrk brief (via Bash)
    E->>W: spawn
    W->>G: edit files, run tests
    W-->>E: JSON report
    E->>E: re-run verify, scope check
    E->>G: commit "hive(wrk-...)"
    E-->>S: verified worker report
    S->>S: inspect files, integration checks
    S-->>E: JSON report with children
    E->>E: verify, scope, child cross check
    E-->>M: verified supervisor report
    M-->>E: JSON report with children
    E->>E: verify, scope, child cross check
    E-->>O: verified manager report
    O->>E: hive gate
    O-->>U: phase result and spend
```

## Inside a single dispatch

```mermaid
flowchart TD
    A["dispatch --brief F"] --> B{"Is the role a direct<br/>report of the caller?"}
    B -->|no| X1(["exit 3: chain of command violation"])
    B -->|yes| C{"Depth guard ok?<br/>level = caller level + 1"}
    C -->|no| X2(["exit 3: depth guard"])
    C -->|yes| D{"Global spend<br/>under ceiling?"}
    D -->|no| X3(["BLOCKED: budget"])
    D -->|yes| E["Assign task ID T####-role<br/>save brief to .hive/tasks"]
    E --> R{"Identical brief passed before<br/>and verify still green?"}
    R -->|yes| RU(["PASS reused, cost 0"])
    R -->|no| F["Resolve scope<br/>brief, ownership.json, roster"]
    F --> G["Checkpoint commit if dirty<br/>record start SHA"]
    G --> H["Compose system prompt<br/>common + commander or worker + role card"]
    H --> BID["Select model and effort<br/>override, or bid, clamped by policy<br/>see 10"]
    BID --> I["Run claude -p<br/>model, effort, budget cap, schema, timeout"]
    I --> J["Verification pipeline<br/>see 05"]
    J --> K{"Final status"}
    K -->|FAIL and retries left| L["Build feedback<br/>reasons, failing output,<br/>children already passed"] --> ESC["Escalate one model tier<br/>and one effort level"] --> I
    K -->|PASS| M["Commit hive(role): title [id]"]
    K -->|FAIL or BLOCKED| N["Stash work as hive-failed:id"]
    I -.->|"SIGTERM from hive stop"| AB["Kill agent, stop descendants"] --> N
    M & N --> O["Write report, ledger task_end"]
    O --> P(["Print report JSON<br/>exit 0 PASS, 1 FAIL, 2 BLOCKED, 4 ABORTED"])
```

## Task states

```mermaid
stateDiagram-v2
    [*] --> Refused: wrong parent or depth
    [*] --> Blocked: budget ceiling reached
    [*] --> Reused: identical brief still verifies
    [*] --> Bidding: dispatch accepted
    Bidding --> Running: model and effort chosen
    Running --> Verifying: agent returns report
    Running --> Aborted: hive stop or signal
    Running --> Dead: process vanished without reporting
    Dead --> Aborted: hive stop cleans up
    Aborted --> Stashed: uncommitted work saved
    Verifying --> Pass: self PASS and all checks green
    Verifying --> Retrying: any check fails and retries left
    Retrying --> Running: rerun with feedback on a stronger model
    Verifying --> Fail: checks fail and no retries left
    Verifying --> Blocked: agent reports BLOCKED
    Pass --> Committed: auto commit
    Fail --> Stashed: work saved in git stash
    Reused --> [*]
    Committed --> [*]
    Stashed --> [*]
    Blocked --> [*]
    Refused --> [*]
```

## What a child process receives

```mermaid
flowchart LR
    subgraph SP["System prompt"]
        C1["common.md<br/>protocol, rules, report contract"]
        C2["commander.md or worker.md<br/>level procedure"]
        C3["role card<br/>mission, deliverables, done criteria,<br/>scope, direct reports"]
    end
    subgraph UP["User prompt"]
        B1["task brief JSON"]
        B2["failure feedback<br/>only on retry"]
        B3["completed work from<br/>an interrupted run"]
        B4["lessons for this role<br/>from earlier failures"]
    end
    subgraph ENV["Environment"]
        E1["HIVE_CALLER_ROLE"]
        E2["HIVE_TASK_ID"]
        E3["HIVE_DEPTH"]
        E4["HIVE_ENGINE, HIVE_STATE, HIVE_WORKDIR"]
        E6["your secrets from<br/>.hive/secrets.env"]
        E5["long Bash timeouts"]
    end
    subgraph FLAGS["CLI flags"]
        F1["--json-schema report"]
        F2["--model and --effort<br/>from the bid"]
        F3["--max-budget-usd"]
        F4["--disallowedTools Agent Task"]
    end
    SP & UP & ENV & FLAGS --> Agent(["claude -p"])
```

`HIVE_CALLER_ROLE` is how the engine knows who is calling when the child itself dispatches. Native subagents are disabled, so the only way down the chain is through the engine.
