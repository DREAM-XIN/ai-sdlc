# Operator Store SPI — v1

Issue: #220

## Decision

AI-SDLC freezes a storage-technology-neutral Operator Store backend SPI at `ai-sdlc.operator-store-backend/v1`.

The existing protected remote-Git implementation remains the **v0.3 reference backend**, not the permanent or only allowable backend. This work deliberately does not introduce Postgres, Redis, EventStore, or another database merely for abstraction purity.

## Layering

The Store is split into two authority layers.

### Semantic layer

The existing deterministic model and command planners own Operator Store meaning:

- append immutable Operation events;
- rebuild deterministic projections from immutable history;
- create-once semantic reservations;
- generation, Feature, and dispatch claims;
- exact Operation/effect lookup and correlation;
- launch and Persist receipt semantics;
- cancellation, takeover, replay, and idempotency rules;
- immutable-versus-projection path rules.

A storage backend **must not reinterpret these semantics**. It persists `StoreMutationPlan` values produced by trusted planners.

### Backend SPI

`scripts/operator_store_spi.py` defines the minimal persistence contract consumed by `OperatorStoreRuntime`:

- trusted `repository` identity;
- trusted `state_ref` identity;
- `read_snapshot()`;
- `commit(plan, protection_receipt)`;
- `commit_replanned(planner, protection_receipt, max_attempts=...)`.

A conforming backend must preserve exact compare-and-set/conflict semantics and must fail closed unless a positively bound protection receipt authorizes semantic writes.

The runtime now validates this SPI at composition time; caller/model/Feature payloads cannot select an arbitrary backend implementation or state ref.

## v0.3 reference backend

The reference production path remains:

`OperatorStoreRuntime` → `RemoteGitStateRefBackend` → protected Git branch ref.

This backend preserves:

- immutable Store objects as Git objects/paths;
- exact parent-SHA planning;
- remote state-ref compare-and-set via non-fast-forward rejection;
- re-read/re-plan after conflict;
- durable fresh-process recovery from the shared protected ref;
- auditable commits and protection verification.

`GitStateRefBackend` remains useful for local deterministic tests, and `MemoryStateRefBackend` remains test-only. Neither changes the production reference-backend decision.

## Future backend requirements

A future backend may use a different storage engine only if it implements the same semantic contract and passes backend conformance evidence. It does not gain Feature lifecycle authority, Gate authority, external-launch authority, Persist authority, or authorization-policy authority.

At minimum, future-backend evaluation must preserve:

- append-only immutable event history;
- deterministic replay/projection rebuild;
- create-once reservation/claim behavior;
- compare-and-set or equivalent serializable conflict detection;
- exact effect/Operation/receipt lookup;
- positive protection/authority proof before semantic writes;
- concurrency-safe re-planning;
- idempotent equivalent-operation convergence.

## Trigger metrics for considering another backend

A second production backend should be justified by measured dogfood/production evidence, especially:

- state-ref CAS conflict rate;
- p95 and p99 Operation event write latency;
- GitHub API/rate saturation;
- callback throughput;
- write amplification;
- projection rebuild cost;
- multi-tenant active Operation count;
- operational availability dependence on GitHub branch/ruleset APIs.

Until those signals justify a migration, v0.3 keeps the Git reference backend to preserve auditability and reduce release risk.
