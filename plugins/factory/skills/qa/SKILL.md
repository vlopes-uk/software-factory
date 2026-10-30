---
name: qa
description: QA. `plan` writes the test plan from approved specs; `review <api#PR|web#PR>` runs the QA reviewer subagent on a developer PR.
disable-model-invocation: true
argument-hint: "plan | review <api#PR | web#PR>"
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/gate.py *) Bash(gh issue *) Bash(gh pr view *) Read Write Edit Glob Grep
---

# Role: QA

Mode requested: **$0** (argument: $1)

## Gate status

!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/gate.py status`

## Factory config

!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/gate.py config`

The gate rules depend on the mode:

- `plan` requires **planning** = `approved`.
- `review` requires **build** = `ready`.

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
4. Point out ACs that are ambiguous or untestable, and send them back via `/factory:po revise ...`.
5. Hand off: "Review `TEST-PLAN.md`, then run `/factory:approve test-plan`. The build phase then opens: `/factory:build api#<n>`."

## Mode `review`: review PR $1

Resolve `$1` with the config: `api#31` means a PR in `repos.api.github`, and `web#31` means one in `repos.web.github`. Then delegate to the **qa-reviewer** subagent (Agent tool, `subagent_type: "factory:qa-reviewer"`) with:

> Review pull request `<owner/repo>#<n>`. Local clone: `<absolute clone path>`. Product repo with the specs: `<absolute path of the current directory>`. Stack profile: `${CLAUDE_PLUGIN_ROOT}/stack/dotnet-react-azure.md`. Check it against its linked task issue, the acceptance criteria, `openapi.yaml` and TEST-PLAN.md, and post your review on the PR.

When it returns, give the user its verdict and the key findings in under 10 lines. Remind them that **merging is their gate**. If changes were requested, suggest `/factory:build <repo-key>#<issue> fix`.
