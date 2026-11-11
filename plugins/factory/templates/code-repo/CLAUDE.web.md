# <Product> Web (React 19 + TypeScript)

Part of a software factory. Specs live in **<owner>/<product>-product** (`docs/factory/`) and are read-only here.

- API types are generated, never hand-written or committed: `npm run gen:api` (run automatically by the `pre*` scripts) writes the gitignored `src/api/schema.d.ts` from `contracts/openapi.yaml`. Only a sync-contract task changes `contracts/` (`npm run contract:sync`). Calls go through the `openapi-fetch` client plus TanStack Query.
- Design: tokens from UI-SPEC live in `src/styles/tokens.css`. Use CSS variables only, and implement every state in the spec (empty, loading, error, disabled, focus). Feature tasks don't edit `tokens.css`.
- Structure: `src/features/<feature>/` with its own `routes.tsx` and `mocks.ts` (picked up by `import.meta.glob`, so never edit a central routes or handlers list), shared UI in `src/components/`, Playwright specs in `e2e/`.
- Merge conflicts: see "Cheap rebases" in the factory's FE agent. Never hand-merge `package-lock.json`; take main's and run `npm install --package-lock-only --no-audit --no-fund`.
- Forms: React Hook Form + Zod. Routing: React Router.
- Tests: Vitest + React Testing Library + user-event, with MSW for the network; Playwright + `@axe-core/playwright` for e2e and accessibility.
- `e2e/smoke.spec.ts` (`@smoke`) runs against dev and prod after deploys. Keep it **read-only**: load screens, assert real API reads succeed, and never submit or create anything. Add a smoke test when you add a key screen.
- Before every commit: `npm run lint && npm run typecheck && npm test -- --run && npm run build`.
- Branches are `factory/<issue>-<slug>`; commits are conventional; PRs contain `Closes #<issue>`.
- `CODEMAP.md` at the repo root says where everything lives. Read it first, and update it in the same PR when you add or move a feature or key file.
