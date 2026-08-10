# Plan — F-OPERATOR-OPERATION-STORE-0001

## Role

Orchestrator implementation plan for approved `requirement-v2` + `design-v2`.

## Delivery strategy

Implement the Store as small deterministic layers so concurrency, authority and immutability can be reviewed independently before the later vertical-loop workstream consumes them.

## Work unit 1 — Store schemas and pure model/reducer

Files:

- `spec/operator/store/*.schema.json`
- `scripts/operator_store_model.py`

Deliver:

- canonical JSON/digest helpers;
- Operation Event v1 validation/model;
- immutable reservation, dispatch claim and Feature claim models;
- projection model/reducer;
- semantic-effect-key and stable external-dispatch-key derivation;
- deterministic current-generation/active-claim/linearization/UNKNOWN state reconstruction.

Verification:

- immutable event ordering/replay tests;
- projection rebuild equality;
- semantic key generation independence;
- impossible-history rejection.

Dependency: none.

## Work unit 2 — Pure semantic command planner

Files:

- `scripts/operator_store.py`

Deliver `StoreMutationPlan` with only:

- `create_immutable`;
- `replace_projection`.

Implement bounded commands for:

- operation start/convergence;
- trusted generation takeover primitive;
- semantic reservation;
- generation dispatch claim;
- launch authorization;
- launch lookup/callback correlation;
- cancellation;
- Persist requested/linearized/confirmed/correlation;
- unfinished-Operation query.

Every command operates against an immutable Store snapshot and returns deterministic domain outcome; no Git/network I/O.

Verification:

- equivalent starts converge;
- active Feature ownership exclusive;
- duplicate claims converge;
- takeover retains external key;
- cancellation/launch and cancellation/Persist ordering;
- UNKNOWN fail-closed/inheritance;
- callback and lost-ack replay safety;
- immutable-path replacement rejection.

Dependency: WU1.

## Work unit 3 — Protection and CAS adapters

Files:

- `scripts/operator_store_protection.py`
- `scripts/operator_store_git.py`

Deliver:

- PROTECTED / UNPROTECTED / UNKNOWN verifier contract;
- repository/ref-bound `ProtectionReceipt`;
- fail-closed Store writer enforcement;
- safe initialization gate that cannot write semantic Store state before protection verification;
- memory state-ref CAS backend with explicit conflict injection;
- local Git state-ref backend using descendant commits + exact expected-ref lease/CAS;
- path allowlist for `state/operator/v1/**` and immutable-object update/delete rejection.

Verification:

- all protection outcomes;
- forged/mismatched receipt rejection;
- first-ref semantic write rejection before protection;
- exact CAS success and stale-ref conflict;
- conflict re-read/re-plan path;
- no Feature branch/state/features mutation.

Dependency: WU1/WU2.

## Work unit 4 — Canonical API durable backing

Files:

- `scripts/operator_store_backends.py`
- bounded changes to `scripts/operator_api.py` only where required for structured Store domain error mapping/runtime backend composition.

Deliver trusted backends for:

- `operation.start`;
- `operation.status`;
- `operation.cancel`.

Keep unavailable:

- `operator.inbox`;
- `operation.resume`;
- Decision write/backing;
- Notification write/backing.

Do not add MCP write tools.

Verification:

- canonical schemas still pass unchanged;
- idempotency + expected revision requirements preserved;
- durable start/status/cancel success and structured failures;
- `system.capabilities` reports only actually configured Store backends available;
- inbox/resume/Decision/Notification remain honestly unavailable;
- prior MCP read-only conformance unchanged.

Dependency: WU1-WU3.

## Work unit 5 — Deterministic end-to-end validator

Files:

- `scripts/validate_operator_store.py`
- integrate into `scripts/validate.py`.

Cover every approved Requirement v2 deterministic scenario, including injected concurrent writer races without timing sleeps.

Use:

- MemoryStateRefBackend for semantic race/fault tests;
- temp local Git repository for exact ref/CAS/history tests;
- fake trusted protection verifier/receipts;
- fake Feature/candidate verification receipts;
- fake launch receipt lookup states.

Dependency: WU1-WU4.

## Work unit 6 — Trusted production wiring boundary and regression evidence

Add only the minimal trusted runtime/config entrypoints necessary to construct a Store backend with:

- trusted repository identity;
- protected state ref from control configuration;
- protection verifier;
- trusted writer credentials supplied by workflow/runtime, never Worker/client payload.

No vertical role runner or external Worker gateway is added.

Run:

- `python scripts/validate.py`;
- canonical Operator/MCP validators;
- lifecycle/Persist/cross-repo/public-runtime CI suites.

Produce Implementation Evidence pinned to the exact validated runtime head.

Dependency: WU1-WU5.

## Review checkpoints

Code Review must explicitly verify:

- only projections can be replaced;
- reservation/claim/event paths are create-only;
- production semantic writes require positively verified protection;
- exact-ref CAS is not an unbounded force rewrite;
- CAS retry re-evaluates semantics;
- external dispatch key survives generation takeover;
- launch and Persist linearization are distinct;
- UNKNOWN never triggers speculative relaunch;
- no Store code edits Feature Manifest/Gates;
- canonical inbox and later capabilities remain unavailable;
- no MCP write surface was added.

## Completion condition

Implementation may move to Code Review only after the exact candidate passes deterministic Store validation plus the repository's existing required regression suite, with durable implementation/evidence artifacts and no claim that the downstream vertical loop or v0.3 release is complete.
