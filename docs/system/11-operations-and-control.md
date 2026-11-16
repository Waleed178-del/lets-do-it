# 11. Operations and Control: stop, resume, reuse, secrets, watch, learn

## Stopping a build

```mermaid
sequenceDiagram
    autonumber
    actor U as You
    participant S as hive stop
    participant L as ledger + run files
    participant W as Worker engine
    participant Su as Supervisor engine
    participant M as Manager engine
    U->>S: hive stop  (or /hive:status stop)
    S->>L: find RUNNING tasks and their process IDs
    S->>W: SIGTERM (deepest level first)
    W->>W: kill agent process group, stash work
    W->>L: task_end ABORTED
    S->>Su: SIGTERM
    Su->>Su: kill agent, stop remaining descendants
    Su->>L: task_end ABORTED
    S->>M: SIGTERM
    M->>L: task_end ABORTED
    S->>S: after grace period, SIGKILL anything still alive
    S-->>U: stopped, forced, cleaned lists
```

## Liveness

```mermaid
flowchart LR
    A["task_start records engine PID"] --> B{"status computed:<br/>RUNNING and PID alive?"}
    B -->|yes| R(["RUNNING"])
    B -->|no, never reported| D(["DEAD"])
    D --> S["hive stop marks it ABORTED"]
    D --> RS["re-run resumes it"]
```

## Resume and reuse

```mermaid
flowchart TD
    Start["Manager or supervisor dispatched"] --> Q1{"Identical brief already<br/>PASSED, commit still in history,<br/>verify still green?"}
    Q1 -->|yes| Reuse(["Return earlier verified report<br/>no agent run, cost 0"])
    Q1 -->|no| Q2{"Earlier run of the same role<br/>ended FAIL, ABORTED, DEAD or BLOCKED?"}
    Q2 -->|no| Fresh(["Normal run"])
    Q2 -->|yes| Collect["Collect its PASSED descendants<br/>whose commits are still in git"]
    Collect --> Inject(["Prompt section:<br/>WORK ALREADY COMPLETED AND VERIFIED<br/>dispatch only what remains"])
    Fresh --> Retry{"Attempt fails, retry left?"}
    Retry -->|yes| NoRedo(["Retry prompt lists children that passed:<br/>do NOT dispatch them again"])
```

## Secrets

```mermaid
flowchart LR
    EX[".env.example files<br/>written by agents"] --> CHK["hive secrets<br/>required vs present"]
    CHK --> TPL["hive secrets --template<br/>adds missing keys"]
    TPL --> SF[".hive/secrets.env<br/>you fill values, git ignored"]
    SF --> ENV["Engine passes as env vars to<br/>agents, verify commands, gates"]
    ENV --> RED["Values replaced by *** in logs<br/>and verification output"]
    SF --> DEP["Deployer uses hosting tokens<br/>to deploy and check the live URL"]
```

## Environment provisioning

```mermaid
flowchart TD
    A["Ownership mapper writes<br/>docs/architecture/toolchain.json"] --> B["Environment Provisioner<br/>installs missing tools,<br/>user level installers"]
    B --> C["hive doctor --project"]
    C --> D{"All required tools<br/>present at min version?"}
    D -->|yes| E(["Scaffold continues"])
    D -->|no, needs root or another OS| F(["BLOCKED with exact install command"])
    C -.-> G["Every phase gate re-checks toolchain"]
```

## Watching

```mermaid
flowchart LR
    LG["ledger.jsonl"] --> W1["hive status --watch<br/>terminal, refreshes"]
    LG --> W2["hive serve<br/>http://127.0.0.1:8765<br/>auto refresh, JSON at /api/status"]
    LG --> W3["notify_command<br/>runs on manager end, BLOCKED,<br/>ABORTED, gate, budget"]
    W3 --> N(["your hook: ntfy, Slack webhook,<br/>desktop notification, email"])
```

## Cost control before and during the build

```mermaid
flowchart TD
    BP["Blueprint written"] --> ES["hive estimate<br/>range per manager from model table,<br/>switches to observed averages<br/>after 3 finished tasks per level"]
    ES --> CMP{"High estimate above ceiling?"}
    CMP -->|yes| ASK(["Orchestrator tells you,<br/>you raise the ceiling or accept a stop"])
    CMP -->|no| GO(["Build starts"])
    GO --> D1["Per dispatch cap --max-budget-usd"]
    GO --> D2["Global ceiling: new dispatches BLOCKED"]
    GO --> D3["Bids pick the cheapest likely model"]
```

## Human review points

```mermaid
flowchart LR
    P1["Phase passes gate"] --> Q{"Phase listed in<br/>approval_phases?"}
    Q -->|no| N(["Next phase"])
    Q -->|yes| A["Orchestrator pauses,<br/>summarizes, asks you"]
    A -->|approve| N
    A -->|changes| C["Corrective brief"] --> P1
```

Enable with `/hive:build plan.md --review` (Discovery and Architecture) or `hive config set approval_phases '["1-discovery","2-architecture"]'`.

## Learning between runs

```mermaid
flowchart LR
    F["Task fails first attempt"] --> L["Reasons saved to<br/>.hive/lessons.json and ~/.hive/lessons.json"]
    L --> I["Next prompt for the same role<br/>gets LESSONS FROM EARLIER RUNS"]
    F --> R["hive retro<br/>failures by role,<br/>pass rate per model and effort"]
    R --> T(["You tune roster missions<br/>or model policy with evidence"])
```
