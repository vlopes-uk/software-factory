# Code map: <Product> <api | web>

Agents read this first to find their way around. Keep it short (under about 80 lines). Update it in the same PR whenever you add or move a feature, a project or a key file.

## Layout
| Path | What lives there |
| --- | --- |
| `src/<Product>.Api/Features/` | one folder per feature slice (endpoints, requests, handlers, validators) |
| `src/<Product>.Domain/` | entities and domain rules |
| `src/<Product>.Infrastructure/` | EF Core DbContext, migrations, external clients |
| `tests/<Product>.UnitTests/` | xUnit + Moq, mirrors `src/` folders |
| `tests/<Product>.IntegrationTests/` | WebApplicationFactory + Testcontainers, contract conformance test |
<!-- web example: src/features/<feature>/ · src/components/ · src/api/ (generated types + client) · src/mocks/ (MSW) · e2e/ -->

## Features
<!-- One row per feature, kept in alphabetical order. Add or edit only your own row; never reflow the table. (.gitattributes: CODEMAP.md merge=union) -->
| Feature | Folder | operationIds / screens | Notes |
| --- | --- | --- | --- |
| Orders | `src/<Product>.Api/Features/Orders/` | createOrder, listOrders | uses `OrderValidator` |

## Key files
| File | Why it matters |
| --- | --- |
| `Program.cs` | DI, middleware; discovers feature endpoints and modules by assembly scan (no per-feature edits) |
| `contracts/openapi.yaml` | vendored contract; changed only by sync-contract tasks |

## Conventions worth knowing
- <one line each; link to CLAUDE.md for the rest>
