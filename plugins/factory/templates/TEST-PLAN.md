# Test plan: <Product name>

## 1. Strategy
| Level | Repo | Scope | Tool | Runs in |
| --- | --- | --- | --- | --- |
| Unit | api | domain + handlers | xUnit + Moq | PR CI |
| Integration + contract | api | endpoints vs `openapi.yaml`, real DB | WebApplicationFactory + Testcontainers | PR CI |
| Component | web | UI states in UI-SPEC | Vitest + RTL + MSW | PR CI |
| End-to-end + a11y | web | key journeys from PRD §6 | Playwright + axe | PR (mocked), dev env (live) |

**Test data:**
**Environments:**

## 2. Traceability matrix
| Story · AC | Test case | Level | Repo | Task | Automated |
| --- | --- | --- | --- | --- | --- |
| S-01 · AC1 | TC-001 <name> | Integration | api | owner/x-api#3 | yes |

## 3. Non-functional checks
| NFR | Check | Pass threshold |
| --- | --- | --- |
| Performance | | |
| Accessibility | axe + keyboard pass | 0 serious violations |
| Security | dependency audit, authz tests | 0 high |

## 4. Ambiguous or untestable criteria
- <AC>: <why> → proposed rewording (send to the PO)

## 5. Exit criteria for a release
- All Must ACs pass; no open ❌ in QA reviews; NFR thresholds met.
