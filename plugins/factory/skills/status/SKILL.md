---
name: status
description: Show the factory's phase gates, what is approved, changed or stale, and the next step.
disable-model-invocation: true
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/gate.py *) Bash(gh issue list *) Bash(gh pr list *)
---

# Factory status

!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/gate.py status`

Present the table above to the user as-is, then explain in one or two sentences:

- what each `changed` or `stale` phase means for them (an approved doc was edited, or an upstream doc was re-approved, so downstream work is blocked until they re-approve), and
- the single next step.

If the build phase is `ready` and GitHub is available, also summarise in a small table: open `factory:task` issues (`gh issue list --label factory:task --state open --json number,title,labels`) and open PRs on `factory/*` branches (`gh pr list --json number,title,headRefName,labels`), including any with the `qa:*` or `factory:blocked` labels.
