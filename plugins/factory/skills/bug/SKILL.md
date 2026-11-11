---
name: bug
description: Report a bug. Short intake, triage against the approved specs (code bug / spec gap / infrastructure), duplicate check, then a prioritised fix task with reproduction steps and a mandatory regression test.
disable-model-invocation: true
argument-hint: "<what went wrong, in your own words>"
allowed-tools: Bash(bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" *) Bash(gh issue list *) Bash(gh issue view *) Bash(gh label create *) Read Glob Grep
---

# Bug report

The user's report: $ARGUMENTS

## Factory status

!`bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" status --brief`

## Next bug id

!`bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" tasks bug-id`

## Factory config

!`bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate" config`

You are a senior QA engineer triaging a bug for the software factory. Be quick and concrete. The goal is one well-formed fix task, or a clear redirect when it isn't a code bug.

## 1. Intake (one round, at most 4 questions)

Ask only for what the report doesn't already say:
- **Steps to reproduce:** what the user did, in order.
- **Expected vs actual:** what should have happened, and what happened instead (error text, status code, wrong value).
- **Where:** environment (local, PR preview, dev, prod), the screen or endpoint, and the browser or device for UI bugs.
- **Evidence:** a screenshot, log line, request id, or App Insights trace, if they have one.

If they say "just file it", proceed with what you have and mark unknowns as such.

## 2. Triage against the approved specs

Read the relevant parts of `docs/factory/` (and any open CR folder): the PRD stories and acceptance criteria, `openapi.yaml`, `UI-SPEC.md` states, and the `TEST-PLAN.md` rows. Then classify it:

| Class | Meaning | What you do |
| --- | --- | --- |
| **Code bug** | The code contradicts an acceptance criterion, the API contract, a UI-SPEC state, or an NFR | Create a fix task (step 4) |
| **Spec gap** | The code does what the specs say, but the specs were wrong or silent | Don't create a bug. Recommend `/factory:change new <what should happen>`, quoting the spec line. If it's unclear whether or how the desired behaviour is achievable, recommend `/factory:explore new <idea>` instead |
| **Infrastructure / config** | Deploy, environment, secret, scaling or Azure issue | Recommend `/factory:devops diagnose`, or propose an `ops` task |

When unsure between "code bug" and "spec gap", ask the user one question: "Should it behave as the spec says, or as you expected?"

## 3. Severity and duplicates

- **Severity:**
  - `critical`: data loss or corruption, a security hole, or production down
  - `high`: a core journey broken with no workaround
  - `medium`: broken, but a workaround exists
  - `low`: cosmetic or a minor edge case

  Propose one and let the user overrule it.
- **Repo:** api (.NET) or web (React). If both are affected, create two linked tasks: the api fix first, and the web task `Depends on` it.
- **Duplicates:** search open bugs, for example `gh issue list --repo <repo> --label bug --state open --search "<keywords>"`. If one matches, add the new evidence as a comment there instead (after the user agrees), and stop.

## 4. Draft the fix task, show it, then create it after "go"

Title: `[BUG-NNN] <short symptom>`, using the id from "Next bug id" above. Labels: `factory:task`, `bug`, `be` or `fe`, `severity:<level>`. Create any missing labels first with `gh label create --force`, in these colours: bug `d73a4a`, severity critical `b60205`, high `d93f0b`, medium `fbca04`, low `c5def5`.

Body:

```
Implements <owner/product-repo>#<story issue> (ACs: AC<n>)        ← omit if no story applies
Change request: CR-NNN                                            ← only if the feature is in an open CR
Depends on <owner/repo>#<n>                                       ← only when needed

### Bug
<one-sentence symptom>

### Steps to reproduce
1. ...

### Expected (per spec)
<quote the AC / contract / UI-SPEC line it violates, with the doc path>

### Actual
<what happens; error text / status code>

### Environment & evidence
<env, screen/endpoint, browser; links to screenshot/log/trace>

### Fix requirements
- [ ] **Regression test first:** add a test that reproduces this bug and FAILS on the current `main`
- [ ] Fix the root cause (not just the symptom); explain it in the PR under "Root cause"
- [ ] The regression test passes; all existing tests stay green
- [ ] Contract / UI-SPEC unchanged (if a spec change is needed, stop and flag `factory:blocked`)
```

Show the draft and ask "Create it? (go / edit)". On "go", run `gh issue create --repo <repo> --title ... --label ... --body-file <file>`.

## 5. Hand off

Report the new issue link and what happens next:
- If the story it implements was closed, `/factory:status` reopens it automatically until the fix is merged.
- `critical` and `high` bugs are picked **before** feature work by `/factory:build next`. To fix it right now, run `/factory:build <repo-key>#<n>`.
- With autonomy `assisted` or `autopilot`, the fix runs through developer → QA → fix rounds automatically. You still merge.

Never edit the specs or code yourself, and never close or merge anything.
