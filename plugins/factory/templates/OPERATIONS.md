# Operations: <Product name>

Owner: DevOps (`/factory:devops plan`). Approved together with ARCHITECTURE.md.

## 1. Environments
| Env | Purpose | Web (SWA) | API host | Data | Deploys on | Approval |
| --- | --- | --- | --- | --- | --- | --- |
| preview | per-PR check | SWA PR preview | dev API | dev DB | every PR (web) | none |
| dev | integration | | | | push to `main` | none |
| prod | users | | | | GitHub release published | **human (GitHub `prod` environment)** |

## 2. Azure resources
Region: <region> · Naming: `<type>-<product>-<env>` (for example `app-booking-prod`, `kv-booking-prod`)

| Resource | dev | prod | Notes (SKU, settings) |
| --- | --- | --- | --- |
| Resource group | rg-<p>-dev | rg-<p>-prod | |
| Static Web App | | | |
| API host (App Service / Container Apps per ADR) | | | |
| Database (per ADR) | | | backups, geo-redundancy |
| Key Vault | | | RBAC mode, managed identity access |
| Application Insights + Log Analytics | | | retention, sampling |

## 3. Infrastructure as code
| Repo | `infra/` modules | Parameters per env |
| --- | --- | --- |
| api | main.bicep → apiHost, database, keyVault, appInsights | `env`, SKU |
| web | main.bicep → staticWebApp | `env` |

## 4. CI/CD
| Repo | On PR | On push to main | On release published |
| --- | --- | --- | --- |
| api | build, format, test, contract test | deploy dev (OIDC) | deploy-prod (waits for approval) |
| web | lint, typecheck, test, build, e2e; SWA preview | deploy dev | deploy-prod (waits for approval) |

Release order when the contract changes: **api first, then web**.

## 5. Identity and secrets
- GitHub → Azure: OIDC federated credentials per repo and environment; the service principal has Contributor **on its resource group only**.
- App → Azure: managed identity → Key Vault and the database (no connection-string passwords where the service supports Entra auth).
- GitHub secrets: none, except the SWA deployment token if needed.

## 6. Monitoring and alerts
| Signal | Threshold | Action |
| --- | --- | --- |
| Availability test on `/health/ready` (prod) | 2 failures in 5 min | email <owner> |
| API 5xx rate | > 2% over 10 min | email |
| p95 latency (key endpoints from NFRs) | > <ms> | email |
| Budget | 80% / 100% of <ceiling>/month | email |

## 7. Backups, rollback, recovery
- DB: <point-in-time restore window>; restore drill: <frequency>.
- App rollback: re-run the previous release's `deploy-prod` job. A DB migration that isn't backward compatible needs a written rollback plan in its PR.

## 8. Cost estimate (per month, estimate)
| Item | dev | prod | Assumption |
| --- | --- | --- | --- |
| | | | |
| **Total** | | | |

## 9. Open questions
- [ ]
