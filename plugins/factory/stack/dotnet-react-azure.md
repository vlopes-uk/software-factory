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
- **Web** never hand-writes API types. `npm run gen:api` runs `openapi-typescript` on the vendored `contracts/openapi.yaml` and writes `src/api/schema.d.ts`. That file is **generated, gitignored and never committed**; `predev`, `pretypecheck`, `pretest` and `prebuild` scripts regenerate it, so it can never conflict. It uses `openapi-fetch` as the typed client. Before the backend lands, the web app mocks the contract with MSW.
- **Vendored contract copies** (`contracts/openapi.yaml` in the api and web repos) change **only in a dedicated sync-contract task** (`npm run contract:sync` in web, a plain copy in api), which the planner creates whenever the contract changes. Feature tasks depend on it and never touch `contracts/`.
- Changing the contract means changing the architecture phase, which goes through re-approval.

## Backend (`api` repo)

- **.NET 10 (LTS)**, C# latest, `<Nullable>enable</Nullable>`, `<TreatWarningsAsErrors>true</TreatWarningsAsErrors>`, file-scoped namespaces.
- ASP.NET Core Web API. The Architect chooses minimal APIs or controllers in an ADR; the default is minimal APIs with endpoint groups per feature.
- **Features self-register, so parallel tasks never edit the same central file.** Each feature folder has a `<Feature>Endpoints : IEndpointGroup` (and, if needed, a `<Feature>Module : IServiceModule` for DI). `Program.cs` calls `builder.Services.AddFeatureModules()` and `app.MapEndpointGroups()`, which discover them by assembly scanning; validators come from `AddValidatorsFromAssemblyContaining<Program>()` and EF configurations from `ApplyConfigurationsFromAssembly`. A feature task never edits `Program.cs`.
- The default layout is vertical slices:
  ```
  src/<Product>.Api/            Program.cs (no per-feature lines), Features/<Feature>/{Endpoints,Module,Requests,Handlers,Validators}.cs
  src/<Product>.Domain/         entities, value objects, domain rules (no framework deps)
  src/<Product>.Infrastructure/ EF Core DbContext, migrations, external clients
  tests/<Product>.UnitTests/
  tests/<Product>.IntegrationTests/
  ```
  The Architect may switch to Clean Architecture via an ADR.
- Validation uses FluentValidation. Errors are RFC 9457 `ProblemDetails`, with validation errors as `ValidationProblemDetails`.
- Persistence is **EF Core** with code-first migrations; the provider is decided per project. Migrations are committed. Never use `EnsureCreated` outside tests. On a migration conflict (the model snapshot), take main's migrations and snapshot, delete your own migration, and re-run `dotnet ef migrations add`; never hand-merge the snapshot.
- Configuration is `IOptions<T>` bound from appsettings or the environment. Secrets live in **Azure Key Vault** and are read through managed identity. Locally they come from `dotnet user-secrets`. Never commit secrets.
- Logging uses `ILogger<T>` with structured logs, exported to Application Insights through OpenTelemetry.
- Health checks are at `/health/live` and `/health/ready`.
- **Tests: xUnit + Moq**, using xUnit `Assert` (add `Shouldly` only if an ADR says so; avoid FluentAssertions v8+, which has a commercial licence):
  - unit tests: one test class per class under test; **mocks are created in the test class constructor** and stored in readonly fields; naming `Method_Scenario_ExpectedResult`; Arrange/Act/Assert
  - integration tests: `WebApplicationFactory<Program>` plus Testcontainers for the real database engine; no in-memory provider for relational behaviour
  - the contract conformance test described above
- Commands (agents use the quiet forms): `dotnet restore -v q`, `dotnet build -warnaserror -v q -clp:ErrorsOnly`, `dotnet format --verify-no-changes -v q`, `dotnet test --nologo -v q --logger "console;verbosity=minimal"` (CI adds `--collect:"XPlat Code Coverage"`).

## Frontend (`web` repo)

- **React 19 + TypeScript (strict) + Vite**. Package manager is npm (lockfile committed). Node is the current LTS.
- Routing uses React Router. Server state uses TanStack Query wrapping the `openapi-fetch` client. Local state stays in React state or context; add a store only via an ADR.
- Styling: the design tokens from UI-SPEC become CSS custom properties in `src/styles/tokens.css`. The component approach (CSS Modules by default) is decided in the UI spec. `tokens.css` is edited only by `T-00-web` and design-change tasks; feature tasks use existing tokens and report a missing one instead of adding it.
- **Features self-register.** Each feature exports its routes from `src/features/<feature>/routes.tsx` and its MSW handlers from `src/features/<feature>/mocks.ts`. `src/app/router.tsx` and `src/mocks/handlers.ts` collect them with `import.meta.glob(..., { eager: true })`, so a feature task never edits a central routes or handlers list.
- Forms use React Hook Form with Zod schemas.
- Layout:
  ```
  src/api/        schema.d.ts (generated, gitignored), client.ts, queries/
  src/app/        router.tsx (globs features/*/routes.tsx), providers
  src/components/ shared UI components (+ *.test.tsx)
  src/features/<feature>/  routes.tsx, mocks.ts, screens, hooks, components (+ tests)
  src/mocks/      handlers.ts (globs features/*/mocks.ts), browser/server setup
  e2e/            Playwright specs
  ```
- **Tests:** Vitest with React Testing Library and `@testing-library/user-event` for components; MSW for network; **Playwright** for end-to-end, including an `@axe-core/playwright` accessibility check per screen.
- Commands (agents use the quiet forms): `npm ci --no-audit --no-fund --loglevel=error`, `npm run lint -- --quiet` (ESLint), `npm run typecheck` (`tsc --noEmit`), `npm test -- --run --reporter=dot`, `npm run build --silent`, `npx playwright test --reporter=line`.

## Azure and delivery

- **Web:** Azure Static Web Apps, with a PR preview environment for every pull request.
- **API:** Azure App Service (Linux) or Azure Container Apps, chosen in an ADR. The default is App Service for a single API and Container Apps when there are multiple services or background workers.
- **IaC: Bicep** in each code repo's `infra/`. Environments are `dev` and `prod`, with `staging` optional.
- **CI/CD: GitHub Actions.** Azure login uses **OIDC federated credentials** (`azure/login`), with no stored secrets. The CI templates are `${CLAUDE_PLUGIN_ROOT}/templates/ci/`.
- Production deploys: publishing a GitHub release triggers each repo's `deploy-prod` job, which waits for the human's approval on the `prod` environment (`/factory:devops release`). The operations design lives in `OPERATIONS.md`, next to ARCHITECTURE.md.
- Required PR checks: build, format or lint, typecheck, tests, and the contract test (api). Branch protection on `main` requires PRs, 1 review and these checks, with **"require branches to be up to date" off** (or a merge queue, where available), so a PR that is merely behind `main` never needs a rebase.
- **Conflict-free repo hygiene:** a code-repo `.gitattributes` with `CODEMAP.md merge=union` (rows are one per feature, alphabetical), `package-lock.json` and `*ModelSnapshot.cs` marked `-diff` for review, and `src/api/schema.d.ts` in `.gitignore`.
- Observability: Application Insights for both; the SPA uses the JS SDK and the connection string from config.

## Decided per project (ADR required)

Database (Azure SQL, Azure Database for PostgreSQL, Cosmos DB…) · API hosting (App Service vs Container Apps) · auth (Microsoft Entra ID for workforce, Entra External ID for customers, or other) · minimal APIs vs controllers · background processing (hosted service, Azure Functions, Service Bus) · file storage · caching · component styling approach.
