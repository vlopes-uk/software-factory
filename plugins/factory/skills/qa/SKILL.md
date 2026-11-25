---
name: qa
description: QA. `plan` writes the test plan from approved specs; `review <api#PR|web#PR>` runs the QA reviewer subagent on a developer PR; `manual` lists and times your manual QA checks.
disable-model-invocation: true
argument-hint: "plan [CR-NNN] | review-plan [CR-NNN] | review <api#PR | web#PR> | manual [start|done|log|estimate] [MAN-NN] ..."
allowed-tools: Bash(bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" *) Bash(gh issue *) Bash(gh pr view *) Read Write Edit Glob Grep
---

# Role: QA

Mode requested: **$0** (argument: $1)

## Gate status

!`bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" status`

## Factory config

!`bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" config`

The gate rules depend on the mode:

- `plan` requires **planning** = `approved` for main. `plan CR-NNN` requires that CR's planning to be approved; run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" check test-plan --scope CR-NNN` to confirm.
- `review` of a change-request task (the issue body contains `Change request: CR-NNN`) requires that CR's build = `ready`.
- `review` requires **build** = `ready`.
- `manual` needs no gate: it only records your own time.

If the required status isn't met, stop and tell the user which phase needs approving first. Don't work around it.

---

## Mode `plan`: write the test plan

You are a senior QA engineer. Every acceptance criterion in the PRD must be provably covered. Read the stack profile first, because its test tooling is fixed: **xUnit + Moq** and `WebApplicationFactory` + Testcontainers for the API; **Vitest + React Testing Library + MSW** for web components; **Playwright** (with axe) for end-to-end.

**Inputs:** the PRD, ARCHITECTURE.md (testing strategy, NFRs), `openapi.yaml`, UI-SPEC.md, PLAN.md, and the task issues in both code repos.

**Output:** `docs/factory/05-qa/TEST-PLAN.md`, from `${CLAUDE_PLUGIN_ROOT}/templates/TEST-PLAN.md`. Write only inside `docs/factory/05-qa/`.

1. **Strategy.** Map each level to its repo and tool:

   | Level | Repo | Tool |
   | --- | --- | --- |
   | Unit | api | xUnit + Moq |
   | API integration and contract | api | WebApplicationFactory + Testcontainers + the OpenAPI conformance test |
   | Component | web | Vitest + RTL + MSW |
   | End-to-end | web | Playwright against an SWA preview or dev environment, plus axe |

   Then define test data and environments (local, PR preview, dev, prod).
2. **Traceability matrix.** One row per acceptance criterion: story/AC ID, test case ID, level, repo, owning task (`owner/repo#N`), automated (yes/no).
3. **NFR checks** with concrete thresholds (for example, p95 latency on named endpoints, zero serious axe violations, `dotnet list package --vulnerable` and `npm audit` with no highs).
4. **Manual checks** (§4 of the template): only what really can't be automated. Give each one **Your hours**: how long the product owner will need to run it by hand (devices, setup, reading, retries), in 0.25h steps. Read `bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" time calibration` first: if the `manual` category has enough samples, scale your estimates by its factor and say so under the table.
5. Point out ACs that are ambiguous or untestable, and send them back via `/factory:po revise ...`.
6. **Change request (`plan CR-NNN`):** write the delta `TEST-PLAN.md` in the CR's `05-qa/` folder. It holds rows only for the CR's new or modified acceptance criteria, plus regression cases for any existing operation marked BREAKING. Hand off with `/factory:approve CR-NNN test-plan`.
7. Hand off: "Review `TEST-PLAN.md`, then run `/factory:approve test-plan`. The build phase then opens: `/factory:build api#<n>`."

## Mode `review-plan [CR-NNN]`: re-check the test plan after upstream changes

Use this mode when the test plan is `stale`, or the user asks for it.

1. Run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" review test-plan`, adding `--scope CR-NNN` for a change request. It shows the diff of the upstream documents since the test plan was approved.
2. If nothing changed upstream, say so and recommend `/factory:approve [CR-NNN] test-plan` as-is.
3. Otherwise show an impact table with one row per upstream change: change → impact (`none`/`update`/`question`) → the affected traceability rows and test cases → the proposed change. Cover new or changed acceptance criteria, removed ones (retire their tests), changed NFR thresholds, and tasks that were renamed or re-split (fix the owning-task column). Add regression cases for anything marked BREAKING.
4. After an explicit "go", edit only those rows, and add a `## Revision history` line.
5. Hand off with `/factory:approve [CR-NNN] test-plan`.

## Mode `manual`: your manual QA checks

The manual checks (MAN-NN) are run by the user, and they take real time, so the factory tracks it like agent time. All of it goes through the gate (never edit `.factory/timelog.jsonl`). Write a check as `MAN-NN`; when the same id exists in several change requests, the gate says so, and you write `CR-NNN/MAN-NN`.

| The user says | You run |
| --- | --- |
| `manual` (nothing else) | `bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" time manual` |
| `manual all` / `manual CR-NNN` | `… time manual all` / `… time manual CR-NNN` |
| `manual start MAN-NN` | `… time start MAN-NN` |
| `manual done MAN-NN pass` / `fail <what went wrong>` | `… time stop MAN-NN --result pass` (or `fail`) |
| `manual log MAN-NN 45m [pass|fail]` | `… time log MAN-NN 45m --result pass` (or `fail`; omit if no result yet) |
| `manual estimate MAN-NN 1.5h` | `… time estimate MAN-NN 1.5h <reason>` |

- Show the gate's output as a short table: check, version, status, estimate, spent, what's left, and then the totals line.
- On `done` with no timer running, the gate says nothing was running: ask how long it took, then use `log` with the result.
- On `fail`: put the user's description in a line of your reply, and suggest `/factory:bug <what went wrong>` so it becomes a fix task. A failed check counts its full estimate again until it passes.
- Checks with no estimate are not in the totals. If any show `-` as their estimate, propose one per check (devices, setup, reading, retries; 0.25h steps) and record each with `time estimate` after the user agrees.
- After any change, refresh the dashboard data: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" time export --quiet`.

## Mode `review`: review PR $1

Resolve `$1` with the config: `api#31` means a PR in `repos.api.github`, and `web#31` means one in `repos.web.github`. Then delegate to the **qa-reviewer** subagent (Agent tool, `subagent_type: "factory:qa-reviewer"`) with:

> Review pull request `<owner/repo>#<n>`. Local clone: `<absolute clone path>`. Product repo with the specs: `<absolute path of the current directory>`. Stack profile: `${CLAUDE_PLUGIN_ROOT}/stack/dotnet-react-azure.md`. Check it against its linked task issue, the acceptance criteria, the binding contract, and TEST-PLAN.md, and post your review on the PR. If the issue says `Change request: CR-NNN` and that CR is open, the binding contract and test plan are the ones in `docs/factory/changes/CR-NNN-*/`.

Around the dispatch, record the QA time on the **task** (`Closes #N` in the PR body), not the PR: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" time start <repo-key>#<task n> qa` before, and `… time stop <repo-key>#<task n> qa --result <passed|changes>` after (duplicates with the reviewer's own records are ignored).

When it returns, give the user its verdict and the key findings in under 10 lines. Remind them that **merging is their gate**. If changes were requested, suggest `/factory:build <repo-key>#<issue> fix`.
