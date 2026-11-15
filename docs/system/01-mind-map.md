# 01. Mind Map: the whole system at a glance

```mermaid
mindmap
  root((Hive))
    Input
      Your plan file or text
      /hive:build command
      Blueprint written by the Orchestrator
        Product name
        Platforms web android ios
        Phases and managers
        Assumptions
    Chain of command
      Level 0 Orchestrator
        Your Claude Code session
      Level 1 Managers 7
        Product
        Architecture
        Backend
        Frontend
        Mobile conditional
        Quality
        Release
      Level 2 Supervisors 18
      Level 3 Workers 62
    Phases
      1 Discovery
        Requirements
        UX definition
      2 Architecture
        Stack ADR
        Data model
        API contract
        ownership.json
        commands.json
        Scaffold that builds
      3 Build
        Backend first
        Frontend and Mobile in parallel
      4 Quality
        Tests
        Security
        Accessibility and performance
      5 Release
        CI and container
        Deploy config
        README
    Engine hive.py
      Dispatch
        One headless claude process per task
        Role prompt plus brief
      Enforcement
        Direct reports only
        Report schema
        Re-run verify commands
        Scope check against ownership
        Child status cross check
      Recovery
        Automatic retry with feedback
        Corrective briefs by the parent
        Stash failed work
        Rollback to any task
      Limits
        Budget per dispatch
        Global spend ceiling
        Timeouts per level
    Outputs
      Working repository
      One git commit per verified task
      .hive ledger and reports
      FINAL_REPORT.md
```

## Reading the map

| Branch | Question it answers | Detail file |
|---|---|---|
| Input | What do I give the system? | [03 Phase workflow](03-phase-workflow.md) |
| Chain of command | Who does what? | [02 Chain of command](02-chain-of-command.md) |
| Phases | In what order is the product built? | [03 Phase workflow](03-phase-workflow.md) |
| Engine | How is every task run and checked? | [04 Dispatch lifecycle](04-dispatch-lifecycle.md), [05 Verification](05-verification-pipeline.md) |
| Recovery and limits | What happens when something fails? | [07 Failure and escalation](07-failure-and-escalation.md) |
| Outputs | What do I get at the end? | [08 State and artifacts](08-state-and-artifacts.md) |
