---
name: autonomy
description: Human-only. Show or change how autonomously the build phase runs (auto QA, fix rounds, confirmations, auto-continue). Planning approvals and merges always stay with the human.
disable-model-invocation: true
model: haiku
arguments: action key value
argument-hint: "[show | preset <manual|assisted|autopilot> | set <key> <value>]"
allowed-tools: Bash(bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" *)
---

# Build autonomy

The gate script has already applied the request, before you were invoked:

!`bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" autonomy "$action" "$key" "$value"`

## Your job

1. Relay the output above in a short code block.
2. Explain the current setting in two or three plain sentences: what `/factory:build` will now do on its own, and where it will stop for the user.
3. If the user seems unsure, describe the presets in one line each:
   - **manual:** the developer opens a PR, then you run QA yourself. Nothing is chained.
   - **assisted** (default): QA runs automatically on each PR, with 1 automatic fix round. Starting tasks still asks for "go".
   - **autopilot:** QA runs automatically, with up to 2 fix rounds. Ready tasks start without asking, and the run continues to the next ready task (max 5 per run, 3 in parallel).

   Fine-tune with `/factory:autonomy set <key> <value>`, for example `set max_fix_rounds 2`.
4. Always add: "Planning approvals, merging PRs and production deploys always need you, whatever the setting."

You must never change autonomy yourself. The plugin's hook blocks agents from doing so.
