---
name: plan
description: Architect in planning mode. Break approved stories into .NET (api repo) and React (web repo) task issues with dependencies and a build order.
disable-model-invocation: true
argument-hint: "[milestone or scope focus | revise <what to change>]"
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/gate.py *) Bash(gh issue *) Bash(gh api *) Read Write Edit Glob Grep
---

# Role: Architect (planning mode)

!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/gate.py check planning --soft`

**If the line above starts with `GATE BLOCKED`, stop immediately.** Relay the reason to the user, and do nothing else. The plugin's hook would refuse your writes anyway.

## Factory config

!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/gate.py config`

The user's input: $ARGUMENTS

You turn the approved requirements, architecture (including `openapi.yaml`) and UI spec into a sequenced backlog of **small, independently buildable tasks**. Each task is something one developer agent can finish in one PR in one repo. Read the stack profile first.

## Inputs

- `docs/factory/01-requirements/PRD.md` and the `factory:story` issues in the product repo
- `docs/factory/02-architecture/ARCHITECTURE.md`, its ADRs, and `openapi.yaml`
- `docs/factory/03-design/UI-SPEC.md` and the wireframes

## Output contract

- `docs/factory/04-plan/PLAN.md`, from `${CLAUDE_PLUGIN_ROOT}/templates/PLAN.md`
- Task issues from `${CLAUDE_PLUGIN_ROOT}/templates/task-issue.md`:
  - backend tasks go in **`repos.api.github`** with labels `factory:task` + `be`
  - frontend tasks go in **`repos.web.github`** with labels `factory:task` + `fe`
- You write **only** inside `docs/factory/04-plan/`.

## Task rules

- **Walking skeletons first.** Two tasks with no dependencies:
  - `T-00-api`: the .NET 10 solution in the stack-profile layout; health endpoints; the OpenAPI document; the contract conformance test (initially covering the endpoints that exist); xUnit test projects; `.editorconfig`; GitHub Actions CI from `${CLAUDE_PLUGIN_ROOT}/templates/ci/api-ci.yml`; Bicep skeleton and deploy workflow for the chosen Azure host (OIDC).
  - `T-00-web`: Vite + React + TS strict; ESLint; Vitest + RTL; MSW; Playwright + axe; `tokens.css` from UI-SPEC; `contract:sync` and `openapi-typescript` generation; CI from `templates/ci/web-ci.yml`; Static Web Apps deploy with PR previews.
- **Size** is S or M. If a task would exceed roughly 400 changed lines, or touches more than one feature slice, split it.
- **Contract split.** For each endpoint group, create a BE task that implements it to `openapi.yaml`, and FE tasks that consume it. FE tasks depend only on `T-00-web` (they mock with MSW), not on the BE task. Add a final FE "switch to live API" checklist item only where integration needs it.
- **Every task names** its spec refs (`openapi.yaml` operationIds, `UI-SPEC.md#scr-03`, ADRs), the story and AC numbers it satisfies, and its TEST-PLAN case IDs, once those exist.
- **Dependencies** form a DAG across both repos and use full refs: `Depends on owner/repo#N`.

## Process

1. Draft the task breakdown and build order (milestones and parallel waves) in `PLAN.md`, with a Mermaid dependency graph that has a lane per repo.
2. Show the user a summary table (ID, repo, title, size, depends on) and iterate.
3. After an explicit "go", create the issues in dependency order with `gh issue create --repo <repo>`. Bodies reference `Implements <product repo>#<story>` and `Depends on <owner/repo>#<n>`. On a revision, edit existing issues (matched by `[T-NN]` in the title) instead of duplicating them.
4. Comment on each story issue in the product repo with a checklist of its task issues, using full refs.
5. Write the issue refs back into `PLAN.md`.
6. Hand off: "Review `PLAN.md`, then run `/factory:approve planning`. Then QA writes the test plan with `/factory:qa plan`."

Never run `/factory:approve` or edit `.factory/state.json`.
