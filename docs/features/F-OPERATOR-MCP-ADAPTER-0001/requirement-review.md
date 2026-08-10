# Requirement Review — F-OPERATOR-MCP-ADAPTER-0001

## Review context

- Role: independent Requirement Reviewer
- Feature: `F-OPERATOR-MCP-ADAPTER-0001`
- Issue: #210
- Reviewed artifact: `requirement-v1`
- Reviewed Requirement URI: `docs/features/F-OPERATOR-MCP-ADAPTER-0001/requirement.md`
- Authoritative review-start state: revision `3`, `requirement-review: WORKING`, `requirement-gate: PENDING`
- Normative upstream: frozen v0.3 Release Spec, canonical `ai-sdlc.operator/v1` foundation from `F-OPERATOR-CANONICAL-API-0001`, and current role/lifecycle rules

## Verdict

**PASS_WITH_NOTES**

- BLOCKER: 0
- MAJOR: 0
- MINOR: 1

The Requirement is sufficiently clear, bounded, testable, security-aware, and aligned with the frozen v0.3 implementation order to proceed to Design. The single MINOR is a Design-level clarification and does not require Requirement rework.

## Findings

### MINOR-1 — distinguish canonical discovery from invokable MCP write exposure

The Requirement correctly preserves the canonical registry and honest `CAPABILITY_UNAVAILABLE` semantics, while also requiring that this Feature not expose semantic write capabilities as supported MCP operations. However, acceptance/conformance wording stating that write capabilities are not "advertised/exposed as supported MCP operations" can be read two ways:

1. correct interpretation: write capabilities remain known canonical capability identifiers and may appear in `system.capabilities` with `available: false`, but are not registered as invokable MCP tools/resources in this read-only adapter; or
2. incorrect interpretation: filter write capability identifiers out of canonical discovery entirely.

The second interpretation would conflict with the frozen canonical API behavior, whose `system.capabilities` discovery is registry-oriented and reports bounded availability rather than redefining the registry per adapter.

**Required Design clarification:** explicitly freeze the first interpretation. The MCP transport may expose only the bounded read-only invokable surface, while canonical discovery remains complete and honest about known-but-unavailable write capabilities. Tests should assert both properties independently.

Severity is MINOR because the Requirement already contains the necessary authority, availability, and read-only constraints; this is an ambiguity in how two valid clauses compose, not a missing release requirement or unsafe authorization boundary.

## Review checks

### Scope and release alignment — PASS

The Requirement implements the next frozen workstream as one genuine MCP AI-client adapter over `ai-sdlc.operator/v1`, bounded to read-only inspect/status behavior. It explicitly does not claim the second supported adapter, write-capable release slice, Operation Store, orchestration recovery, Decision/Notification durability, dogfood completion, or overall v0.3 release readiness.

### Canonical capability surface — PASS_WITH_NOTE

The required MCP read surface covers `system.capabilities`, `project.inspect`, `feature.status`, `operator.inbox`, `operation.status`, `decision.list`, and `notification.list`. The six-capability shared conformance subset exactly matches the frozen v0.3 subset. `project.inspect` is correctly required for MCP while not silently mutating the frozen common conformance subset. MINOR-1 applies only to the discovery/exposure distinction for write capabilities.

### Version and structured errors — PASS

The Requirement requires `ai-sdlc.operator/v1`, fail-closed unsupported-version handling, deterministic unknown-capability behavior, canonical `CAPABILITY_UNAVAILABLE` for known unavailable capabilities, and machine-readable structured errors across MCP translation.

### Trusted identity and authorization boundary — PASS

The Requirement explicitly separates client-controlled MCP identity from trusted runtime/service identity, forbids client assertion of trusted authorization context, forbids privilege expansion, and prevents bypass of canonical validation/backend availability checks. MCP adapter identity is not treated as human authorization.

### Lifecycle authority and mutation safety — PASS

The Requirement keeps MCP as transport rather than authority. Direct Manifest/Event/Gate/shell/repository mutation substitutes are prohibited, and semantic write capabilities are excluded from this Feature. Existing Feature Event/Persist and independent-role controls remain authoritative.

### Honest backing-state semantics — PASS

The Requirement permits later-backed reads to return `CAPABILITY_UNAVAILABLE` and explicitly forbids representing deterministic test fixtures as production durable stores. This prevents the MCP adapter Feature from faking Operation/Decision/Notification completion.

### Conformance and material independence — PASS

The Requirement requires use of the reusable transport-neutral `CanonicalAdapter` harness or an equivalent reviewed extension, a real MCP protocol boundary, stable adapter identity, identity propagation, version/error/unavailability semantics, trusted-field injection rejection, and proof that the implementation is not an alias/thin wrapper around either canonical fixture adapter.

### Determinism and dependency constraints — PASS

Tests must not require a live external MCP service, production credentials, or network access. An in-process protocol/client harness is allowed only when it exercises the same supported MCP adapter implementation. New MCP dependencies must be declared/pinned under repository policy.

### Backward compatibility — PASS

The Requirement preserves canonical fixture tests and v0.2 lifecycle/gh-aw/Feature Event/Persist behavior, and explicitly excludes `VERSION` / final v0.3 release-manifest changes.

### Acceptance criteria — PASS

Acceptance conditions are observable and independently verifiable. They distinguish Feature completion from v0.3 release readiness and preserve downstream QA/review authority.

## Gate recommendation

`requirement-gate`: **PASS**

Justification: no BLOCKER or MAJOR finding exists. MINOR-1 is bounded, actionable, and appropriately owned by Design. Requirement `requirement-v1` may be approved with this review Evidence attached.

## Design handoff requirement

The Architect must consume MINOR-1 and explicitly define:

- complete canonical `system.capabilities` discovery semantics;
- the exact MCP-visible invokable read tool/resource surface;
- fail-closed behavior for attempts to invoke write capability identifiers through MCP;
- deterministic tests proving write capabilities may remain discoverable as known/unavailable while not being exposed as invokable MCP write operations.
