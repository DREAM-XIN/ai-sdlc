# Requirement Review — F-OPERATOR-DECISIONS-NOTIFICATIONS-0001

## Role and reviewed candidate

Role: independent Requirement Reviewer.

Feature: `F-OPERATOR-DECISIONS-NOTIFICATIONS-0001` / Issue #229 / PR #230.

Reviewed exact PR head: `faf1ba726b69619422c058dbbcb0a7adbb917bb0`.

Requirement content is unchanged from functional Requirement candidate `bc64664bbfe38e108021e60345a914c3f70daa89`; the delta to the reviewed head contains only the legal Requirement Review START Event and its authoritative Manifest materialization.

Authoritative lifecycle at review:

- revision: `3`;
- `requirement: DONE`;
- `requirement-review: WORKING`;
- `requirement-gate: PENDING`;
- Design / Plan / Implementation not started.

## Normative sources reviewed

The Requirement was checked against the current protected `main` versions of:

- `docs/v0.3-release-spec.md`;
- `release/v0.3.0-draft.yaml`;
- canonical `ai-sdlc.operator/v1` capability registry and schemas;
- accepted Operation Store, Vertical Loop, and Effect Lineage authority/safety boundaries;
- Issue #229 scope and explicit release non-scope.

The canonical API already declares `operator.inbox`, `decision.list`, `decision.respond`, `notification.list`, and `notification.ack`; this Feature correctly requires durable trusted backends/semantics rather than inventing a parallel API surface.

## Verdict

**PASS_WITH_NOTES — 0 BLOCKER / 0 MAJOR / 1 MINOR**

The Requirement is sufficiently complete, bounded, testable, and consistent with the frozen v0.3 contract to approve and enter Design. The note below is a Design carry-forward and does not require Requirement rework.

## Requirement coverage

The Requirement correctly requires:

- durable bounded Decision identity/state and deterministic reconstruction;
- exact Feature / Operation / generation / revision / ref / candidate / policy / responder / expiry binding;
- trusted allowed-choice selection and fail-closed rejection of free-form/unbounded authorization;
- protected/default-branch/installation/control-repository policy authority with Feature-branch tighten-only semantics;
- fresh state/policy/identity validation before a response can authorize progression;
- preservation of cancellation, Effect Lineage, launch-linearization, Persist-linearization, and independent Gate-role boundaries;
- durable Notification Outbox with the four frozen minimum notification types;
- idempotent exact-notification acknowledgement without Feature lifecycle mutation or authorization side effects;
- new-session `operator.inbox` discovery of unfinished Operations, pending Decisions, and unread Notifications from durable trusted state;
- trusted scope isolation rather than caller-selected repository/tenant scope;
- replay/rebuild/CAS/duplicate/concurrent-resume coverage;
- canonical API/version/error/idempotency/capability-discovery compatibility;
- explicit exclusion of the second materially independent adapter, #221 real-runtime fault injection, #218 release-evidence accounting, and overall v0.3 release readiness.

The Requirement also preserves the existing canonical `decision.respond` request shape (`decision_id` plus bounded string `response`) while correctly requiring the trusted backend to resolve that string to one exact current allowed choice or reject it. This is compatible with the frozen Release Spec rule that generic natural-language approval alone is insufficient.

## MINOR-1 — preserve the full frozen Decision audit field set in Design

The frozen Release Spec explicitly lists authorization-bearing Decision audit bindings including `requested_by`, `requested_at`, `responded_by_user`, `responded_via_client`, `responded_at`, and `selected_choice` in addition to the authority-bearing state fields.

The Requirement covers responder identity, expiry, allowed choices, and request/response evidence/correlation, but groups some of those audit facts under a general "request/response evidence or correlation identity sufficient for audit and replay safety" clause instead of naming each frozen field.

This does not create a product/safety gap because §2 makes the frozen Release Spec normative and the Requirement expressly forbids silent reinterpretation. However, Design MUST make the full frozen audit record explicit and MUST NOT treat the Requirement's condensed wording as permission to omit any applicable Release Spec field.

Disposition: Design carry-forward; no Requirement rework required.

## Independence and release boundary

This review approves only the Requirement. It does not approve a Design, implementation, Code Gate, Verification Gate, release dogfood, second adapter, or overall v0.3 release readiness.

The next legal authoring role after trusted Gate materialization is Architect / Design Author. That author must carry MINOR-1 explicitly into the Design and remain independently reviewable.
