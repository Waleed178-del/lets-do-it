# 05. Verification Pipeline: how a report becomes PASS

An agent's own PASS is only a claim. The engine turns it into a verdict by running these checks after every attempt.

```mermaid
flowchart TD
    R["Agent finishes"] --> A{"Valid JSON report<br/>matching the schema?"}
    A -->|no| F1["reason: no valid report"]
    A -->|yes| B{"Self status"}
    B -->|BLOCKED| BL(["BLOCKED<br/>skip verification"])
    B -->|PASS or FAIL| C["Run every brief verify command<br/>in the project, CI=1"]
    C --> C2{"all exit 0?"}
    C2 -->|no| F2["reason: verification failed<br/>command and exit code"]
    C2 -->|yes| D
    F2 --> D["Diff repo since start SHA<br/>committed, unstaged, untracked"]
    D --> D2{"every file inside scope,<br/>a shared file at any depth,<br/>or generated output?"}
    D2 -->|no| F3["reason: scope violation<br/>list of files"]
    D2 -->|yes| E
    F3 --> E{"PASS with any<br/>deliverable done=false?"}
    E -->|yes| F4["reason: undone deliverable"]
    E -->|no| G
    F4 --> G{"Commander: do reported child<br/>statuses match the ledger?"}
    G -->|no| F5["reason: misstated or invented child"]
    G -->|yes| H
    F5 --> H{"self PASS and<br/>zero reasons?"}
    H -->|yes| P(["PASS"])
    H -->|no| FL(["FAIL"])
    F1 --> FL
```

## The checks

| # | Check | Catches | Source of truth |
|---|---|---|---|
| 1 | Schema | Rambling or missing reports | `--json-schema` plus engine parsing |
| 2 | Verify commands | "Tests pass" claims that are false | Commands in the brief, run by the engine |
| 3 | Scope | Agents editing other teams' files | `ownership.json` or brief scope, shared and ignore lists |
| 4 | Deliverables | PASS with admitted gaps | The agent's own deliverables list |
| 5 | Child cross check | Commanders hiding child failures | `.hive/ledger.jsonl` |
| 6 | Delegation | Commanders doing the work themselves | ledger children, engine recorded commits, own edit budget |
| 7 | Brief lint (before dispatch) | Malformed briefs wasting worker attempts | brief structure and path hygiene |

## Before dispatch: brief lint

```mermaid
flowchart LR
    B["Commander writes brief"] --> L{"objective present, list fields clean,<br/>no whitespace or trailing punctuation in paths,<br/>known model and effort?"}
    L -->|no| R(["exit 3, exact reason on stderr<br/>commander fixes the brief, no agent ran"])
    L -->|yes| D(["dispatch"])
```

## Commander checks: the chain of command is enforced

```mermaid
flowchart TD
    C["Manager or supervisor reports PASS"] --> K{"Dispatched at least one child?<br/>unless brief sets allow_no_children,<br/>which is reported"}
    K -->|no| F1(["FAIL: delegation is mandatory"])
    K -->|yes| O["Attribute the commander's own edits:<br/>uncommitted changes, its checkpoint commits,<br/>any commit the engine did not make"]
    O --> M{"Level"}
    M -->|manager| M1{"Edited anything besides<br/>docs/summaries/?"}
    M1 -->|yes| F2(["FAIL: manager edited files directly"])
    M1 -->|no| P(["checks pass"])
    M -->|supervisor| S1{"Own edits above 8 files<br/>or 60 changed lines?"}
    S1 -->|yes| F3(["FAIL: that is worker work"])
    S1 -->|no| P
```

Commit attribution trusts only SHAs the engine recorded in the ledger. A commit message that merely looks like `hive(...)` proves nothing.

## Scope resolution

```mermaid
flowchart TD
    S["Which files may this task change?"] --> A{"Brief has scope?"}
    A -->|yes| A1(["use brief scope"])
    A -->|no| B{"Roster scope is @ownership?"}
    B -->|no| B1(["use roster scope<br/>for example docs/product/**"])
    B -->|yes| C{"ownership.json has<br/>an entry for the role?"}
    C -->|yes| C1(["use that entry"])
    C -->|no| D{"Any descendant<br/>has entries?"}
    D -->|yes| D1(["union of descendants' globs"])
    D -->|no| D2(["unrestricted<br/>only before phase 2 exists"])
    C1 --> U(["commanders: plus every descendant's globs"])
    A1 & B1 & C1 & D1 & D2 --> Plus["plus _shared list, matched at any depth<br/>package.json, web/package.json, lockfiles, ..."]
    Plus --> Ign["ignored entirely: generated output<br/>node_modules, dist, .next, coverage, build caches, plus _ignore"]
```

## Commander accountability

A manager or supervisor may still PASS when a child failed, for example after a corrective dispatch succeeded or after it audited the result itself. It cannot hide that decision.

```mermaid
flowchart LR
    C["Commander reports PASS"] --> Q{"Any child<br/>FAIL or BLOCKED?"}
    Q -->|no| OK(["clean PASS"])
    Q -->|yes, reported truthfully| AO(["PASS with<br/>accepted_child_failures<br/>listed in FINAL_REPORT"])
    Q -->|yes, reported as PASS| X(["FAIL: misstated child"])
```
