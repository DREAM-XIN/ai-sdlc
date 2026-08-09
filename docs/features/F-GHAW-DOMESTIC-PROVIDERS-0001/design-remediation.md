# Design Remediation — F-GHAW-DOMESTIC-PROVIDERS-0001

## Scope

Remediates `DR-MAJOR-1` from `design-review.md` only.

## Change

The revised Design now defines two explicit Registry source-existence modes:

- deterministic worker materialization write mode uses `load_registry(require_source_files=False)` only to validate Registry metadata/identity/security before generating a registered worker source that does not yet exist;
- renderer `--check` and every ordinary trusted read/routing/preflight/audit/allowlist consumer continue to use the default `require_source_files=True` and fail closed on a missing source.

Materialization workflow discovery is explicitly permitted to use the bounded mode before rendering; PR compile discovery occurs after generated sources are committed and therefore uses normal strict source-existence validation.

## Required tests added to Design

1. Positive fixture: new valid compatible profile + absent source fails normal load, passes bounded materialization load, is generated deterministically, then passes normal load and `--check`.
2. Negative fixture: deleting the generated source again makes normal Registry load, renderer `--check`, and normal trusted consumers fail closed.

## Boundary

No Requirement, provider scope, provider endpoint/model fact, Gate semantics, runtime authority, or provider maturity was changed by this remediation.
