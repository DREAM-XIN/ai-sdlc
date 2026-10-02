# v0.3 real-runtime scenario fixture: duplicate-worker-completion

This branch is one fixed release-only fixture slot for Issue #221 under prerequisite #310.

Fixed Feature: `F-OPERATOR-V03-FI-DUPLICATE-WORKER-0001`  
Fixed ref: `verification/v0.3-fi-duplicate-worker-221`

It intentionally contains no product implementation change. Provisioning registers this
file as the single draft implementation artifact and moves only `code-review` from
`READY` to `WORKING` using the same reviewed Code-Review-first profile as #276/#277.
It does not fabricate a Worker result, Gate verdict, Product Acceptance, dogfood, or
release evidence. Runtime lifecycle mutation remains protected Store + exact Feature
Event/Persist authority.

The slot is permanently bound to scenario `duplicate-worker-completion` for v0.3. It must not be
reset, recycled for another scenario, force-pushed, or merged as a product change.
