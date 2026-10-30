# <Product> Web (React 19 + TypeScript)

Part of a software factory. Specs live in **<owner>/<product>-product** (`docs/factory/`) and are read-only here.

- API types are generated, never hand-written: `npm run contract:sync` copies `openapi.yaml` from the product repo to `contracts/`, and `openapi-typescript` writes `src/api/schema.d.ts`. Calls go through the `openapi-fetch` client plus TanStack Query.
- Design: tokens from UI-SPEC live in `src/styles/tokens.css`. Use CSS variables only, and implement every state in the spec (empty, loading, error, disabled, focus).
- Structure: `src/features/<feature>/`, shared UI in `src/components/`, MSW mocks in `src/mocks/`, Playwright specs in `e2e/`.
- Forms: React Hook Form + Zod. Routing: React Router.
- Tests: Vitest + React Testing Library + user-event, with MSW for the network; Playwright + `@axe-core/playwright` for e2e and accessibility.
- Before every commit: `npm run lint && npm run typecheck && npm test -- --run && npm run build`.
- Branches are `factory/<issue>-<slug>`; commits are conventional; PRs contain `Closes #<issue>`.
