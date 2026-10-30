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
| architecture | `/factory:architect` | Architect | `ARCHITECTURE.md`, ADRs, **`openapi.yaml`** |
| design | `/factory:design` | UI Designer | `UI-SPEC.md` (React components, tokens) + HTML wireframes |
| planning | `/factory:plan` | Architect | `PLAN.md` + `be` issues in api, `fe` issues in web |
| test-plan | `/factory:qa plan` | QA | `TEST-PLAN.md` (AC → xUnit / Vitest / Playwright) |
| build | `/factory:build api#12` / `web#7` | .NET / React dev subagents | Branch + PR in that repo |
| review | `/factory:qa review api#31` | QA reviewer subagent | Review + `qa:*` label |

Gate commands (human only): `/factory:approve <phase>`, `/factory:status`, `/factory:init`.

## Install

```bash
claude plugin marketplace add ./software-factory
claude plugin install factory@software-factory
```
Or push this folder to GitHub, then run `/plugin marketplace add <owner>/<repo>` and `/plugin install factory@software-factory`.

Requirements: Claude Code, `python3`, `git`, and the `gh` CLI authenticated (`gh auth login`). Where builds run, you also need the .NET 10 SDK, Node LTS, and Docker (for Testcontainers).

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

## How the gates work

- `scripts/gate.py` keeps `.factory/state.json`. An approval stores the SHA-256 of the phase's artifact folder.
- Each role skill runs `gate.py check <phase> --soft` through `!` injection before the model sees the prompt. When blocked, it prints `GATE BLOCKED` plus the reason, and the role stops.
- `hooks/protect_state.py` (PreToolUse, applies to subagents too) is the hard enforcement. It refuses:
  - writes into a phase's `docs/factory/0N-*` folder while upstream isn't approved, whether by Write/Edit or by a shell write
  - `gh pr create` while the build phase is closed
  - any edit to the state file
  - any model-initiated `gate.py approve`
- If you edit an approved doc, that phase becomes `changed`. Re-approving it marks every downstream phase `stale`. Both states block the phases that depend on them.
- `/factory:approve` works only for you, because its `!` injection runs outside the tool pipeline and the skill has `disable-model-invocation: true`.
- For team use, add GitHub branch protection plus a CODEOWNERS entry on `docs/factory/` and `.factory/`, so approvals are also reviewed in PRs.

## Test

```bash
python3 plugins/factory/tests/test_gate.py
claude plugin validate ./plugins/factory
```
