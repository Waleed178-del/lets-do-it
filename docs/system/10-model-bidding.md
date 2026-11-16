# 10. Model Bidding: every agent picks its own model and effort

Before a task runs, the agent assigned to it bids for the model and effort it needs. Your policy bounds the bid, your overrides beat it, and a failed attempt automatically moves up a tier.

## Decision flow for one task

```mermaid
flowchart TD
    T["Task dispatched to role R"] --> O1{"Override for R?"}
    O1 -->|yes| OV(["Use override exactly<br/>locked, never escalated"])
    O1 -->|no| O2{"Override for R's level?"}
    O2 -->|yes| OV
    O2 -->|no| O3{"Override for all?"}
    O3 -->|yes| OV
    O3 -->|no| M{"Mode"}
    M -->|fixed| FX["Level default<br/>for example worker sonnet/medium"]
    M -->|parent| PA["Commander's model and effort<br/>from the brief"]
    M -->|bid| BD["Bidder call<br/>haiku, low effort, no tools<br/>about $0.002"]
    BD --> BV{"Valid bid?"}
    BV -->|no| FB["Fall back to brief hint<br/>or level default"]
    BV -->|yes| BB["model, effort,<br/>complexity 1 to 5, rationale"]
    FX & PA & FB & BB --> CL["Clamp to level policy<br/>allowed models, max effort"]
    CL --> RUN(["Run attempt 1"])
    RUN --> R{"Attempt failed and retries left?"}
    R -->|no| END(["Done"])
    R -->|yes| ESC["Escalate one model tier<br/>and one effort level,<br/>still within policy"]
    ESC --> RUN2(["Run next attempt"])
    RUN2 --> R
```

## The bid

```mermaid
sequenceDiagram
    autonumber
    participant P as Parent commander
    participant E as Engine
    participant B as Bidder (haiku)
    participant A as Assigned agent
    P->>E: dispatch brief with model hint<br/>"sonnet, medium, two standard pages"
    E->>E: no override, mode = bid
    E->>B: role card, brief, allowed models,<br/>max effort, parent hint
    B-->>E: {"model":"haiku","effort":"low",<br/>"complexity":2,"rationale":"..."}
    E->>E: clamp to policy, log bid in ledger
    E->>A: claude -p --model haiku --effort low
    A-->>E: report
    alt verification fails
        E->>A: retry with --model sonnet --effort medium
    end
    E-->>P: verified report with model_selection per attempt
```

## How a bid is judged

```mermaid
flowchart LR
    subgraph Inputs
        I1["role mission and level"]
        I2["brief: objective, deliverables,<br/>acceptance, verify, scope"]
        I3["parent's suggestion"]
        I4["allowed models and max effort"]
    end
    subgraph Guide["Bidder guide"]
        G1["haiku: mechanical, fully specified,<br/>one or two files, config, simple docs"]
        G2["sonnet: standard engineering,<br/>features, components, endpoints, tests"]
        G3["opus: architecture, cross cutting integration,<br/>subtle debugging, security, many children"]
        G4["cheapest that passes first time:<br/>a failed attempt costs more than it saves"]
    end
    Inputs --> Guide --> Out(["model + effort + complexity + rationale"])
```

## Default policy

| Level | Allowed models | Max effort | Default (fixed mode, fallback) | Engine retries |
|---|---|---|---|---|
| Manager | sonnet, opus | xhigh | opus / high | 0 |
| Supervisor | haiku, sonnet, opus | high | sonnet / medium | 1 |
| Worker | haiku, sonnet, opus | high | sonnet / medium | 1 |

Escalation ladder: `haiku → sonnet → opus` and `low → medium → high → xhigh → max`, each capped by the level's policy.

## Your controls

```mermaid
flowchart TD
    You(["You, inside Claude Code"]) --> Skill["/hive:models make workers cheaper"]
    You --> CLI["hive models ..."]
    Skill --> CLI
    CLI --> S1["set ROLE or LEVEL or all<br/>--model --effort"]
    CLI --> S2["unset ROLE or LEVEL or all"]
    CLI --> S3["mode bid | parent | fixed"]
    CLI --> S4["policy LEVEL --allowed --max-effort<br/>--default --default-effort"]
    S1 & S2 & S3 & S4 --> W{"--global?"}
    W -->|yes| G["~/.hive/config.json<br/>every project"]
    W -->|no| P[".hive/config.json<br/>this project, wins over global"]
    G & P --> Live(["Read fresh on every dispatch:<br/>changes apply mid build"])
```

## Configuration layers

```mermaid
flowchart LR
    D["Built in defaults"] --> G["~/.hive/config.json<br/>global"] --> P[".hive/config.json<br/>project"] --> Eff(["Effective config"])
```

## Seeing what was chosen

```mermaid
flowchart LR
    L["ledger.jsonl<br/>bid and attempt events"] --> ST["hive status<br/>model/effort on every task"]
    L --> MD["hive models<br/>last pick per role"]
    L --> RT["hive retro<br/>pass rate and spend per model/effort"]
    L --> FR["FINAL_REPORT.md<br/>model usage table"]
    RT --> Tune(["Tune policy with evidence,<br/>for example raise the worker floor<br/>if haiku pass rate is low"])
```
