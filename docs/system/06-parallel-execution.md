# 06. Parallel Execution: isolated git worktrees

Independent tasks with disjoint scopes run at the same time through `hive batch --parallel N`. Each one works in its own git worktree, so agents never write over each other.

## Batch flow

```mermaid
flowchart TD
    B["hive batch --parallel 2<br/>--briefs frontend.json mobile.json"] --> CP["Checkpoint commit if dirty<br/>base SHA"]
    CP --> W1["worktree .hive/wt/x-0<br/>branch hive/wt/x-0"]
    CP --> W2["worktree .hive/wt/x-1<br/>branch hive/wt/x-1"]
    W1 --> D1["dispatch mgr-frontend<br/>inside worktree"]
    W2 --> D2["dispatch mgr-mobile<br/>inside worktree"]
    D1 --> J{"wait for all"}
    D2 --> J
    J --> M1{"frontend PASS?"}
    M1 -->|yes| MG1["merge --no-ff into main tree"]
    M1 -->|no| K1["drop worktree<br/>keep branch for inspection"]
    MG1 --> C1{"conflict?"}
    C1 -->|yes| A1["merge --abort<br/>task becomes FAIL"]
    C1 -->|no| OK1(["merged, branch deleted"])
    J --> M2{"mobile PASS?"}
    M2 -->|yes| MG2["merge --no-ff"]
    M2 -->|no| K2["drop worktree<br/>keep branch"]
    MG2 --> OK2(["merged"])
```

## Git history shape

```mermaid
gitGraph
    commit id: "hive(init)"
    commit id: "phase 1 docs"
    commit id: "phase 2 scaffold"
    commit id: "backend tasks"
    branch hive-wt-frontend
    commit id: "wrk-component-builder"
    commit id: "wrk-page-builder"
    commit id: "wrk-api-client"
    checkout main
    branch hive-wt-mobile
    commit id: "wrk-android-scaffold"
    commit id: "wrk-android-screens"
    checkout main
    merge hive-wt-frontend id: "merge frontend"
    merge hive-wt-mobile id: "merge mobile"
    commit id: "phase 4 quality"
    commit id: "phase 5 release"
```

## Shared state across processes

All parallel processes share one `.hive/` directory through `HIVE_STATE`, while `HIVE_WORKDIR` points each one at its own worktree.

```mermaid
flowchart LR
    subgraph Shared[".hive/ shared by every process"]
        L["ledger.jsonl<br/>file locked appends"]
        CNT["counter.json<br/>file locked task IDs"]
        RP["reports/"]
        LG["logs/"]
    end
    P1["frontend process tree<br/>HIVE_WORKDIR = wt/x-0"] --> Shared
    P2["mobile process tree<br/>HIVE_WORKDIR = wt/x-1"] --> Shared
```

The safety condition is disjoint scopes. The architecture phase's `ownership.json` is designed to make the frontend and mobile areas disjoint. If two parallel tasks still touch the same file, the merge conflict is caught and that task is failed rather than silently overwritten.
