---
name: status
description: Show the factory's phase gates, what is approved, changed or stale, time spent vs estimates (remaining, forecast, deviations), and the next step.
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

**Hotfixes:** run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" tasks hotfix`. If it lists open hotfixes, report them first, in one or two lines, with the next step (`/factory:build <ref>`, `/factory:hotfix release` or `/factory:hotfix backport`). A `BACKPORT PENDING` line means the fix is in prod (or about to be) but not on `main`: say that `/factory:devops release` is blocked until the back-merge lands. Say nothing when there are none.

If the build phase is `ready`, run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" tasks list` and summarise the open tasks by status (READY / WAITING / IN-REVIEW / BLOCKED / GATED), grouped by story. Then suggest `/factory:build next`, or `/factory:build story S-NN` for a story whose tasks are all ready.

## Time: estimates, spent, remaining

!`bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" time report all --export`

Two kinds of hours, never added together: **agent hours** (the factory's developers, QA and fix rounds, measured automatically by `/factory:build`) and **your hours** (the manual QA checks, timed with `/factory:qa manual`). The human-dev figures are the plan's estimate of what a human developer would need, compared with QA's estimate after each task. Summarise in at most 7 lines:

- **Your manual QA:** checks passed out of the total, your hours spent and still needed, and any failed or in-progress check. If there are open checks, mention `/factory:qa manual`.
- **Where we are:** tasks done, hours spent against the estimate, and the hours still needed: the estimate-based remainder **and** the calibrated forecast. Lead with the forecast, and say what it is based on (the calibration factor and sample count, or "uncalibrated" when there is too little history).
- **Deviations:** the 1–3 biggest overruns from the report, and which stage the extra time went to (dev, QA, or fix rounds). Point out a pattern if one category keeps running over (for example, `fe` tasks or fix rounds).
- **Open tasks already over their estimate**, if any.
- If the report says `TIME UNAVAILABLE`, mention it in one line and skip the rest of this section. Mention the dashboard data refresh only if the output contains `EXPORT FAILED`.

Close the section with: "Dashboard: `docker compose up -d` in the factory repo's `dashboard/` folder, then http://localhost:3000." Say it once; don't repeat the setup steps.

