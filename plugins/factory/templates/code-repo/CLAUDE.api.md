# <Product> API (.NET 10)

Part of a software factory. Specs live in **<owner>/<product>-product** (`docs/factory/`) and are read-only here.

- Binding API contract: `docs/factory/02-architecture/openapi.yaml` in the product repo (vendored to `contracts/openapi.yaml`). The contract conformance test must stay green.
- Layout: vertical slices in `src/<Product>.Api/Features/<Feature>/`; domain in `src/<Product>.Domain`; EF Core in `src/<Product>.Infrastructure`.
- Errors: RFC 9457 ProblemDetails. Validation: FluentValidation. Config: `IOptions<T>`. Secrets: Key Vault or user-secrets, never committed.
- Tests: xUnit + Moq. Mocks are created in the test class constructor; names follow `Method_Scenario_ExpectedResult`. Integration tests use `WebApplicationFactory<Program>` + Testcontainers.
- Before every commit: `dotnet build -warnaserror && dotnet format --verify-no-changes && dotnet test`.
- Branches are `factory/<issue>-<slug>`; commits are conventional; PRs contain `Closes #<issue>`.
