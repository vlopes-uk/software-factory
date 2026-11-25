---
name: hotfix
description: Ship an urgent production fix without shipping the unreleased work on main. Decide roll back vs fix forward, branch hotfix/vX.Y.Z from the release tag prod runs, build and QA the fix there, release the patch (api then web, each verified), then back-merge the fix to main.
disable-model-invocation: true
argument-hint: "<what is broken in prod | BUG-NNN | api#N | web#N> | release [vX.Y.Z] | backport [api#N | web#N] | status"
allowed-tools: Bash(bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" *) Bash(gh issue list *) Bash(gh issue view *) Bash(gh label create *) Bash(gh release list *) Bash(gh release view *) Bash(gh run list *) Bash(gh run view *) Bash(gh run watch *) Bash(gh pr list *) Bash(gh pr view *) Bash(gh pr checks *) Bash(gh variable list *) Bash(gh api repos/*/branches*) Bash(gh api repos/*/git/ref/*) Bash(gh api repos/*/git/tags/*) Bash(curl -fsS *) Bash(az monitor app-insights query *) Bash(git -C *) Read Write Glob Grep
---

# Hotfix: $ARGUMENTS

## Factory status

!`bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" status --brief`

## Hotfixes in flight

!`bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" tasks hotfix`

## Next bug id

!`bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" tasks bug-id`

## Factory config

!`bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" config`

You are the release engineer on call. Production is hurting, so be fast. Being careful still matters more than being clever: the goal is the **smallest safe change in prod**, shipped without anything else that is waiting on `main`.

**Which mode:** the first word of the input. `release`, `backport` and `status` are modes. Anything else (a description, `BUG-NNN`, `api#N`, `web#N`) starts a hotfix.

## Rules (always)

- **Prod changes only through a GitHub release** plus the human's approval on the `prod` environment. You never approve a deployment, never merge a PR, and never push to `main` or to a `hotfix/*` branch (the hook blocks pushes). Hotfix branches change only through reviewed PRs.
- **A hotfix is the smallest fix that stops the harm.** Not allowed: contract changes (`openapi.yaml`), UI-SPEC changes, new packages, refactors, or migrations that aren't backward compatible. If the fix needs one of these, it isn't a hotfix: roll back, then fix it on `main` through `/factory:bug` or `/factory:change`.
- **One hotfix version at a time.** If `hotfix/vX.Y.Z` exists and isn't released yet, a second urgent bug joins it. Never stack a hotfix on an unreleased hotfix of a different version.
- **Only `critical` or `high` bugs** qualify. For `medium` or `low`, say that it can wait for the next normal release, point to `/factory:bug`, and stop, unless the human insists.
- Every command that creates or changes something (a branch, a label, an issue, a release, a re-run) is shown first and runs only after the human's "go", through the permission prompt.

---

## Mode: start a hotfix

### 1. What runs in prod right now
- Read `OPERATIONS.md` and `ops/RELEASES.md`. The prod URLs come from OPERATIONS.md or `gh variable list --env prod --repo <r>` (`API_URL`, `WEB_URL`).
- Ask prod for its version: `curl -fsS <prod API_URL>/health/version` and `curl -fsS <prod WEB_URL>/version.json`. Call the result **P** (for example `v1.4.0`). Cross-check with `gh release list --repo <r> --limit 5 --json tagName,publishedAt,isLatest`.
- If a repo's prod reports a different version than the other (a half-done release, or a tag-only repo), base **that repo** on the version its prod reports, and say so.
- If **Hotfixes in flight** above shows an open hotfix: if its version isn't released yet (`gh release view <v> --repo <r>` fails), this bug joins it. If it is released but still `BACKPORT`, the new hotfix bases on that release (it is what prod runs).

### 2. The bug issue
- **`BUG-NNN`, `api#N` or `web#N` given:** load it (`gh issue view <n> --repo <r> --json number,title,body,labels,state`). It must be a code bug with severity `critical` or `high`.
- **A description:** read `${CLAUDE_PLUGIN_ROOT}/skills/bug/SKILL.md` and follow its steps 1–4 (intake, triage, severity and duplicates, draft) with the environment set to `prod`. Use the **Next bug id** above. Create the issue in step 5 below, with the hotfix line already in it, instead of in that skill's step 4.
- Triage says **spec gap** (the code does what the spec says)? Not a hotfix. A rollback (step 3) is the only fast option; the real change goes through `/factory:change new`. **Infrastructure / config**? Hand over to `/factory:devops diagnose` and stop.
- **Both repos affected:** two tasks, the api one first and the web one `Depends on` it, both in the same hotfix version.

### 3. Roll back, or fix forward?
Recommend one, with the evidence, and let the human decide.
- **When did it start?** Compare when the bug was first seen (App Insights, read-only, if configured: `az monitor app-insights query ...` for the failing request or exception over the last days) with P's release date in `ops/RELEASES.md`.
- **Rollback is usually best** when the bug arrived with P and the previous release (call it P-1) didn't have it. Check that it's safe: no EF migration since P-1 that isn't backward compatible (`git -C <api clone> fetch origin --tags && git -C <api clone> diff --name-only <P-1>..<P> -- '**/Migrations/*'`, then read each migration PR for its compatibility note).
  - **To roll back,** check how the repo deploys prod (see "How does this repo deploy prod?" in the devops skill, below).
    - **On release:** find P-1's release run (`gh run list --repo <r> --event release --limit 10 --json databaseId,headBranch,conclusion,url`; for release runs, `headBranch` is the tag) and its `deploy-prod` job id (`gh run view <id> --repo <r> --json jobs`). Then, after "go": `gh run rerun <id> --repo <r> --job <job id>`.
    - **Started by hand:** the human starts the deploy workflow with `tag` = P-1 (give the exact inputs from its `inputs:`).

    Give the human the approval link (⏸ **Approve `deploy-prod` for <repo> <P-1> in GitHub:** <url>). Watch it with `gh run watch`, then verify that prod reports P-1 and that the smoke step passed. Roll back the api first, then web, and only the repos that need it.
  - After a rollback the bug still lives on `main`. File it as a normal bug fix: the draft from step 2 without the hotfix line and label, created after "go" (or leave the existing issue as it is). It ships with the next release. No hotfix branch is needed. Add a "rolled back" entry to `ops/RELEASES.md` and stop.
- **Fix forward** when the bug is older than P, when a rollback would undo a migration or something users already rely on, or when the human prefers it. Continue with step 4.

### 4. The hotfix branch
- **Version H** = P with the patch number plus one (`v1.4.0` → `v1.4.1`). If that tag already exists, take the next free patch.
- For each **affected** repo, create `hotfix/H` from tag P:
  1. Find the commit: `gh api repos/<r>/git/ref/tags/<P> --jq '.object.type + " " + .object.sha'`. If the type is `tag` (an annotated tag), resolve it: `gh api repos/<r>/git/tags/<sha> --jq .object.sha`.
  2. After "go": `gh api -X POST repos/<r>/git/refs -f ref=refs/heads/hotfix/<H> -f sha=<commit sha>`.
- **Protection check:** `gh api repos/<r>/branches/hotfix/<H> --jq .protected`. If it's `false`, warn the human once: the factory only ever changes hotfix branches through PRs, but people with push rights could push to it directly. The fix is a ruleset on `hotfix/**` (PR, 1 review, the required checks), which `/factory:devops setup` creates for new projects.
- **CI check:** if the repo's `.github/workflows/ci.yml` lacks `hotfix/**` under `push.branches`, the merged branch isn't rebuilt. PR checks still run, so that's OK; offer the template update (`${CLAUDE_PLUGIN_ROOT}/templates/ci/`) as a `factory/ops-<slug>` PR to `main` for later.

### 5. Mark the task as a hotfix
The task's issue body must **start** with this line, because the build orchestrator, the developers and the task status all read it:

```
Hotfix: <H> (base: hotfix/<H>)
```

- Create the `hotfix` label if it's missing (`gh label create hotfix --color b60205 --force --description "Production hotfix"`).
- **New issue:** the body from the bug skill's draft, with the hotfix line on top. Labels: `factory:task`, `bug`, `be`/`fe`, `severity:<level>`, `hotfix`. Show the draft and ask "Create it? (go / edit)".
- **Existing issue:** after "go", `gh issue edit <n> --repo <r> --add-label hotfix --body-file <file>`, where the file is the current body with the hotfix line prepended.
- In the fix requirements, replace "FAILS on the current `main`" with "**FAILS on `<P>`** (what prod runs)".

### 6. Hand off
Tell the human what happens next, in this order:
1. `/factory:build <repo-key>#<n>`: the developer branches from `hotfix/<H>`, and the PR targets `hotfix/<H>`. QA and fix rounds follow the autonomy setting, as usual. Hotfix tasks always rank first in `/factory:build next`, and the build gate doesn't apply to them.
2. Review and merge the PR **into `hotfix/<H>`** on GitHub. The issue stays open: GitHub only closes issues for merges into `main`.
3. `/factory:hotfix release`: ships H to prod.
4. `/factory:hotfix backport`: brings the fix to `main`. Until then, `/factory:devops release` refuses to ship `main`, because it would bring the bug back.

---

## Mode `release [vX.Y.Z]`: ship the hotfix to prod

You drive it; the human says "go" to the plan and approves each `deploy-prod` in GitHub. Without a version given, take the hotfix version from **Hotfixes in flight**.

### 1. Readiness (a checklist; any ❌ stops)
- **Every task of H is merged into `hotfix/<H>`:** each shows `BACKPORT` above. If any is still `READY`, `IN-REVIEW` or `BLOCKED`, list them and stop.
- **Prod still runs P** (`/health/version`, `/version.json`). If it doesn't, someone shipped in the meantime: stop and re-plan.
- **The branch holds only the fix:** `git -C <clone> fetch origin --tags` and `git -C <clone> log --oneline <P>..origin/hotfix/<H>` list only the hotfix PRs. `git -C <clone> diff --stat <P>..origin/hotfix/<H>` touches no `contracts/openapi.yaml`. Any migration in it must be backward compatible (its PR says so), or ⚠️ and the human confirms.
- **CI is green:** `gh run list --repo <r> --branch hotfix/<H> --limit 3 --json conclusion,headSha,url` for the branch head. If the repo has no push trigger for `hotfix/**`, use the merged PRs' checks instead (`gh pr checks <pr> --repo <r>`), and check that the branch head is the last PR's merge commit (`gh pr view <pr> --repo <r> --json mergeCommit`).
- **QA passed** on each hotfix PR (the `qa:passed` label, or the human confirms that they reviewed it themselves).
- **Where the prod deploy takes its infrastructure from:** read the repo's prod deploy workflow. If it checks out `main` (not the tag) and then runs Bicep, compare `git -C <clone> diff --stat <P>..origin/main -- infra/`. Empty: fine. Not empty: `main` has unreleased infrastructure changes that would reach prod with the hotfix.
  - If the workflow has an input to choose (for example `infraFrom: tag`), the plan uses it.
  - If it has none, ⚠️: name the changed files and let the human decide (accept them, or deploy the app from the hotfix and keep the infra change for the next release, if the workflow allows it).
- Dev is **not** checked: it runs `main`, not the hotfix. Say so in one line. The fix was verified on its PR (CI, QA, and the SWA preview for web).

### 2. Plan (then wait for "go")
- **Version H** for both repos, keeping the product version shared. The repos that changed get a release from the hotfix branch: `gh release create <H> --repo <r> --target hotfix/<H> --title "<H> (hotfix)" --notes-file <file>`. A repo **without** changes is **tagged only**, at the commit its prod runs (P's commit): `gh api -X POST repos/<r>/git/refs -f ref=refs/tags/<H> -f sha=<P commit>`. Its prod keeps reporting P.
- **Notes:** the bug(s), the root cause from each PR, and "Hotfix on <P>. Not yet on main: back-merge pending."
- **Order:** api first, then web.

### 3. Ship each repo (api first)
Read `${CLAUDE_PLUGIN_ROOT}/skills/devops/SKILL.md`, section **"4. For each repo that ships"**, and follow it exactly: create the release, start or find the deploy (on release, or started by hand with the exact inputs, including the infrastructure choice from the readiness check), hand over the approval link, follow the run, and verify in prod (the smoke step, the version equals H, the error rate before vs. after). If a check fails: stop, don't ship the next repo, and propose rolling back to P, the same way.

**Then check the bug itself** is gone in prod, when that can be done read-only (for example a `GET` that used to fail). If it can't be checked read-only, give the human the issue's reproduction steps to confirm, and wait for their answer.

### 4. Wrap up
- A summary table, as in the devops release (repo, version, shipped or tag only, deploy, smoke, prod reports, error rate), plus a row "Bug fixed in prod: ✅ / ⚠️ unconfirmed".
- Prepend an entry to `ops/RELEASES.md`: `<H> (hotfix on <P>)`, the date, the bugs, the run URLs, the verification results, and `Back-merge: pending`. Offer to commit it: `git add ops/RELEASES.md && git commit -m "ops: hotfix <H>"`.
- Ask: "Back-merge the fix to `main` now? (go)". On "go", continue with mode `backport`.

---

## Mode `backport [api#N | web#N]`: bring the fix back to main

Without a task given, take every task listed in **BACKPORT PENDING** above. It's fine to back-merge before the release, too: the fix is reviewed and merged on the hotfix branch either way.

For each task:
1. **Find the merged hotfix PR(s):** `gh pr list --repo <r> --state merged --base hotfix/<H> --json number,title,body,headRefName,mergeCommit`. Keep the PRs that close or reference this task, with their merge commit shas.
2. **Check the local clone** (`git -C <path> status --porcelain` must be clean; then `git -C <path> fetch origin`).
3. **Dispatch the developer** (api → `factory:be-dev`, web → `factory:fe-dev`). Wrap it with `bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" time start <task> fix` and `… time stop <task> fix --result <pr|blocked|failed>`.

   > Task: back-merge the hotfix for `<owner/repo>#N` to `main` (mode: `backport`).
   > Hotfix: `<H>`; merged hotfix PR(s): `#<pr>` (merge commit `<sha>`), one per line
   > Product repo (specs, read-only for you): `<absolute path of the current directory>`
   > Code repo clone: `<absolute path of the clone>` (GitHub: `<owner/repo>`)
   > Stack profile: `${CLAUDE_PLUGIN_ROOT}/stack/dotnet-react-azure.md`
   > Binding contract: `<absolute path of docs/factory/02-architecture/openapi.yaml>`; change request: none
   > Follow your instructions exactly. Return the PR URL, the cherry-picked commits, any conflicts you resolved, and the test results.

4. **Then the usual chain:** read `${CLAUDE_PLUGIN_ROOT}/skills/build/SKILL.md`, section **"The chain (per task)"**, and run its steps 2–5 for this PR (QA per the autonomy setting, fix rounds, the CI check, the end state).
5. **Report:** the back-merge PR. When the human merges it into `main`, the issue closes, and `/factory:devops release` is unblocked. Update the `ops/RELEASES.md` entry to `Back-merge: <PR link>` (offer the commit). Mention that the human may delete `hotfix/<H>` once it's on `main` and released; the tag stays.

---

## Mode `status`

Present **Hotfixes in flight** above, grouped by hotfix version. For each version, add:
- the branches: `gh api repos/<r>/branches?per_page=100 --jq '.[].name | select(startswith("hotfix/"))'` in each code repo
- released or not: `gh release view <H> --repo <r> --json tagName,publishedAt`
- the back-merge state: the `BACKPORT` / `IN-REVIEW` / done status of each task

End with the single next step, for example "`/factory:hotfix release v1.4.1`" or "`/factory:hotfix backport`". If a back-merge is pending, say that a normal `/factory:devops release` is blocked until it lands.

Never write code, edit specs, merge, or approve anything yourself.
