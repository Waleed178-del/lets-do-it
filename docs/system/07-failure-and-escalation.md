# 07. Failure Handling and Escalation

Failures are handled at the lowest level that can fix them, then escalate one level at a time.

## Escalation ladder

```mermaid
flowchart TD
    W["Worker attempt fails"] --> R1{"Engine retry left?<br/>default 1 for workers"}
    R1 -->|yes| W2["Engine reruns worker one model tier<br/>and one effort level higher,<br/>with exact failure reasons and output"]
    W2 --> W
    R1 -->|no| S["Supervisor receives FAIL report"]
    S --> S1["Diagnose root cause<br/>from report and files"]
    S1 --> S2{"Corrective briefs used < 2?"}
    S2 -->|yes| S3["New brief r2 or r3<br/>names defect, file, expected behavior"]
    S3 --> W
    S2 -->|no| S4{"Can own task still PASS?"}
    S4 -->|yes| SP(["PASS with accepted failure<br/>reported truthfully"])
    S4 -->|no| M["Manager receives FAIL"]
    M --> M1["Same loop: diagnose,<br/>up to 2 corrective supervisor briefs"]
    M1 -->|still failing| O["Orchestrator receives FAIL"]
    O --> O1{"Corrective manager<br/>briefs used < 2?"}
    O1 -->|yes| O2["Corrective manager brief"] --> M
    O1 -->|no| H(["Halt build<br/>report exact blocker to you"])
```

## Who fixes what

| Failure type | Fixed by | How |
|---|---|---|
| Typo, failing test the worker caused | Worker on automatic retry | Feedback includes the failing output |
| Wrong or unclear brief | Supervisor | Corrective brief with specific instructions |
| Integration break between workers | Supervisor glue fix or new worker task | Supervisor runs integration checks |
| Area wide design problem | Manager | Re-plans supervisor briefs |
| Missing credential, contradictory plan | You | Agent reports BLOCKED with the exact need |
| Spend ceiling reached | You | Raise `max_total_usd` and resume |

## Blocked vs failed

```mermaid
flowchart LR
    Q{"Why can the agent not finish?"}
    Q -->|"its own work is wrong"| F(["FAIL<br/>retried and corrected"])
    Q -->|"something outside its control is missing<br/>key, contradiction, broken dependency"| B(["BLOCKED<br/>no retry, escalates immediately"])
    Q -->|"ambiguity it can reasonably resolve"| A(["Decide, record assumption,<br/>continue"])
```

## Safety nets

```mermaid
flowchart TD
    subgraph Limits
        L1["Per dispatch budget<br/>--max-budget-usd"]
        L2["Global ceiling<br/>max_total_usd"]
        L3["Wall clock timeout per level<br/>process group killed"]
        L4["Max 2 corrective briefs<br/>per task per level"]
        L5["Escalation capped by<br/>level policy"]
    end
    subgraph Recovery
        R1["Failed work kept in<br/>git stash hive-failed:ID"]
        R2["start SHA recorded per task<br/>hive rollback ID --yes"]
        R3["Resume: rerun /hive:build<br/>continues from first unpassed phase,<br/>managers told what already passed"]
        R4["hive stop<br/>cascading abort of every level"]
        R5["Lessons: first attempt failures<br/>injected into future prompts"]
    end
    Limits --> NoRunaway(["No runaway cost or infinite loops"])
    Recovery --> NoLoss(["No lost work, clean tree"])
```

## Resume after a halt

```mermaid
sequenceDiagram
    actor U as You
    participant O as Orchestrator
    participant E as Engine
    U->>U: fix the blocker (add key, edit plan, raise budget)
    U->>O: /hive:build plan.md
    O->>E: hive status
    E-->>O: task tree, RUNNING or DEAD leftovers
    O->>E: hive stop (clean leftovers)
    O->>O: find first phase without a passing gate
    O->>E: dispatch that phase's manager
    E->>E: find PASSED descendants of the interrupted run still in git
    E-->>O: manager prompt includes "work already completed"
    Note over O,E: completed phases and completed tasks are not redone
```
