# EXP-NNN: <Idea>, PoC brief

| Field | Value |
| --- | --- |
| Chosen approach | <from FINDINGS.md> |
| Timebox | <e.g. "one developer run per repo, max 2 iterations"> |
| Repos | api / web / both |
| Branches | `spike/EXP-NNN-<slug>` in each repo used |

## 1. What to prove
<Copied from FINDINGS §6, each with how we will know it worked.>
| # | Claim | Proven when |
| --- | --- | --- |
| P1 | | <a visible, checkable result in the demo> |

## 2. Scope
**Build (thinnest slice that proves the claims):**
-

**Fake or skip (say how):**
- <e.g. auth: hard-coded user; payments: sandbox keys; data: seeded in memory>

## 3. Per repo
### api (`be-dev`, spike mode)
- <endpoints, background jobs, packages to try>

### web (`fe-dev`, spike mode)
- <screens, libraries to try; calls the local api at http://localhost:<port> or MSW>

## 4. Allowed shortcuts
No tests or QA required. New packages allowed: <list>. Contract additions allowed (listed in the report).

## 5. Demo the human will run
1. <command or click>
2. <what they should see>
