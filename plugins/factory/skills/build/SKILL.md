---
name: build
description: Dispatch factory tasks to the .NET or React developer subagents. `next` picks the next ready task, `story S-NN` starts every ready task of a story, `api#N`/`web#N` builds one task.
disable-model-invocation: true
arguments: target extra
argument-hint: "next | story S-NN | <api#N | web#N | owner/repo#N> [fix]"
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

---

## Which mode

- **`next`:** the selection shows `NEXT: <ref> …`. Present that task in one or two lines (story, priority, what it does), plus the runners-up. Ask: "Start `<ref>`? (go / pick another)". On "go", dispatch it (see below). If it says `NEXT: none ready`, explain why from the list (waiting on what, gated by what) and stop.
- **`story S-NN`:** show the story's task table. If there's a `DISPATCH:` line, say which tasks will start and which are held back, and why. Ask **once**: "Start these N tasks in parallel? (go / choose)". On "go", dispatch all of them **in parallel**: one Agent call per task, all in the same message. No `DISPATCH:` line means nothing is ready, so explain and stop.
- **`<api#N | web#N | owner/repo#N> [fix]`:** a single task. If its status is not READY:
  - IN-REVIEW: proceed only with `fix`.
  - WAITING: ask, noting that FE tasks may proceed against the MSW-mocked contract.
  - BLOCKED or GATED: stop and relay the reason.
  Then dispatch it. The user already named it, so no extra confirmation is needed.

## Dispatch (per task)

1. **Route by repo:** api → subagent `factory:be-dev` (.NET); web → subagent `factory:fe-dev` (React).
2. **Load the issue:** `gh issue view N --repo <repo> --json number,title,body,labels,state`.
   - **Gate check:** if the body contains `Change request: CR-NNN`, run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" check build --scope CR-NNN`; otherwise run `… check build`. If it prints `GATE BLOCKED`, skip this task and report it.
   - **Binding contract:** for an *open* CR, use `docs/factory/changes/CR-NNN-*/02-architecture/openapi.yaml` (the full contract). Otherwise, including merged CRs, use `docs/factory/02-architecture/openapi.yaml`.
3. **Check the local clone:** `git -C <path> status --porcelain` and `git -C <path> fetch origin`. It must exist at the configured path and be clean. If not, stop and tell the user. Developers work in their own worktrees, so one clean clone serves parallel tasks.
4. **Launch the subagent** with the Agent tool. Prompt:

   > Task: implement `<owner/repo>#N` (mode: `<fix or empty>`; empty means new work, `fix` means address review feedback on the existing PR).
   > Product repo (specs, read-only for you): `<absolute path of the current directory>`
   > Code repo clone: `<absolute path of the clone>` (GitHub: `<owner/repo>`)
   > Stack profile: `${CLAUDE_PLUGIN_ROOT}/stack/dotnet-react-azure.md`
   > Binding contract: `<absolute path chosen in step 2>`; change request: `<CR-NNN or none>`
   > Follow your instructions exactly. Return the PR URL, a summary of the changes, the test results, and anything you could not do.

5. **Report**, in under 10 lines per task: the PR link, what was done, the test status, and any blockers. Then suggest `/factory:qa review <repo-key>#<pr>` for each PR, and `/factory:build next` (or the story again) to continue.

You only dispatch. Don't write code in this session.
