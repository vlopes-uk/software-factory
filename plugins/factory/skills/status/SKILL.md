---
name: status
description: Show the factory's phase gates, what is approved, changed or stale, and the next step.
disable-model-invocation: true
allowed-tools: Bash(bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" *) Bash(gh issue list *) Bash(gh pr list *)
---

# Factory status

!`bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" status`

Present the output above as-is: the main baseline table, then the change requests. Then explain in a few sentences:

- what each `changed` or `stale` phase means for them (an approved doc was edited, or an upstream doc was re-approved, so the work that depends on it is blocked until they re-approve)
- that change requests are gated independently, so a CR in progress never blocks main builds
- the next steps, from the `Next:` lines. For a `stale` phase that is the role's **review mode** (for example `/factory:architect review`), which shows exactly what changed upstream and what it affects. Recommend it before re-approving.

If the user wants to add a new feature, point them to `/factory:change new <title>`.

If the build phase is `ready`, run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" tasks list` and summarise the open tasks by status (READY / WAITING / IN-REVIEW / BLOCKED / GATED), grouped by story. Then suggest `/factory:build next`, or `/factory:build story S-NN` for a story whose tasks are all ready.
