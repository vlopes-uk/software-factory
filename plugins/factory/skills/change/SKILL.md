---
name: change
description: Change requests for new features after the factory cycle started. `new <title>` opens a CR, `list` shows them, `merge CR-NNN` folds an approved CR's docs into the main docs.
disable-model-invocation: true
argument-hint: "new <feature title> | list | merge CR-NNN"
allowed-tools: Bash(bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" *) Bash(gh label create *) Bash(gh issue *) Read Write Edit Glob Grep
---

# Change requests

Requested: **$ARGUMENTS**

## Current factory status

!`bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" status`

## Factory config

!`bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" config`

A **change request (CR)** is a new feature, or a change to an existing one, that runs through its own small set of phases: requirements → (architecture ∥ design) → planning → test-plan → build. These phases are gated by the human like the main cycle, **without blocking the main build or other CRs**. Its documents are *deltas* and live in `docs/factory/changes/CR-NNN-<slug>/`. Once a CR is approved, `merge` folds them into the main docs.

---

## `new <title>`

0. If the user doesn't yet know whether the feature is possible, or how to do it (new technology, an unknown integration, a big architectural question), suggest `/factory:explore new <idea>` first: discussion, then a working proof of concept, then promotion into a pre-filled CR. If they want to go ahead anyway, continue.
1. Run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" change new <title words>` and show the output.
2. If GitHub is configured, create the label in all three repos: `gh label create "cr:CR-NNN" --repo <repo> --color 7057ff --description "<title>" --force`.
3. Tell the user the next step: `/factory:po CR-NNN`. The PO interviews them about the feature.
4. Explain the flow in 4 lines:
   - each role takes the CR id as its first argument (`/factory:architect CR-NNN`)
   - approvals look like `/factory:approve CR-NNN <phase>`
   - the CR's tasks build like any others
   - `/factory:change merge CR-NNN` folds the docs into main at the end

## `list`

Run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" change list`, and summarise each CR's next step from the status above.

## `merge CR-NNN`

You fold an approved CR into the main documents, acting on behalf of each role owner. Preconditions: every CR phase shows `approved` in the status above, and main has no `stale` phases. If either isn't true, stop and say what's missing.

Let `C` be the CR folder `docs/factory/changes/CR-NNN-<slug>`. Apply the deltas **precisely**, without rewriting unrelated text:

| From (CR) | Into (main) | How |
| --- | --- | --- |
| `C/01-requirements/REQUIREMENTS.md` | `docs/factory/01-requirements/PRD.md` | Add the new stories under their epics, keeping their IDs. Apply the "Modified stories" edits in place. Add new personas, metrics and NFRs. Add a line to a "Change history" section: `CR-NNN <title>, merged <date>`. |
| `C/02-architecture/ARCHITECTURE.md` | `docs/factory/02-architecture/ARCHITECTURE.md` | Merge each delta section into the matching main section. |
| `C/02-architecture/adr/*` | `docs/factory/02-architecture/adr/` | Copy them; the numbers already continue the main sequence. |
| `C/02-architecture/openapi.yaml` | `docs/factory/02-architecture/openapi.yaml` | **Replace main with the CR file byte for byte** (`cp`). The CR contract is the full contract. The gate verifies they are identical. |
| `C/03-design/UI-SPEC.md` + `wireframes/` | `docs/factory/03-design/` | Merge the screens, components and tokens, and copy the wireframes. Skip this if the CR says "No UI changes". |
| `C/04-plan/PLAN.md` | `docs/factory/04-plan/PLAN.md` | Append a milestone `CR-NNN <title>` containing its tasks table. |
| `C/05-qa/TEST-PLAN.md` | `docs/factory/05-qa/TEST-PLAN.md` | Append its rows to the traceability matrix and add its NFR checks. |

Leave the CR folder in place as history. When you're done, list exactly what changed in each main file and tell the user:

> Review the main docs (for example with `git diff docs/factory`), then run **`/factory:approve merge CR-NNN`**. Until you do, the main phases show as `changed` and new main-scope PRs are paused.

If the gate later refuses the merge because the main contract changed since the CR's architecture was approved (another CR merged first), tell the user to run `/factory:architect CR-NNN rebase`, then re-approve the CR's architecture and its downstream phases, and then merge again.

Never run `/factory:approve` or edit `.factory/state.json` yourself.
