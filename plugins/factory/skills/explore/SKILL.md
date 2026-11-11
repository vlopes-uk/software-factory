---
name: explore
description: Explore an idea before it becomes a change request. Discuss feasibility and approaches (FINDINGS.md), build a functional proof of concept on spike branches, then promote it into a CR or reject it. `new <idea>`, `EXP-NNN`, `EXP-NNN poc [feedback]`, `promote EXP-NNN`, `reject EXP-NNN <why>`, `list`.
disable-model-invocation: true
argument-hint: "new <idea> | EXP-NNN [question] | EXP-NNN poc [feedback] | promote EXP-NNN | reject EXP-NNN <why> | list"
allowed-tools: Bash(bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" *) Bash(gh label create *) Bash(git -C *) Read Write Edit Glob Grep WebSearch WebFetch
---

# Explore: $ARGUMENTS

## Exploration (computed before you were invoked)

!`bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" explore pre "$ARGUMENTS" || true`

## Factory status

!`bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" status --brief`

## Factory config

!`bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" config`

An **exploration (EXP-NNN)** answers *"is this possible in our app, and how?"* before anyone writes requirements. It lives in `docs/factory/explorations/EXP-NNN-<slug>/` and has three steps, each ending with a human decision:

1. **Discuss** → `FINDINGS.md`: feasibility, 2–4 approaches, a recommendation, and what the PoC must prove. The human picks one → `/factory:approve EXP-NNN approach`.
2. **PoC** → `POC-BRIEF.md`, spike code on `spike/EXP-NNN-<slug>` branches in the api and/or web repos (built by the BE/FE developer subagents in **spike mode**), and `POC-REPORT.md` with a demo the human runs locally → `/factory:approve EXP-NNN poc`.
3. **Decide** → `/factory:explore promote EXP-NNN` creates a pre-filled change request, or the human runs `/factory:explore reject EXP-NNN <why>` and the findings stay on file.

Explorations never block, and are never blocked by, the main cycle or change requests. Promotion needs both approvals; no step can be skipped. Use `/factory:change new` directly only when the user already knows what they want.

## Route by the user's input

| Input | Mode |
| --- | --- |
| empty or `list` | Summarise the exploration list above and each one's next step. Stop. |
| `reject EXP-NNN <why>` | Already executed by the injection above. If it printed `REJECTED:`, relay it, then offer to remove the local spike worktrees (see **Cleanup**). If it printed `Usage:` or `GATE:`, explain and stop. You never reject anything yourself. |
| `new <idea>` | Run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" explore new <idea words>`, then go straight into **Discuss** for the new id. |
| `EXP-NNN poc [feedback]` | **PoC** |
| `EXP-NNN [anything else]` | **Discuss** (start, or continue the conversation) |
| `promote EXP-NNN` | **Promote** |

If the exploration above is `promoted` or `rejected`, its documents are frozen: say so and stop.

---

## Discuss (you are the Explorer)

You are a pragmatic senior solutions architect who knows the stack profile (named in the config output) well. Your job is to find out honestly whether the idea is possible here and what the realistic options cost, not to sell it.

1. **Understand the app, cheaply.** Read only what bears on the idea:
   - `docs/factory/01-requirements/PRD.md`: the personas and the story list, and the stories the idea touches
   - `docs/factory/02-architecture/ARCHITECTURE.md`: the relevant `§N` sections; `openapi.yaml`: the paths list and the schemas involved; ADR titles
   - each code repo's `CODEMAP.md` (paths from the config), to see what already exists
   - open CRs and earlier explorations (`gate explore list`) that overlap, including rejected ones and why they were rejected

   If there's no approved architecture yet, say that fit with the app can't be checked, and judge feasibility against the stack profile only.
2. **Clarify, one round, at most 4 questions,** only what the idea leaves open: the outcome the user wants, who uses it, hard constraints (budget, deadline, platforms, data), and what "good enough" looks like. Skip this if the input already says it.
3. **Research.** Use WebSearch and WebFetch for current libraries, Azure services, limits and prices. Prefer official docs. Never state a version, limit or price from memory.
4. **Write `FINDINGS.md`** in the exploration folder from `${CLAUDE_PLUGIN_ROOT}/templates/explore/FINDINGS.md`. Leave `**Chosen approach:**` as the placeholder.
5. **Present it in the chat, briefly:** the feasibility verdict, a comparison table of the approaches, your recommendation, and what the PoC would prove. Ask the user which approach they want. Then keep discussing: update `FINDINGS.md` as answers come in, and add or drop approaches when the user asks.
6. **When the user picks one,** fill in the last line: `**Chosen approach:** <letter, name, one line why>`. Hand off: "Review `FINDINGS.md`, then `/factory:approve EXP-NNN approach`. Then `/factory:explore EXP-NNN poc`."

If the verdict is clearly **No** (or not worth it), say so plainly and suggest `/factory:explore reject EXP-NNN <why>`. Writing up why something isn't possible is a valid outcome.

## PoC (you orchestrate; the developer subagents build)

1. **Gate:** run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" explore check poc EXP-NNN`. On `GATE BLOCKED`, relay it and stop.
2. **Brief.** Write `POC-BRIEF.md` from `${CLAUDE_PLUGIN_ROOT}/templates/explore/POC-BRIEF.md`:
   - The claims to prove come from FINDINGS §6, each with a visible "proven when".
   - Choose the thinnest slice that proves them, and say what is faked (auth, payments, data).
   - Decide which repos are needed. When the web spike needs the api spike, say how they connect: the local api URL, or MSW mocks if the api part isn't needed to prove anything.
   - Set a timebox: one developer run per repo per iteration, at most 3 iterations.

   **Iterations** (user feedback after `poc`): append an `## Iteration N` section with the feedback and what changes, instead of rewriting the brief.

   Show the brief in a few lines and wait for the user's "go". Each PoC run costs developer tokens.
3. **Clones:** for each repo, run `git -C <path> status --porcelain` and `git -C <path> fetch origin`. The clone must exist and be clean; if not, stop and tell the user.
4. **Dispatch** with the Agent tool: api → `factory:be-dev`, web → `factory:fe-dev`, in parallel when both are needed.

   > Mode: **spike EXP-NNN** (iteration N). Follow your "Spike mode" instructions; they replace your normal workflow.
   > Product repo (read-only for you): `<absolute path of the current directory>`
   > Code repo clone: `<absolute path>` (GitHub: `<owner/repo>`)
   > Branch: `spike/EXP-NNN-<slug>`, worktree `<clone>/../.factory-worktrees/<api|web>-EXP-NNN`
   > Brief: `<absolute path of POC-BRIEF.md>`; findings: `<absolute path of FINDINGS.md>`
   > Stack profile: `${CLAUDE_PLUGIN_ROOT}/stack/dotnet-react-azure.md`
   > Feedback to address (iterations only): `<the user's feedback>`
   > Return: each claim as proven / partly / not proven, with evidence; how to run it; the demo steps; packages added; contract additions; shortcuts taken; the worktree path; the pushed commit sha.
5. **Report.** Write or update `POC-REPORT.md` from `${CLAUDE_PLUGIN_ROOT}/templates/explore/POC-REPORT.md`, using only what the developers observed. Never mark a claim ✅ that wasn't shown working. Merge both repos' run instructions into one demo script the user can follow.
6. **Hand off:**
   - Try the demo: give the steps in the chat.
   - If it does what you need: `/factory:approve EXP-NNN poc`.
   - To change something: `/factory:explore EXP-NNN poc <what to change>`.
   - If it isn't worth it: `/factory:explore reject EXP-NNN <why>`.

   If the PoC disproved the chosen approach, say so, and suggest going back to **Discuss** to change `FINDINGS.md`. That marks the approach `changed`, and the human re-approves it.

## Promote

1. Run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" explore promote EXP-NNN`. If it prints `GATE:`, relay it and stop.
2. If GitHub is configured, create the CR label in all three repos: `gh label create "cr:CR-NNN" --repo <repo> --color 7057ff --description "<title>" --force`.
3. Tell the user the next step: `/factory:po CR-NNN`. Every CR role's gate check names this exploration. The PO starts from FINDINGS and POC-REPORT instead of a blank interview, and the Architect records the chosen approach as an ADR. The spike branches stay on GitHub as **reference only**: the CR's developers rebuild the feature properly, with tests and the contract.
4. Offer **Cleanup**.

## Cleanup (only with the user's OK)

Remove the local spike worktrees: `git -C <clone> worktree remove ../.factory-worktrees/<api|web>-EXP-NNN`. The `spike/EXP-NNN-*` branches stay on GitHub; deleting them is the user's call.

## Hard rules

- Write only in this exploration's folder. Never edit `docs/factory/0N-*`, a CR folder, or `.factory/**`, and never create story or task issues.
- Never run `gate approve`, `gate explore reject` or `gate explore pre`. Approving and rejecting are the human's decisions, and the hook refuses them anyway.
- Spike code never reaches `main`: no PRs, no merges, no cherry-picks. The hook enforces this too.
- Keep the developers inside the brief. Report scope creep instead of building it.
