# Stack profile: .NET + React on Azure, separate GitHub repos

Every factory role reads this file. These are the **fixed** choices. Only the decisions listed under "Decided per project" get an ADR.

## Repositories (see `.factory/config.json`)

| Key | Holds | Issues there | PRs there |
| --- | --- | --- | --- |
| `product` | `docs/factory/**`, `.factory/**`, `contracts/openapi.yaml` (a copy published from the architecture docs) | epics, stories | spec changes only |
| `api` | the .NET solution, API tests, `infra/` for the API | `be` tasks | backend code |
| `web` | the React app, component and e2e tests, `infra/` for the SPA | `fe` tasks | frontend code |

- Issue references always use the full form, `owner/repo#N`, because they cross repos.
- Local clones sit at the paths given in `config.json` (`api.path`, `web.path`), relative to the product repo. The default is siblings: `../<name>`.

## The contract is the glue

- `docs/factory/02-architecture/openapi.yaml` (OpenAPI 3.1) is the **binding API contract**, written by the Architect and approved with the architecture phase.
- **API** implements it. An integration test loads the app with `WebApplicationFactory`, fetches the generated document (`Microsoft.AspNetCore.OpenApi`, served at `/openapi/v1.json`), and fails if any path, method, status code or schema in the contract is missing or different.
- **Web** never hand-writes API types. It generates them with `openapi-typescript` into `src/api/schema.d.ts` from the contract, which is vendored at `contracts/openapi.yaml` in the web repo and synced from the product repo by `npm run contract:sync`. It uses `openapi-fetch` as the typed client. Before the backend lands, the web app mocks the contract with MSW.
- Changing the contract means changing the architecture phase, which goes through re-approval.

## Backend (`api` repo)

- **.NET 10 (LTS)**, C# latest, `<Nullable>enable</Nullable>`, `<TreatWarningsAsErrors>true</TreatWarningsAsErrors>`, file-scoped namespaces.
- ASP.NET Core Web API. The Architect chooses minimal APIs or controllers in an ADR; the default is minimal APIs with endpoint groups per feature.
- The default layout is vertical slices:
  ```
  src/<Product>.Api/            Program.cs, Features/<Feature>/{Endpoints,Requests,Handlers,Validators}.cs
  src/<Product>.Domain/         entities, value objects, domain rules (no framework deps)
  src/<Product>.Infrastructure/ EF Core DbContext, migrations, external clients
  tests/<Product>.UnitTests/
  tests/<Product>.IntegrationTests/
  ```
  The Architect may switch to Clean Architecture via an ADR.
- Validation uses FluentValidation. Errors are RFC 9457 `ProblemDetails`, with validation errors as `ValidationProblemDetails`.
- Persistence is **EF Core** with code-first migrations; the provider is decided per project. Migrations are committed. Never use `EnsureCreated` outside tests.
- Configuration is `IOptions<T>` bound from appsettings or the environment. Secrets live in **Azure Key Vault** and are read through managed identity. Locally they come from `dotnet user-secrets`. Never commit secrets.
- Logging uses `ILogger<T>` with structured logs, exported to Application Insights through OpenTelemetry.
- Health checks are at `/health/live` and `/health/ready`.
- **Tests: xUnit + Moq**, using xUnit `Assert` (add `Shouldly` only if an ADR says so; avoid FluentAssertions v8+, which has a commercial licence):
  - unit tests: one test class per class under test; **mocks are created in the test class constructor** and stored in readonly fields; naming `Method_Scenario_ExpectedResult`; Arrange/Act/Assert
  - integration tests: `WebApplicationFactory<Program>` plus Testcontainers for the real database engine; no in-memory provider for relational behaviour
  - the contract conformance test described above
- Commands: `dotnet restore`, `dotnet build -warnaserror`, `dotnet format --verify-no-changes`, `dotnet test --collect:"XPlat Code Coverage"`.

## Frontend (`web` repo)

- **React 19 + TypeScript (strict) + Vite**. Package manager is npm (lockfile committed). Node is the current LTS.
- Routing uses React Router. Server state uses TanStack Query wrapping the `openapi-fetch` client. Local state stays in React state or context; add a store only via an ADR.
- Styling: the design tokens from UI-SPEC become CSS custom properties in `src/styles/tokens.css`. The component approach (CSS Modules by default) is decided in the UI spec.
- Forms use React Hook Form with Zod schemas.
- Layout:
  ```
  src/api/        schema.d.ts (generated), client.ts, queries/
  src/components/ shared UI components (+ *.test.tsx)
  src/features/<feature>/  screens, hooks, components (+ tests)
  src/mocks/      MSW handlers from the contract
  e2e/            Playwright specs
  ```
- **Tests:** Vitest with React Testing Library and `@testing-library/user-event` for components; MSW for network; **Playwright** for end-to-end, including an `@axe-core/playwright` accessibility check per screen.
- Commands: `npm ci`, `npm run lint` (ESLint), `npm run typecheck` (`tsc --noEmit`), `npm test -- --run`, `npm run build`, `npx playwright test`.

## Azure and delivery

- **Web:** Azure Static Web Apps, with a PR preview environment for every pull request.
- **API:** Azure App Service (Linux) or Azure Container Apps, chosen in an ADR. The default is App Service for a single API and Container Apps when there are multiple services or background workers.
- **IaC: Bicep** in each code repo's `infra/`. Environments are `dev` and `prod`, with `staging` optional.
- **CI/CD: GitHub Actions.** Azure login uses **OIDC federated credentials** (`azure/login`), with no stored secrets. The CI templates are `${CLAUDE_PLUGIN_ROOT}/templates/ci/`.
- Required PR checks: build, format or lint, typecheck, tests, and the contract test (api). Branch protection on `main` requires PRs and 1 review.
- Observability: Application Insights for both; the SPA uses the JS SDK and the connection string from config.

## Decided per project (ADR required)

Database (Azure SQL, Azure Database for PostgreSQL, Cosmos DB…) · API hosting (App Service vs Container Apps) · auth (Microsoft Entra ID for workforce, Entra External ID for customers, or other) · minimal APIs vs controllers · background processing (hosted service, Azure Functions, Service Bus) · file storage · caching · component styling approach.
