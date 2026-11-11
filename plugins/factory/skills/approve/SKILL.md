---
name: approve
description: Human gate. Approve a main phase, a change-request phase, the merge of a change request into the main docs, or an exploration step (approach / PoC).
disable-model-invocation: true
arguments: first second
argument-hint: "<phase> | CR-NNN <phase> | merge CR-NNN | EXP-NNN <approach|poc>"
allowed-tools: Bash(bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" *) Bash(git add *) Bash(git commit *) Bash(git status *)
---

# Approve: $ARGUMENTS

The gate script has already executed the approval, before you were invoked:

!`bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" approve "$first" "$second" || true`

The accepted forms are:

- `/factory:approve <phase>`: a main baseline phase (`requirements`, `architecture`, `design`, `planning`, `test-plan`)
- `/factory:approve CR-NNN <phase>`: a phase of a change request
- `/factory:approve merge CR-NNN`: accept that the CR's docs have been merged into the main docs. Main is re-baselined, and the CR's tasks fall under the main build gate.
- `/factory:approve EXP-NNN approach`: accept the exploration's `FINDINGS.md` and its chosen approach. This opens the proof of concept.
- `/factory:approve EXP-NNN poc`: accept the proof of concept (`POC-BRIEF.md` + `POC-REPORT.md`) after trying the demo. With both approved, `/factory:explore promote EXP-NNN` can create the change request.

If the output says `GATE:` or `Usage:` instead of `APPROVED:` or `MERGED:`, **nothing was approved**. Explain why, using the output, and stop.

## Your job

1. Relay the gate output above to the user verbatim in a short code block.
2. Offer to commit so the approval is traceable in git: `git add docs/factory .factory && git commit -m "factory: approve $ARGUMENTS"`. Only commit if the user agrees.
3. Tell the user the next command (the gate output's `Next:` lines).

You must never approve anything yourself, by any means. This skill exists only for the human.
