# Software Factory: a Claude Code plugin

Role-based agents that take a product from conversation to merged PRs, with a **human gate on every phase**.
Stack: **.NET 10 (C#) API + React 19/TypeScript web, on Azure, built with GitHub Actions.** See `plugins/factory/stack/dotnet-react-azure.md`.

## Repositories

| Repo | Holds | Issues | PRs |
| --- | --- | --- | --- |
| `<product>-product` (you run Claude Code here) | `docs/factory/**`, `.factory/**` | epics, stories | — |
| `<product>-api` | .NET solution, Bicep | `be` tasks | backend |
| `<product>-web` | React app, Bicep | `fe` tasks | frontend |

Clone `api` and `web` next to the product repo; `/factory:init` grants Claude Code access to them via `.claude/settings.json` (`permissions.additionalDirectories`) (`../<product>-api`, `../<product>-web`). Names and paths live in `.factory/config.json`.
The Architect's `openapi.yaml` is the binding contract between the two code repos: the API is tested against it, and the web app generates its types from it.

| Phase | Command | Role | Output |
| --- | --- | --- | --- |
| requirements | `/factory:po <idea>` | Product Owner | `PRD.md` + epics/stories (product repo) |
| architecture | `/factory:architect` + `/factory:devops plan` | Architect + DevOps | `ARCHITECTURE.md`, ADRs, **`openapi.yaml`**, `OPERATIONS.md` |
| design | `/factory:design` | UI Designer | `UI-SPEC.md` (React components, tokens) + HTML wireframes |
| planning | `/factory:plan` | Architect | `PLAN.md` + `be` issues in api, `fe` issues in web |
| test-plan | `/factory:qa plan` | QA | `TEST-PLAN.md` (AC → xUnit / Vitest / Playwright) |
| build | `/factory:build next` / `story S-03` / `api#12` | .NET / React dev subagents | Branch + PR in that repo |
| review | `/factory:qa review api#31` | QA reviewer subagent | Review + `qa:*` label |

Gate commands (human only): `/factory:approve <phase>` / `CR-NNN <phase>` / `merge CR-NNN`, `/factory:autonomy`, `/factory:status`, `/factory:init`. New features: `/factory:change`. Ideas you're not sure are possible: `/factory:explore`. Bugs: `/factory:bug`.

## Install

```bash
claude plugin marketplace add ./software-factory
claude plugin install factory@software-factory
```
Or push this folder to GitHub, then run `/plugin marketplace add <owner>/<repo>` and `/plugin install factory@software-factory`.

Requirements: Claude Code, **Git** (on Windows: Git for Windows, which provides Git Bash), **Python 3.9+**, and the `gh` CLI authenticated (`gh auth login`).

**Windows notes**
- Install Python from python.org and tick "Add python.exe to PATH". The plugin finds `python3`, `python` or `py -3`, so the missing `python3` command on Windows is fine, and the Microsoft Store placeholder alias is skipped.
- Install Git for Windows. The plugin's gates and hooks run in Git Bash, which Claude Code uses on Windows when it is installed.
- Keep LF line endings for this repo (`.gitattributes` does this) so the bash launchers keep working.
- All documents are read and written as UTF-8, so accents and symbols are safe on Windows consoles. Where builds run, you also need the .NET 10 SDK, Node LTS, and Docker (for Testcontainers).

## Use

```text
cd ~/code/myapp-product && claude
/factory:init <owner> myapp       # config, the 3 repos, labels, CLAUDE.md files
/factory:po A booking app for independent physiotherapists
/factory:approve requirements
/factory:architect                # and /factory:design, in either order
/factory:approve architecture
/factory:design
/factory:approve design
/factory:plan
/factory:approve planning
/factory:qa plan
/factory:approve test-plan
/factory:build api#1              # T-00-api walking skeleton; in parallel: /factory:build web#1
/factory:qa review api#2
# you review and merge the PR
```

## Choosing what gets built

```text
/factory:build next          # the next ready task: story priority (must > should > could), then plan order
/factory:build story S-03    # every ready task of one story, BE and FE in parallel
/factory:build api#12        # one specific task (add "fix" to address QA feedback on its PR)
```

`gate.py tasks` reads the task issues, their `Depends on` links, open PRs and story priorities from GitHub, and the build gates from `.factory/`. It classifies every task as READY, WAITING (dependency open), IN-REVIEW (PR open), BLOCKED (developer asked a question), GATED (build phase closed for its scope) or DONE. `next` and `story` show you the selection and start only after your "go". `/factory:status` includes the same task overview.

## Token efficiency

| Measure | How |
| --- | --- |
| **Model per role** | Planning roles (PO, Architect, Designer, planning, bug triage) and `/factory:build` orchestration use your session model. BE/FE developers and the QA reviewer run on **Sonnet, medium effort**. `/factory:status` uses Sonnet and `/factory:autonomy` uses Haiku. Change it with `model:` / `effort:` in `agents/*.md` or `skills/*/SKILL.md`. |
| **Spec pack** | `bash <plugin>/scripts/gate spec api#12` prints only what that task references: its story ACs, the contract operations plus the schemas they use, the `§N` architecture sections, ADRs, `SCR-NN` screens and `TC-NNN` test rows. The open CR's docs come first. Developers and QA read the pack, and open full docs only for items listed under MISSING. |
| **Incremental QA** | Every QA review records `Reviewed commit: <sha>`. A re-check after a fix reviews only the diff since then plus the rows that were ❌/⚠️, carrying ✅ rows over. |
| **Quiet output** | Agents run `dotnet` and `npm` in quiet mode, read failures only, run focused tests while working, and run the full suite once. `gh` calls use `--json` with only the fields needed. |
| **Code map** | Each code repo keeps a short `CODEMAP.md` (created by the walking skeletons, updated in PRs, checked by QA), so agents go straight to the right files instead of searching. |

Tip: start a fresh Claude Code session between build runs. All state lives in the repos, so nothing is lost. Measure with `/cost` or `/usage`.

## Fewer merge conflicts (and rebases)

Parallel PRs used to collide on the same few files, and every rebase cost the developer agents tokens. The factory now designs those hotspots out:

- **Generated API types aren't committed.** `src/api/schema.d.ts` is gitignored and regenerated by `npm run gen:api` (run automatically before dev, typecheck, test and build).
- **The vendored contract changes only in a sync-contract task** (`[T-NN] sync contract`), which the planner creates when the contract changes; feature tasks depend on it and never touch `contracts/`.
- **Features self-register.** Web routes and MSW mocks live in each feature folder and are collected with `import.meta.glob`; API endpoint groups, DI modules, validators and EF configurations are found by assembly scanning. No feature edits `Program.cs`, the router or a central handlers list. `tokens.css` is off-limits to feature tasks.
- **CODEMAP.md merges itself:** one row per feature, alphabetical, with `merge=union` in the code repo's `.gitattributes`.
- **No "branch must be up to date" rule.** DevOps sets branch protection with `strict: false` (or a merge queue). Developers rebase only on a real conflict, resolve lockfiles, migrations and generated files by taking `main`'s version and regenerating, with `git rerere` on. QA never fails a PR for being behind.
- **The planner sequences hotspots:** tasks touching the same screen, feature, shared component, entity or migration get a `Depends on` so they don't run in parallel.

Existing repos: run the "adopt conflict-free layout" task once per code repo (see the release notes).

## Reporting bugs

```text
/factory:bug On dev, two patients both got a confirmation for the same 10:00 slot
```

1. **Intake:** at most 4 questions (steps, expected vs actual, environment, evidence). Say "just file it" to skip.
2. **Triage against the approved specs:**
   - **code bug:** violates an acceptance criterion, the contract, a UI state or an NFR, so it becomes a fix task
   - **spec gap:** the code does what the spec says, so it suggests `/factory:change new` (or `/factory:explore new` if it's unclear how it could work)
   - **infra:** suggests `/factory:devops diagnose`
3. **Severity** (critical / high / medium / low) and a duplicate check.
4. **Draft, then "go":** creates `[BUG-NNN]` in the api or web repo with labels `factory:task`, `bug`, `be`/`fe`, `severity:*`. It links the story and AC it violates, includes the reproduction steps, and requires a **regression test that fails before the fix**.

**Priority:** critical and high bugs come before all feature work in `/factory:build next`. Medium ranks with "should" stories, low with "could".

**Fixing:**
- The developer reproduces the bug, commits the failing test first, fixes the root cause, and explains it in the PR.
- QA checks that the test fails on the base branch and passes on the PR.
- A closed story with an open bug fix is reopened by the sync until the fix is merged.

## Build autonomy: how much happens without you

Planning approvals, merging PRs and production deploys **always** need you. Autonomy only shapes the build phase, and only you can change it (the hook blocks agents):

```text
/factory:autonomy                      # show the current setting
/factory:autonomy preset assisted      # manual | assisted (default) | autopilot
/factory:autonomy set max_fix_rounds 2 # fine-tune one setting
```

| Preset | QA after each PR | Auto-fix rounds | Asks before starting | Continues to next ready task |
| --- | --- | --- | --- | --- |
| manual | no (run `/factory:qa review` yourself) | 0 | yes | no |
| **assisted** (default) | yes | 1 | yes | no |
| autopilot | yes | 2 | no | yes (max 5 per run, 3 in parallel) |

With auto QA on, each task runs as a chain: **developer → QA → (fix → QA) × rounds → stop**. Every run ends with a table showing ✅ ready to merge, ⚠️ needs you, or ⛔ blocked. Stored as `"autonomy"` in `.factory/config.json`. Add `no-qa` to a build command to skip QA for that run.

## Stories and epics close themselves

Every `/factory:status` and `/factory:build` first runs `gate.py tasks sync --apply`:
- a **story** closes when every task that implements it (`Implements <product>#N`) is closed. Merging a PR with `Closes #N` closes its task.
- an **epic** closes when all its stories (`Part of #N`) are closed
- if new open work later points at a closed story or epic (for example a change-request task), it is **reopened**
- each close or reopen leaves a comment saying why. Stories without tasks are left alone.

Preview without changing anything: `bash <plugin>/scripts/gate tasks sync`.

## DevOps (GitHub + Azure), kept lean

One command, no extra gate. `OPERATIONS.md` is approved together with the architecture.

| Command | When | What |
| --- | --- | --- |
| `/factory:devops plan [CR-NNN]` | after `/factory:architect` | `OPERATIONS.md`: environments, Azure resources and naming, Bicep modules, CI/CD, secrets, alerts, backups and rollback, monthly cost estimate |
| `/factory:devops setup` | once, before the walking skeletons | generates `ops/bootstrap-azure.sh` (RGs, app registration + OIDC federated credentials, RG-scoped role, Key Vault, budget alert) and `ops/bootstrap-github.sh` (dev/prod environments with you as prod reviewer, variables, branch protection), then walks you through them |
| `/factory:devops preview <dev\|prod>` | before infra changes ship | Bicep `what-if`: create / modify / delete per resource |
| `/factory:devops release` | to ship | readiness checks, changelog, api-before-web ordering, `gh release create`; each repo's `deploy-prod` job then **waits for your approval** in GitHub |
| `/factory:devops diagnose [run]` | when CI/CD or Azure fails | reads failed logs (+ App Insights), classifies the failure, offers a fix PR |

Guardrails (hook-enforced):
- no `az ... delete/purge`, no Complete-mode deployments
- no direct prod deploys; prod goes only through a GitHub release plus the `prod` environment approval
- the agent never holds prod credentials, and secrets are never printed

## When upstream documents change: review mode

When you re-approve a document (for example the PRD after `/factory:po revise ...`), everything downstream becomes **stale**. Don't just re-approve; run the owning role's review mode:

```text
/factory:architect review          # also: /factory:design review, /factory:plan review, /factory:qa review-plan
/factory:architect CR-001 review   # same for change requests; /factory:po CR-001 review if main moved under a CR
```

1. `gate.py review <phase>` prints the exact diff of each upstream document between the version this phase was approved against and now. Every approval stores a snapshot in `.factory/snapshots/`; older approvals fall back to git history.
2. The role produces an **impact report** with one row per upstream change: `none` / `update` / `question`, the exact section, operationId, screen or task affected, and the proposed change. The Architect also classifies contract changes as additive or **BREAKING**. Planning never reopens closed tasks; it adds follow-ups instead.
3. Nothing is edited until you say "go". Edits are targeted, and a `Revision history` line is added. If upstream was only re-approved with no content change, the review says re-approving as-is is safe.

## Not sure it's possible? Explorations

Use an exploration when you have an idea but don't know whether the app can do it, or how. You discuss it with an Explorer agent, it builds a working proof of concept, and you decide. Explorations can start at any time, even during the first cycle. They never block anything and are never blocked by anything.

```text
/factory:explore new Offline mode for patients   # creates EXP-001 and starts the discussion
#   the Explorer reads the PRD, architecture, contract and code maps, asks up to 4 questions,
#   researches options and writes FINDINGS.md: feasibility, 2-4 approaches, a recommendation,
#   and what a PoC must prove. You pick one; it records "Chosen approach:".
/factory:approve EXP-001 approach
/factory:explore EXP-001 poc                     # POC-BRIEF.md, then after your "go" the BE/FE devs
                                                 # build it in spike mode; POC-REPORT.md + a demo script
/factory:explore EXP-001 poc <feedback>          # optional iterations
/factory:approve EXP-001 poc                     # after you ran the demo locally
/factory:explore promote EXP-001                 # creates CR-NNN, pre-filled from the exploration
/factory:explore reject EXP-001 <why>            # or: keep the findings on file and stop
```

| Step | Output (in `docs/factory/explorations/EXP-NNN-<slug>/`) | Gate |
| --- | --- | --- |
| Discuss | `FINDINGS.md` | `approve EXP-NNN approach` needs a filled-in `Chosen approach:` |
| PoC | `POC-BRIEF.md`, `POC-REPORT.md`, code on `spike/EXP-NNN-<slug>` in api/web | writable only after the approach is approved; `approve EXP-NNN poc` |
| Decide | `DECISION.md` (written by the gate) | `promote` needs both approvals, both still current; `reject` is human-only |

- **Spike mode:** the developer subagents (Sonnet) build the thinnest slice that proves the brief's claims. Tests and QA are optional, and shortcuts and extra packages are allowed but listed. They push the `spike/...` branch (CI doesn't run on it) and leave a local worktree under `.factory-worktrees/<api|web>-EXP-NNN` for you to run the demo from.
- **Spike code is reference only.** The hook refuses PRs from spike branches, merging or cherry-picking them, and pushing them to `main`. After promotion, the CR's developers rebuild the feature properly, with tests and the contract.
- **Promoted CRs know their origin.** Every CR role's gate check prints the exploration. The PO drafts from the findings and asks only what's left open, and the Architect records the chosen approach as an ADR.
- **Drift:** editing `FINDINGS.md` after approval marks the approach `changed`. Re-approving it marks the PoC `stale`. Either blocks promotion until you re-approve.
- Rejected explorations stay on file with the reason, so the same idea isn't explored again blind. `/factory:status` lists every exploration.
- If you already know what you want, skip all this and use `/factory:change new`.

## New features after the cycle started: change requests

```text
/factory:change new Online payments     # creates CR-001 (docs/factory/changes/CR-001-online-payments/)
/factory:po CR-001                       # requirements delta: new + modified stories
/factory:approve CR-001 requirements
/factory:architect CR-001                # delta + full CR openapi.yaml; flags BREAKING changes
/factory:design CR-001                   # or "No UI changes"
/factory:approve CR-001 architecture
/factory:approve CR-001 design
/factory:plan CR-001                     # tasks labelled cr:CR-001
/factory:approve CR-001 planning
/factory:qa plan CR-001
/factory:approve CR-001 test-plan
/factory:build api#40                    # CR tasks build like any other
/factory:change merge CR-001             # folds the deltas into the main docs
/factory:approve merge CR-001            # human: main is re-baselined, CR closed
```

- Each CR is gated on its own phases **and** on the approved main baseline. A CR in progress never blocks main builds or other CRs.
- The CR's `openapi.yaml` is the full contract (baseline plus the change), so developers on CR tasks build against one file.
- **Merge safety:** the gate refuses `approve merge` unless main's contract is byte-identical to the CR's. It also refuses if another CR changed the main contract after this CR's architecture was approved; run `/factory:architect CR-001 rebase` and re-approve.
- To change requirements that are still in the *first* cycle, use `/factory:po revise ...` as before.

## How the gates work

- `scripts/gate.py` keeps `.factory/state.json`. An approval stores the SHA-256 of the phase's artifact folder.
- Each role skill runs `gate.py check <phase> --soft` through `!` injection before the model sees the prompt. When blocked, it prints `GATE BLOCKED` plus the reason, and the role stops.
- `hooks/protect_state.py` (PreToolUse, applies to subagents too) is the hard enforcement. It refuses:
  - writes into a phase's `docs/factory/0N-*` folder while upstream isn't approved, whether by Write/Edit or by a shell write
  - `gh pr create` while the build phase is closed
  - any edit to the state file
  - any model-initiated `gate.py approve`, or `gate.py explore reject`
  - writes into closed or unregistered explorations, and PoC docs before the approach is approved
  - PRs, merges, cherry-picks or pushes to `main` from `spike/EXP-*` branches
- If you edit an approved doc, that phase becomes `changed`. Re-approving it marks every downstream phase `stale`. Both states block the phases that depend on them.
- `/factory:approve` works only for you, because its `!` injection runs outside the tool pipeline and the skill has `disable-model-invocation: true`.
- For team use, add GitHub branch protection plus a CODEOWNERS entry on `docs/factory/` and `.factory/`, so approvals are also reviewed in PRs.

## Test

```bash
python3 plugins/factory/tests/test_gate.py
claude plugin validate ./plugins/factory
```
