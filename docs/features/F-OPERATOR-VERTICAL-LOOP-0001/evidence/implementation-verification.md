# Implementation Verification Evidence — F-OPERATOR-VERTICAL-LOOP-0001

## Scope

Developer-side deterministic implementation verification for the approved `F-OPERATOR-VERTICAL-LOOP-0001` scope only.

This evidence is **not** an independent Code Review, QA verdict, Product Acceptance decision, proof of overall v0.3 release readiness, or proof of release-level real-runtime effect safety. Real runtime fault injection remains assigned to Issue #221.

## Functional candidate

Validated runtime candidate:

`dc88354429e1a81468ca78971cc3c51f30c2af62`

Branch:

`feature/F-OPERATOR-VERTICAL-LOOP-0001`

PR:

`#217`

The commits that add this evidence document and the later lifecycle Event are evidence/lifecycle-only changes. Independent Code Review must still bind its review to the actual PR head and inspect runtime equivalence rather than treating this Developer statement as review authority.

## Exact-head CI evidence

All required workflows for the functional candidate completed successfully:

- **Validate AI-SDLC protocol** — run `31361692236` — SUCCESS.
  - `python scripts/validate.py` SUCCESS.
  - Protocol log explicitly reports:
    - `Operator Store deterministic validation passed`;
    - `Operator Store remote durability/protection validation passed`;
    - `Operator vertical loop validation passed`;
    - `Operator vertical recovery validation passed`;
    - `Operator vertical completion-path validation passed`;
    - `Operator vertical deterministic fault/replay validation passed`;
    - `Operator vertical gh-aw validation passed`;
    - `AI-SDLC validation passed`.
  - cross-repo-control job SUCCESS.
- **Validate Public Runtime Distribution** — run `31361692195` — SUCCESS.
- **Required PR Gate** — run `31361692254` — SUCCESS.

## Worker Result / authority evidence

The vertical schemas and translator validation prove:

- Developer/Reviewer/QA payloads are strict `additionalProperties: false` data contracts;
- Workers cannot return authoritative Feature Event/Manifest/gate mutation structures through the role result contracts;
- Workers only declare logical output labels/kinds, not trusted artifact/evidence IDs or authoritative persisted URI provenance;
- role-specific trusted translators create bounded Feature Events from fresh trusted Feature truth;
- all Feature lifecycle mutation remains routed through the existing Feature Event + trusted Persist path;
- Developer completion does not PASS code-gate;
- QA PASS does not PASS release-gate or complete Product Acceptance.

## Collected-output receipt evidence

Deterministic validation covers trusted receipt binding and fail-closed behavior for:

- Operation id/generation/profile;
- semantic-effect and stable external-dispatch identities;
- dispatch id / role / trusted Worker identity / trusted collector identity;
- target repository / Feature id / exact expected revision;
- candidate head where applicable;
- trusted feature worker-run namespace;
- materialized byte length and SHA-256 content digest;
- declared logical output ↔ trusted collected output correspondence.

Namespace traversal, content mismatch, stale revision/stage/candidate, provenance mismatch and duplicate/conflicting receipts are rejected.

## Role independence evidence

Reviewer/QA separation is reconstructed from accepted **durable callback history**, not from a caller-supplied mutable policy object.

Validation proves:

- Reviewer identity cannot equal original Developer/remediation Developer identity;
- QA identity cannot equal Developer, accepted Reviewer or remediation Developer identity;
- fresh Reviewer work after remediation is bound to the post-remediation exact candidate;
- callback replay after restart reconstructs accepted Worker identity history from durable Store facts.

## Vertical lifecycle evidence

The deterministic completion path covers:

1. Developer implementation completion;
2. independent Reviewer PASS path;
3. Reviewer REWORK producing one bounded Developer remediation task;
4. remediation completion with candidate advancement;
5. fresh independent Reviewer PASS against the new exact candidate;
6. independent Verification QA PASS;
7. final bounded Operation DONE while Feature remains **Acceptance READY / release-gate PENDING**.

The tests therefore distinguish Operator vertical-loop completion from Product Acceptance authority.

## Exact binding evidence

Dispatch, callback and Persist handling fail closed on stale or conflicting trusted state. The implementation checks:

- target repository and ref;
- Feature id;
- exact Feature revision;
- exact current stage;
- task identity / role;
- durable semantic reservation;
- exactly one matching current-generation `dispatch.launch.authorized` fact;
- immutable launch/reservation candidate consistency;
- exact Reviewer/QA candidate head;
- fresh current-result candidate binding before accepting Developer output.

Developer work is allowed to advance the candidate head, but that new head is accepted only from fresh trusted Feature truth; the immutable launch authorization continues to represent the exact pre-work launch candidate.

## Operation Store / effect-safety integration evidence

The vertical loop reuses the completed `F-OPERATOR-OPERATION-STORE-0001` substrate:

- generation-independent semantic-effect reservation;
- stable external dispatch key;
- durable dispatch claim;
- `dispatch.launch.authorized` linearization;
- cancellation fencing;
- honest `NOT_LAUNCHED / LAUNCHED / UNKNOWN` lookup state;
- Persist request → linearized → confirmed ordering;
- protected remote Store CAS and semantic re-planning.

No parallel Feature truth, alternate effect store, or direct authoritative Manifest writer was introduced.

## Deterministic fault / replay evidence

`scripts/validate_operator_vertical_reconcile.py` and the existing Store validators deterministically cover:

- launch authorized but local acknowledgement missing;
- lookup `NOT_LAUNCHED` → retry with the same stable external dispatch key;
- lookup `LAUNCHED` → adopt existing launch without relaunch;
- lookup `UNKNOWN` → fail closed, do not launch;
- recorded UNKNOWN remains BLOCKED on a later resume and is **not** silently re-probed/cleared;
- cancellation around a missing launch acknowledgement prevents new launch;
- exact durable callback recording and restart replay;
- conflicting callback dispatch binding rejection;
- Persist translated Event durability;
- Persist linearized but not confirmed → exact Event replay/confirmation;
- Feature Persist already happened but local ack was lost → exact Event lookup/confirm without a duplicate write;
- cancellation before Persist linearization prevents new Persist effect;
- cancellation after Persist linearization permits only the already-linearized exact Event to complete.

These are deterministic fixtures and protocol-level implementation tests. They intentionally do **not** claim the release-level real-runtime failure-injection coverage assigned to #221.

## UNKNOWN / #219 boundary

This implementation intentionally does not absorb Issue #219 `Effect Lineage / UNKNOWN Resolution` semantics.

Within #216:

- UNKNOWN remains an honest fail-closed Store state;
- restart recovery does not manufacture proof that an UNKNOWN effect did or did not occur;
- recorded UNKNOWN is not cleared by generation change, resume, or speculative relaunch;
- no new effect-lineage/resolution protocol was added.

Any future authoritative resolution mechanism must come through separately reviewed #219 semantics.

## Regression evidence

The same exact-head Protocol run also passed existing:

- Feature lifecycle, Event/Persist and optimistic-precondition validation;
- remediation review completion validation;
- Operator API and MCP conformance, with MCP semantic writes still absent;
- Operation Store deterministic and remote durability/protection validation;
- cross-repository control/transport validation;
- Git write-precondition validation;
- GitHub workflow/action security validation;
- gh-aw adapter, autonomous role, candidate history, gate provenance/security, registry/profile and runtime-preflight validation;
- existing release-readiness baseline validation.

Public Runtime Distribution and Required PR Gate also passed on the exact functional candidate.

## Explicit non-scope confirmation

The implementation does **not** implement or claim completion of:

- Issue #219 Effect Lineage / UNKNOWN Resolution;
- Issue #221 real-runtime fault injection / release-level effect-safety proof;
- Decision/Notification persistence;
- complete `operator.inbox`;
- a second AI client adapter;
- full v0.3 dogfood;
- Naming/Benchmark work;
- Product Acceptance or `release-gate: PASS`;
- overall v0.3 release readiness.

## Developer conclusion

The approved #216 implementation scope is complete and deterministically verified at functional candidate `dc88354429e1a81468ca78971cc3c51f30c2af62`.

Evidence supports advancing only to **Implementation DONE / Code Review READY**. Independent Code Review is the next authority; this Developer does not PASS code-gate and does not continue into QA.
