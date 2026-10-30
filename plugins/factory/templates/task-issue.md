**Repo:** the `api` repo for `be` tasks, the `web` repo for `fe` tasks
**Title:** `[T-NN] <task title>`
**Labels:** `factory:task`, plus `be` or `fe`, plus `cr:CR-NNN` for change-request tasks

---

Implements owner/<product>-product#<story> (ACs: AC1, AC2)
Change request: CR-NNN   <!-- only for change-request tasks; omit for baseline tasks -->
Depends on owner/<product>-api#<n>, owner/<product>-web#<n>

### What
<One paragraph: the change, in terms the developer agent can act on.>

### Spec references (binding; all in the product repo)
- `docs/factory/02-architecture/openapi.yaml`: operationIds `createOrder`, `listOrders`
- `ARCHITECTURE.md` §6 feature `Orders`; ADR 0001
- `UI-SPEC.md` SCR-02, components `OrderForm`, `QuantityInput`   <!-- fe -->
- `TEST-PLAN.md` TC-004, TC-005

### Acceptance (from the story)
- [ ] AC1: ...
- [ ] Tests TC-004 (xUnit integration), TC-005 (Vitest) automated and green

### Out of scope
- ...

**Size:** S | M · **Parallel with:** T-NN

<!-- factory: owned by Architect (planning); source docs/factory/04-plan/PLAN.md in the product repo -->
