# Design Review v2 — F-GHAW-DOMESTIC-PROVIDERS-0001

## Verdict

**PASS_WITH_NOTES**

Severity summary:

- BLOCKER: 0
- MAJOR: 0
- MINOR: 1
- SUGGESTION: 0

## Re-review scope

Independently re-reviewed the revised `design.md` after `F-GHAW-DOMESTIC-PROVIDERS-0001-DESIGN-REMEDIATION-1` completed, focusing on `DR-MAJOR-1`, the two Requirement Review MINORs, trusted Registry boundaries, strict compile/materialization, compatibility, security, rollback, and deterministic testability.

## DR-MAJOR-1 closure

**RESOLVED.**

The revised Design explicitly separates:

- bounded deterministic materialization load: `load_registry(require_source_files=False)`;
- renderer `--check` and all ordinary read/routing/preflight/audit/allowlist consumers: default `require_source_files=True`.

The relaxed mode skips only the filesystem-existence predicate needed to bootstrap a generated worker source. It retains Registry schema, identity, URL/host, model, credential, path canonicalization, protocol, maturity, and uniqueness validation.

The Design also defines both required deterministic directions:

1. a new valid profile with an absent source can be materialized and then passes normal validation;
2. deleting that source causes normal Registry load, `--check`, and trusted consumers to fail closed again.

This removes the generation deadlock without weakening execution trust.

## Requirement Review closure

- `RQ-MINOR-1`: RESOLVED — static certification/live entitlement/bounded dogfood/maturity are separate durable states; static preflight stays non-networked.
- `RQ-MINOR-2`: RESOLVED — provider sources/date are durable, Qwen Beijing-region key coupling is explicit, target/workspace base-url override is prohibited, and future endpoint/model changes are reviewed Registry migrations.

## Design rubric

- Requirement coverage: PASS.
- Component boundaries: PASS.
- Contracts/interfaces: PASS.
- Data/config model: PASS.
- Failure handling: PASS.
- Security: PASS.
- Compatibility: PASS.
- Observability/evidence: PASS.
- Migration/rollback: PASS.
- Testability: PASS.
- Risks/alternatives: PASS_WITH_NOTE.

## MINOR finding

### DR2-MINOR-1 — Implementation must keep relaxed Registry loading structurally unreachable from normal consumers

The Design states the correct boundary, but the code shape matters. Implementation should avoid adding a generic CLI option or public runtime configuration that lets resolver/preflight/audit/allowlist callers select `require_source_files=False`.

Preferred implementation is local and capability-specific: renderer/materialization code passes the relaxed argument only in write/bootstrap paths; normal helper defaults remain strict. Code Review should verify this property explicitly.

This is non-blocking because the Design already requires it; the note is an implementation review focus rather than a missing design decision.

## Conclusion

The revised Design satisfies the approved Requirement and closes the prior MAJOR. `design-gate` may PASS with `DR2-MINOR-1` carried into Plan/Code Review as an explicit implementation constraint.
