---
name: approve
description: Human gate. Approve a factory phase (requirements, architecture, design, planning, test-plan) so the next role can start.
disable-model-invocation: true
argument-hint: <requirements|architecture|design|planning|test-plan>
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/gate.py *) Bash(git add *) Bash(git commit *) Bash(git status *)
---

# Approve phase: $0

The approval has already been executed by the gate script, before you were invoked:

!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/gate.py approve $0 || true`

If the output says `GATE:` instead of `APPROVED:`, nothing was approved: explain why, using the output, and stop.

## Your job

1. Relay the gate output above to the user verbatim in a short code block.
2. Offer to commit the approved artifacts so the approval is traceable in git:
   `git add docs/factory .factory && git commit -m "factory: approve $0"`. Only commit if the user agrees.
3. Tell the user which command to run next (the gate output's "Next:" line).

You must never approve a phase yourself, by any means. This skill exists only for the human.
