# Delivery plan: <Product name>

Based on PRD, ARCHITECTURE, openapi.yaml and UI-SPEC as approved on <dates>.

## 1. Milestones
| Milestone | Goal | Stories | Tasks |
| --- | --- | --- | --- |
| M0 Walking skeletons | Both repos build, test, and deploy to dev on Azure | — | T-00-api, T-00-web |
| M1 | | | |

## 2. Tasks
| ID | Repo | Issue | Title | Size | Estimate (agent · human) | Milestone | Implements (story · AC) | Depends on | Spec refs |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| T-00-api | api | owner/x-api#? | .NET 10 solution, CI, Bicep, contract test | M | 3h · 16h | M0 | — | — | ARCHITECTURE §6, §12 |
| T-00-web | web | owner/x-web#? | Vite React TS app, CI, SWA, MSW, codegen | M | 3h · 16h | M0 | — | — | ARCHITECTURE §7, UI-SPEC §2 |
| T-01 | api | | Implement `createOrder` | S | 1.5h · 6h | M1 | S-01 · AC1–3 | T-00-api | openapi `createOrder` |
| T-02 | web | | Create order form (SCR-02) | M | 2h · 8h | M1 | S-01 · AC1–3 | T-00-web | UI-SPEC SCR-02, openapi `createOrder` |

## 3. Estimates
Agent hours = the factory's developer + QA + fix rounds; human hours = an experienced human developer, for comparison.
Calibration applied: <e.g. "be/feature x1.3 (8 samples), fe/feature x1.1 (5 samples)" or "none yet: uncalibrated">.

| Milestone / story | Tasks | Agent hours | Human-dev hours |
| --- | --- | --- | --- |
| M0 Walking skeletons | 2 | 6h | 32h |
| M1 | | | |
| **Total** | | | |

Progress, deviations and the forecast: `/factory:status` (terminal) or the Grafana dashboard.

## 4. Build order
```mermaid
flowchart LR
  subgraph api
    A0[T-00-api] --> A1[T-01 createOrder]
  end
  subgraph web
    W0[T-00-web] --> W2[T-02 order form]
  end
```
**Wave 1 (parallel):** T-00-api, T-00-web
**Wave 2 (parallel):** T-01, T-02 (web mocks the contract)

## 5. Definition of done (every task)
- [ ] Every claimed AC is covered by an automated test (xUnit / Vitest / Playwright)
- [ ] CI green: build, format or lint, typecheck, tests, and the contract test (api)
- [ ] Conforms to `openapi.yaml` and UI-SPEC
- [ ] PR links its issue (`Closes #N`) and the story, and has passed QA review
- [ ] A human merged it; it is deployed to dev
