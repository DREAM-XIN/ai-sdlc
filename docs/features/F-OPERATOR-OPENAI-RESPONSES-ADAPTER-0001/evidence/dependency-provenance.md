# OpenAI Responses Implementation Dependency Provenance

Status: **WORKING / fail-closed evidence only**

This artifact records the production-dependency authority boundary for
`F-OPERATOR-OPENAI-RESPONSES-ADAPTER-0001` / PR #233. It is Developer evidence,
not Code Review, Verification, Acceptance, Supported status, or release evidence.

## Authority rule

The Responses feature must not manufacture the production Vertical/Persist/
recovery prerequisites that make Supported mode possible. A prerequisite counts
as available only when the executable runtime is present on the current trusted
`main` baseline. Issue state, PR prose, comments, branch contents, and deterministic
fixtures are not sufficient authority.

PR #233 therefore protects these upstream authority paths from any candidate delta:

- `scripts/operator_vertical_feature_persist_gateway.py`
- `scripts/operator_vertical_reconcile_classified.py`
- `scripts/operator_v03_write_runtime.py`
- `scripts/operator_v03_vertical_production_runtime.py`
- `scripts/operator_vertical_callback.py`

`.github/workflows/operator-openai-responses-dependency-provenance.yml` fetches
`origin/main` freshly and fails if the #233 candidate changes any protected path.
The comparison intentionally does not trust `pull_request.base.sha`, because the
long-lived PR metadata can lag the actual current-main merge baseline.

## Current executable baseline truth

At this checkpoint, a direct compare of current `main` against
`7376dd2558cd6d387dd4870f91fe9c6fc33356fa` is `identical` (`ahead_by=0`).
That commit is the reviewed #247 exact Feature Event runtime baseline.

Structured GitHub/runtime truth currently does **not** authorize consuming the
remaining production dependencies:

- PR #249 is closed with `merged=false`, and current `main` does not expose
  `DurableVerticalFeaturePersistGateway`;
- PR #251 is open/draft with `merged=false`, and current `main` does not expose
  `FailureClassifyingTrustedRecoveringVerticalExecutor`;
- PR #253 is open with `merged=false`; the final full-Vertical production factory
  is therefore not trusted-main authority;
- PR #255 is open/draft with `merged=false`; stale-recorded-callback convergence
  remains a hard prerequisite rather than accepted baseline behavior.

Some Issue comments currently say #249/#251/#253 were merged or completed. Those
comments are not used as runtime authority because they conflict with current
structured PR state and default-branch executable truth. The Responses readiness
path remains fail closed until the code itself is on trusted `main`.

## Current #233 provenance

The actual PR #233 changed-file set contains none of the protected dependency
paths above. Production authority therefore remains external to this Feature.

The existing Responses readiness probes must continue to report incomplete until
trusted-main executable evidence changes:

- `full_vertical_production_factory = false`
- `stale_recorded_callback_convergence = false`
- WU6 Persist deterministic-rejection classification = blocked
- WU7 Lane B = blocked
- WU8 stale-callback validation = blocked
- mechanical implementation-completion candidate = false

A future change in Issue/PR text alone must not change these values. A future
reviewed merge to `main` should change the executable probes automatically; only
then may the strict WU6/WU7/WU8 paths run.
