---
name: models
description: View and adjust which Claude model and effort level each Hive agent uses. Switch between per task bidding, commander choice and fixed defaults, set the allowed models and maximum effort per level, or pin a specific role, a whole level, or all agents to a model and effort, for this project or globally. Use when the user says things like "use opus for the architects", "make workers cheaper", "set all supervisors to sonnet high", "how are models chosen", "show model usage".
argument-hint: "[what to change, in plain words]"
---

# Hive Model Control

The engine lives in the sibling build skill: `<this skill's base directory>/../build/engine/hive.py` (plugin install) or `<this skill's base directory>/../hive-build/engine/hive.py` (plain skills install). Check which exists and use its absolute path. Below, `hive` means `python3 <that path>`.

## How models are chosen (explain this when asked)
For every task, in this priority order:
1. **User override** for that role ID, else for its level (`manager`, `supervisor`, `worker`), else for all (`*`). Honored exactly and never escalated.
2. **Mode**:
   * `bid` (default): the assigned agent bids. A fast haiku call reads the role and the brief and picks the cheapest model and lowest effort likely to succeed first time. The commander's suggestion in the brief is its strongest hint. About $0.002 per bid.
   * `parent`: the commander's `model` and `effort` in the brief are used directly.
   * `fixed`: the level default.
3. **Policy clamp**: the result is held inside the level's allowed models and maximum effort.
4. **Escalation**: if an attempt fails, the retry runs one model tier higher and one effort level higher, within policy.

Changes apply to the next dispatch, including while a build is running.

## Procedure
1. Run `hive models` and show the user the mode, the per level policy, any overrides, and the last pick for each role that has run.
2. If `$ARGUMENTS` asks for a change, translate it into commands, run them, then run `hive models` again and confirm the result:

| Request | Command |
|---|---|
| Pin one role | `hive models set <role-id> --model opus --effort high` |
| Pin a level | `hive models set worker --model sonnet --effort medium` |
| Pin everything | `hive models set all --model sonnet` |
| Remove a pin | `hive models unset <role-id or level or all>` |
| Change mode | `hive models mode bid` (or `parent`, `fixed`) |
| Change level policy | `hive models policy worker --allowed haiku sonnet --max-effort high --default sonnet --default-effort medium` |
| Apply to every project | add `--global` to any of the above (writes `~/.hive/config.json`; project settings still win) |
| Turn off escalation | `hive config set model_selection.escalate_on_retry false` |
| Change the bidder | `hive config set model_selection.bidder_model sonnet` |

Role IDs come from `hive roster`. Models: `haiku`, `sonnet`, `opus` (add others such as `fable` to `model_selection.tiers` and the allowed lists if the user wants them). Efforts: `low`, `medium`, `high`, `xhigh`, `max`.

3. If the user asks how models performed, run `hive retro` and present the "Model and effort outcomes" table (attempts, pass rate and spend per model and effort). Recommend a policy change only when the data supports it, for example a low pass rate for haiku workers means raising the worker floor to sonnet.
4. State the tradeoff of any change in one line: stronger models cost more per attempt but fail and retry less.
