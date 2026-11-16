# 09. Agent Protocols: how each level thinks

Each level follows a fixed procedure defined in `plugins/hive/skills/build/engine/prompts/` and, for the Orchestrator, in `skills/build/SKILL.md`.

## Orchestrator (level 0)

```mermaid
flowchart TD
    A["Preflight<br/>doctor, init"] --> B["Write blueprint.json"]
    B --> C["Next phase"]
    C --> D["Write manager brief<br/>.hive/briefs/orchestrator/"]
    D --> E["dispatch in background"]
    E --> F["Read verified report"]
    F --> G["hive gate"]
    G --> H["Spot check 2 to 3 key files"]
    H --> I{"PASS and green?"}
    I -->|yes| J{"More phases?"}
    J -->|yes| C
    J -->|no| K["hive report, deliver"]
    I -->|no| L["Corrective brief or halt"]
    L --> E
```

## Commander: Manager (level 1) and Supervisor (level 2)

```mermaid
flowchart TD
    A["1 Analyze<br/>brief, plan, blueprint, docs"] --> B["2 Decompose<br/>one outcome per task,<br/>disjoint scopes in parallel"]
    B --> C["3 Write briefs<br/>.hive/briefs/ID/NN-role.json<br/>with model and effort suggestion"]
    C --> D["4 Dispatch<br/>dependency order,<br/>batch when independent"]
    D --> E["5 Verify<br/>open the files the child claims"]
    E --> F{"Child PASS<br/>and really correct?"}
    F -->|no| G["6 Correct<br/>new brief rN naming the defect<br/>max 2"]
    G --> D
    F -->|yes| H{"All tasks done?"}
    H -->|no| D
    H -->|yes| I["7 Integrate<br/>area build, lint, tests"]
    I --> J["8 Report<br/>children listed truthfully"]
```

| | Manager | Supervisor |
|---|---|---|
| Writes product code | Never | Small glue fixes in scope only |
| Dispatches | Its supervisors | Its workers |
| Model | bids per task, allowed sonnet or opus | bids per task, allowed haiku, sonnet or opus |
| Default retries by engine | 0 | 1 |
| On retry | never redispatches children that passed | never redispatches children that passed |

## Worker (level 3)

```mermaid
flowchart TD
    A["1 Read<br/>brief, inputs, existing code conventions"] --> B["2 Plan<br/>smallest complete change"]
    B --> C["3 Build<br/>production quality, no stubs"]
    C --> D["4 Test<br/>add tests for new behavior"]
    D --> E["5 Self verify<br/>run every verify command"]
    E --> F{"green?"}
    F -->|no, my fault| C
    F -->|no, outside my scope| G["report FAIL or BLOCKED<br/>with exact reason"]
    F -->|yes| H["6 Report<br/>one deliverable entry per brief item,<br/>one check entry per command run"]
```

## Rules every agent follows

```mermaid
mindmap
  root((Hive rules))
    Stay in scope
      Engine diffs every file
    Evidence or it did not happen
      Every check is a real command
      Engine re-runs verify
    No fakes
      No stubs or TODOs
      No weakened tests
    Decide do not stall
      Record assumptions
      BLOCKED only when impossible
    Secrets in env vars only
    Engine owns git
      No commit reset stash push
    Non interactive
      Flags like yes and CI=1
      Servers in background
    Brief overrides role card
```

## Report vocabulary

```mermaid
flowchart LR
    P(["PASS"]) --- PD["Every brief deliverable done,<br/>every verify command green,<br/>all files in scope"]
    F(["FAIL"]) --- FD["Own work is wrong or incomplete.<br/>Retried and corrected"]
    B(["BLOCKED"]) --- BD["External need: key, contradiction,<br/>budget. Escalates immediately"]
```
