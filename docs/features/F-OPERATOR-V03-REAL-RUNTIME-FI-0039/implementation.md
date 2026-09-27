# v0.3 real-runtime fault-injection fixture

This branch is a release-only Feature/PR fixture for Issue #221.

It intentionally contains no product implementation change. The fixture-local
workflow starts at Code Review exactly as required by Issue #276. Provisioning
registers this document as the one draft implementation artifact and moves only
`code-review` from `READY` to `WORKING`; it does not fabricate an implementation
lifecycle stage, an implementation completion, a Gate verdict, or Review evidence.

A real Reviewer `PASS` may approve this draft artifact through the normal Vertical
callback path. A real Reviewer `REWORK` remains canonical through the narrow
artifact-backed Code-Review-first remediation contract: the remediation identity
continues to target `implementation`, but no implementation lifecycle stage is
invented or completed merely to make remediation possible.

A Worker result remains recommendation/evidence only. Any lifecycle mutation
still requires the protected Operator Store callback path and exact Feature
Event/Persist authority exercised by the #221 full-runtime test.

This fixture is not Product Acceptance, dogfood, or release-ready evidence and
must not be merged as a product change.
