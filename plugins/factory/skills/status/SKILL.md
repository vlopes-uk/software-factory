---
name: status
description: Show the factory's phase gates, what is approved, changed or stale, and the next step.
disable-model-invocation: true
model: sonnet
allowed-tools: Bash(bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" *) Bash(gh issue list *) Bash(gh pr list *)
---

# Factory status

## Story and epic sync (runs automatically)

!`bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" tasks sync --apply`

A story closes when every task that implements it is closed, which happens when you merge its PR. An epic closes when all its stories are closed. If new open work later points at a closed story or epic (for example a change-request task), it is reopened. **If the sync output above contains `SYNC APPLIED` or `SYNC FAILED` lines, the first line of your reply must report them**, for example "Closed on GitHub: story S-02 (all tasks done), epic E-01." Say nothing about the sync when it's up to date or unavailable.

## Phase gates

!`bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" status`

Present the gate output above as-is: the main baseline table, then the change requests. Then explain in a few sentences:

- what each `changed` or `stale` phase means for them (an approved doc was edited, or an upstream doc was re-approved, so the work that depends on it is blocked until they re-approve)
- that change requests are gated independently, so a CR in progress never blocks main builds
- explorations (`EXP-NNN`), if any: where each one stands (discuss, PoC, ready to promote), and that they never block anything
- the next steps, from the `Next:` lines. For a `stale` phase that is the role's **review mode** (for example `/factory:architect review`), which shows exactly what changed upstream and what it affects. Recommend it before re-approving.

If the user wants to add a new feature, point them to `/factory:change new <title>`.

If the build phase is `ready`, run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" tasks list` and summarise the open tasks by status (READY / WAITING / IN-REVIEW / BLOCKED / GATED), grouped by story. Then suggest `/factory:build next`, or `/factory:build story S-NN` for a story whose tasks are all ready.
