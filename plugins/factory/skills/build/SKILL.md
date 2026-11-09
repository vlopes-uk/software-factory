---
name: build
description: Build factory tasks: dispatch the .NET/React developer subagents and, depending on the autonomy setting, chain QA review and fix rounds. `next`, `story S-NN`, or `api#N`/`web#N`.
disable-model-invocation: true
arguments: target extra
argument-hint: "next | story S-NN | <api#N | web#N | owner/repo#N> [fix | no-qa]"
allowed-tools: Bash(bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" *) Bash(gh issue view *) Bash(gh pr list *) Bash(gh pr view *) Bash(git -C *)
---

# Build: $ARGUMENTS

## Story and epic sync (runs automatically)

!`bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" tasks sync --apply`

A story closes when every task that implements it is closed, which happens when you merge its PR. An epic closes when all its stories are closed. If new open work later points at a closed story or epic (for example a change-request task), it is reopened. **If the sync output above contains `SYNC APPLIED` or `SYNC FAILED` lines, the first line of your reply must report them**, for example "Closed on GitHub: story S-02 (all tasks done), epic E-01." Say nothing about the sync when it's up to date or unavailable.


## Gate status

!`bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" status --brief`

## Task selection (computed from GitHub + the gates)

!`bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" tasks plan "$target" "$extra"`

## Build autonomy (set by the human with /factory:autonomy)

!`bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" autonomy show`

## Factory config

!`bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" config`

Task statuses in the selection above:
- **READY:** can start now
- **WAITING:** a `Depends on` task is still open
- **IN-REVIEW:** a PR is already open
- **BLOCKED:** a developer stopped with a question on the issue
- **GATED:** the build phase of its scope (main, or its change request) isn't open
- **DONE:** the issue is closed

Trust this selection; don't re-derive it by hand. If it says `TASKS UNAVAILABLE`, relay the reason and ask the user to name the task (`/factory:build api#N`).

Follow the **autonomy settings above exactly**. They're the human's decision. You never change them, never merge a PR, and never approve a planning phase, whatever they say. The word `no-qa` in the user's input turns off `auto_qa` for this run only.

---

## Which mode

- **`next`:** the selection shows `NEXT: <ref> …`. Present it in one or two lines (story, priority, what it does), plus the runners-up.
  - `confirm_dispatch = true`: ask "Start `<ref>`? (go / pick another)" and wait.
  - `confirm_dispatch = false`: say "Starting `<ref>`", then run its chain.
  - `NEXT: none ready`: explain why from the list and stop.
- **`story S-NN`:** show the story's task table. A `DISPATCH:` line lists the ready tasks; say which are held back and why. Start at most `max_parallel` of them at once, and queue the rest until a slot frees.
  - `confirm_dispatch = true`: ask **once** for "go" for the whole set.
  - `confirm_dispatch = false`: start straight away.
  - No `DISPATCH:` line: explain and stop.
- **`<api#N | web#N | owner/repo#N> [fix]`:** a single task. If its status is not READY:
  - IN-REVIEW: proceed only with `fix`.
  - WAITING: ask, noting that FE tasks may proceed against the MSW-mocked contract.
  - BLOCKED or GATED: stop and relay the reason.

  The user named the task, so no confirmation is needed.

## The chain (per task)

Run this for each started task. Separate tasks' chains can run in parallel, up to `max_parallel` developers at once.

1. **Develop.** Dispatch the developer as described under *Dispatch* below. Keep the returned PR number. If the developer reports `factory:blocked` or no PR, the chain ends with **needs you**.
2. **QA** (only if `auto_qa` is true and `no-qa` wasn't given). Dispatch the **qa-reviewer** subagent (`subagent_type: "factory:qa-reviewer"`) with the same prompt `/factory:qa review` uses: the PR `<owner/repo>#<pr>`, the clone path, the product repo path, the stack profile, and the binding contract / CR folder.
3. **Fix loop.** While QA says **CHANGES REQUESTED** and fewer than `max_fix_rounds` fix rounds have run for this task in this run:
   - dispatch the same developer in **fix** mode (`mode: fix`, same PR); it reads the QA review on the PR
   - then dispatch QA again on the same PR

   Count the rounds yourself and **never exceed `max_fix_rounds`**. If a fix round makes no progress (the same failures again), stop early.
4. **End state.** Record exactly one of:
   - ✅ **Ready to merge:** QA passed (or QA is off and the developer's checks passed). Give the PR link. Being behind `main` doesn't matter; only a real conflict (`gh pr view --json mergeable` = `CONFLICTING`) needs `/factory:build fix <repo>#N`, which applies the cheap-rebase rules.
   - ⚠️ **Needs you:** QA still requests changes after the allowed rounds. Give the open issues in 3 lines max and the PR link.
   - ⛔ **Blocked:** the developer asked a question or the gate refused. Give the reason.
5. **Continue** (only if `auto_continue` is true). When a chain ends and fewer than `max_tasks_per_run` tasks have started in this run, run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" tasks plan next` and start its `NEXT:` task the same way, without asking. Tasks that wait on unmerged work won't show as ready, so the run stops naturally. Stop when nothing is ready or the cap is reached.

## Dispatch (developer, per task)

1. **Route by repo:** api → `factory:be-dev` (.NET); web → `factory:fe-dev` (React).
2. **Load the issue:** `gh issue view N --repo <repo> --json number,title,body,labels,state`.
   - **Gate check:** if the body contains `Change request: CR-NNN`, run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" check build --scope CR-NNN`; otherwise run `… check build`. On `GATE BLOCKED`, end this chain as ⛔ Blocked.
   - **Binding contract:** for an *open* CR, `docs/factory/changes/CR-NNN-*/02-architecture/openapi.yaml` (the full contract). Otherwise, including merged CRs, `docs/factory/02-architecture/openapi.yaml`.
3. **Check the local clone:** `git -C <path> status --porcelain` and `git -C <path> fetch origin`. It must exist and be clean; if not, stop and tell the user. Developers use their own worktrees, so one clean clone serves parallel tasks.
4. **Launch** with the Agent tool:

   > Task: implement `<owner/repo>#N` (mode: `<fix or empty>`; empty means new work, `fix` means address the QA review and comments on the existing PR).
   > Product repo (specs, read-only for you): `<absolute path of the current directory>`
   > Code repo clone: `<absolute path of the clone>` (GitHub: `<owner/repo>`)
   > Stack profile: `${CLAUDE_PLUGIN_ROOT}/stack/dotnet-react-azure.md`
   > Binding contract: `<absolute path chosen in step 2>`; change request: `<CR-NNN or none>`
   > Follow your instructions exactly. Return the PR URL, a summary of the changes, the test results, and anything you could not do.

## Final report

Finish with one table, one row per task started in this run:

| Task | PR | Result | Rounds | Next step for you |
| --- | --- | --- | --- | --- |
| api#12 T-02 | #31 | ✅ Ready to merge | QA ✓ after 1 fix | Review & merge on GitHub |

Then add one line: "Merging is yours. After you merge, `/factory:status` closes finished stories and epics, and `/factory:build next` continues."

You only orchestrate. Don't write code in this session.
