# 08. State and Artifacts: where everything is stored

## Repository layout during a build

```mermaid
flowchart TD
    Root["your project/"] --> H[".hive/ (git ignored)"]
    Root --> DP["docs/product/<br/>phase 1 output"]
    Root --> DA["docs/architecture/<br/>phase 2 output"]
    Root --> DQ["docs/quality/<br/>phase 4 output"]
    Root --> DR["docs/release/<br/>phase 5 output"]
    Root --> Src["application code<br/>layout from ownership.json"]
    H --> H1["plan.md"]
    H --> H2["blueprint.json"]
    H --> H3["config.json"]
    H --> H4["ledger.jsonl"]
    H --> H5["tasks/ID.json briefs as dispatched"]
    H --> H6["briefs/ID/ briefs a commander wrote"]
    H --> H7["reports/ID.json verified reports"]
    H --> H8["logs/ID.jsonl full agent transcripts"]
    H --> H9["gates.json"]
    H --> H10["wt/ temporary worktrees"]
    H --> H11["FINAL_REPORT.md"]
    H --> H12["secrets.env<br/>your keys, passed as env, redacted in logs"]
    H --> H13["lessons.json<br/>also ~/.hive/lessons.json"]
    H --> H14["run/ID.json<br/>live process IDs for stop"]
    H --> H15["RETRO.md"]
    DA --> O1["ownership.json<br/>who may change which paths"]
    DA --> O2["commands.json<br/>install, lint, test, build"]
    DA --> O3["toolchain.json<br/>required tools and versions"]
    Root --> GH["~/.hive/config.json<br/>your global defaults"]
```

## Data model

```mermaid
erDiagram
    BLUEPRINT ||--|{ PHASE : defines
    PHASE ||--|{ TASK : "dispatches manager tasks"
    PHASE ||--o| GATE : "checked by"
    TASK ||--o{ TASK : "parent_task of"
    TASK ||--|| BRIEF : "runs"
    TASK ||--|{ ATTEMPT : "has"
    TASK ||--o| BID : "starts with"
    ATTEMPT }o--|| MODEL_CHOICE : "runs on"
    TASK ||--|| REPORT : "produces"
    TASK }o--|| ROLE : "performed by"
    ROLE ||--o{ ROLE : "parent of"
    ROLE }o--o| OWNERSHIP : "scoped by"
    REPORT ||--o{ CHECK : contains
    REPORT ||--o{ DELIVERABLE : contains
    TASK ||--o| COMMIT : "PASS creates"
    TASK ||--o| STASH : "FAIL creates"

    ROLE {
        string id
        int level
        string parent
        string mission
        list scope
    }
    TASK {
        string task_id
        string role
        string parent_task
        string start_sha
        string status
    }
    BRIEF {
        string title
        string objective
        list deliverables
        list acceptance
        list verify
        list scope
    }
    ATTEMPT {
        int number
        string self_status
        string final
        float cost_usd
        list reasons
    }
    REPORT {
        string status
        string summary
        list issues
        list children
        list files_changed
        list accepted_child_failures
    }
    GATE {
        string phase
        bool passed
    }
    BID {
        string model
        string effort
        int complexity
        string rationale
        float cost_usd
    }
    MODEL_CHOICE {
        string model
        string effort
        string source
    }
```

## Ledger events

Every process appends to one locked file. `hive status` and `hive report` are computed from it.

```mermaid
sequenceDiagram
    participant E as Engine
    participant L as ledger.jsonl
    E->>L: task_start (task_id, role, level, parent_task, start_sha)
    opt bid mode
        E->>L: bid (model, effort, complexity, cost_usd)
    end
    loop each attempt
        E->>L: attempt_start (attempt, model, effort, source)
        E->>L: attempt_end (self_status, final, cost_usd, reasons, model)
    end
    E->>L: task_end (status, cost_usd, duration_s, brief_hash, reused_from)
    opt rollback
        E->>L: rollback (task_id, to)
    end
```

## Brief and report contract

```mermaid
classDiagram
    class Brief {
        +role : string
        +title : string
        +objective : string
        +inputs : list
        +deliverables : list
        +acceptance : list
        +verify : list of shell commands
        +scope : list of globs, optional
        +notes : string
        +model : suggestion
        +effort : suggestion
        +model_reason : string
    }
    class AgentReport {
        +status : PASS | FAIL | BLOCKED
        +summary : string
        +deliverables : item, done, evidence
        +checks : command, exit_code, result
        +issues : list
        +children : task_id, role, status
    }
    class VerifiedReport {
        +task_id
        +status : engine verdict
        +attempts
        +failure_reasons
        +verification
        +files_changed
        +start_sha / end_sha / stash
        +accepted_child_failures
        +model_selection per attempt
        +prior_progress
        +cost_usd / duration_s
    }
    Brief --> AgentReport : agent executes
    AgentReport --> VerifiedReport : engine verifies
```
