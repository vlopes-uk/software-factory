---
name: build
description: Dispatch a factory task issue (in the api or web repo) to the .NET or React developer subagent, which implements it in that repo and opens a PR.
disable-model-invocation: true
argument-hint: "<api#N | web#N | owner/repo#N> [fix]"
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/gate.py *) Bash(gh issue view *) Bash(gh pr list *) Bash(gh pr view *) Bash(git -C *)
---

# Build task $0

!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/gate.py check build --soft`

**If the line above starts with `GATE BLOCKED`, stop immediately.** Relay the reason to the user, and do nothing else. The plugin's hook would refuse your writes anyway.

## Factory config

!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/gate.py config`

## Dispatch procedure

1. **Resolve the issue reference `$0`.** `api#12` means `repos.api.github` issue 12, and `web#7` means `repos.web.github` issue 7. A full `owner/repo#N` is matched against the config. A bare number is ambiguous: ask which repo.
   - The repo decides the developer: **api → `factory:be-dev` (.NET)**, **web → `factory:fe-dev` (React)**.
2. **Load the issue.** Run `gh issue view N --repo <repo> --json number,title,body,labels,state`. It must be open and labelled `factory:task`. If its `be`/`fe` label contradicts the repo, stop and ask.
3. **Check dependencies.** Each `Depends on owner/repo#N` line in the body must be closed (`gh issue view N --repo owner/repo --json state`). If any are open, list them and ask whether to proceed. FE tasks may proceed against the mocked contract (MSW); say so.
4. **Check the local clone.** `git -C <path> status --porcelain` and `git -C <path> fetch origin`. The clone must exist at the configured path and be clean. If it isn't, stop and tell the user.
5. **Check for existing work.** `gh pr list --repo <repo> --search "N in:body" --json number,headRefName,state`.
   - `$1` = `fix` → the developer addresses the QA review and comments on the existing PR, on the same branch.
   - An open PR exists and there is no `fix` → ask before starting over.
6. **Launch the subagent** with the Agent tool. Prompt:

   > Task: implement `<owner/repo>#N` (mode: `$1`; empty means new work, `fix` means address review feedback on the existing PR).
   > Product repo (specs, read-only for you): `<absolute path of the current directory>`
   > Code repo clone: `<absolute path of the clone>` (GitHub: `<owner/repo>`)
   > Stack profile: `${CLAUDE_PLUGIN_ROOT}/stack/dotnet-react-azure.md`
   > Follow your instructions exactly. Return the PR URL, a summary of the changes, the test results, and anything you could not do.

   You can dispatch several independent tasks in parallel. Each developer works in its own `git worktree`, so they don't collide.
7. **Report** to the user in under 10 lines: the PR link, what was done, the test status, and any blockers. Then suggest `/factory:qa review <repo-key>#<pr>`.

You only dispatch. Don't write code in this session.
