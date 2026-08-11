# Design Review — F-OPERATOR-DECISIONS-NOTIFICATIONS-0001

## Role and reviewed candidate

Role: independent Design Reviewer.

Feature: `F-OPERATOR-DECISIONS-NOTIFICATIONS-0001` / Issue #229 / PR #230.

Reviewed exact PR head: `f9b569f8062e0ae6a1caea7b20c474c726066bdb`.

The Design content is unchanged from the Design handoff candidate `833c9dda9ea790189d83185dd02e56abdb854806`; the delta to the reviewed head contains only the legal Design Review START Event and its authoritative Manifest materialization.

Authoritative lifecycle at review:

- revision: `7`;
- `design: DONE`;
- `design-review: WORKING`;
- `design-gate: PENDING`;
- Plan / Implementation not started.

## Normative sources reviewed

The Design was reviewed against:

- approved `requirement.md`;
- independent Requirement Review `4902265577` and its MINOR-1 carry-forward;
- protected `docs/v0.3-release-spec.md` and `release/v0.3.0-draft.yaml`;
- canonical `ai-sdlc.operator/v1` capability registry/schemas;
- accepted Operation Store, Vertical Loop, Effect Lineage, cancellation, launch-linearization and Persist-linearization semantics.

## Verdict

**PASS_WITH_NOTES — 0 BLOCKER / 0 MAJOR / 1 MINOR**

The Design is implementable, preserves the frozen v0.3 authority boundaries, and is sufficiently precise to enter Plan. No Design rework is required before planning.

## Safety and architecture findings

The Design correctly:

- extends the existing protected Operator Store instead of creating a second mutable authority;
- uses immutable Decision/Notification definitions plus append-only Operation facts and deterministic reducers;
- explicitly closes Requirement Review MINOR-1 by preserving `requested_by`, `requested_at`, `responded_by_user`, `responded_via_client`, `responded_at`, and `selected_choice` as durable/rebuildable audit facts;
- preserves canonical `decision.respond` compatibility while treating `response` only as an exact current allowed-choice id, with no fuzzy natural-language authorization in trusted backend code;
- re-reads protected Decision policy and effective tighten-only restrictions at authority-bearing creation/response/use boundaries;
- prevents Feature-branch policy expansion and caller/Worker selection of policy, state ref, clock, scope or privileged action vocabulary;
- binds Decision authority to exact Feature revision/ref, candidate when applicable, Operation generation, policy epoch/digest, expiry and responder identity;
- keeps Decision resolution separate from `dispatch.launch.authorized`, Effect Lineage gating, cancellation/supersession and `persist.linearized`;
- makes notification creation deterministic and semantically deduplicated, with bounded append-only/idempotent acknowledgement;
- defines `operator.inbox` as a trusted-scope pure read projection over unfinished Operations, pending Decisions and unread Notifications;
- preserves the human/Product Acceptance boundary: a resolved `NEEDS_ACCEPTANCE` Decision cannot synthesize Acceptance Evidence or PASS `release-gate`;
- requires authoritative validators to be wired into `scripts/validate.py` and keeps #221 real-runtime fault injection outside this Feature.

## MINOR-1 — make expiry materialization explicitly deterministic

The Design correctly defines `decision.expired` as an append-only fact and requires `decision.respond` to reject an expired Decision using a trusted runtime clock. Implementation and Plan MUST make the split explicit:

1. authorization safety does not wait for a reconcile tick — `decision.respond` and any later authorization consumption must compare `expires_at` against the trusted clock and fail closed immediately;
2. durable projected `EXPIRED` state must be materialized by a trusted deterministic reconcile/tick that appends `decision.expired` once, rather than making rebuild output depend implicitly on whichever wall-clock time happens to execute the reducer;
3. replay/rebuild from the same durable Store history must therefore produce the same Decision state, while a later reconcile may append the next deterministic expiry fact.

This is a deterministic-projection precision note, not a current authorization vulnerability. The Design already contains the necessary event and trusted-clock concepts; Plan/Implementation must make their ownership explicit.

## Independence and release boundary

This review approves only `design-v1`. It does not approve Plan, implementation, Code Gate, Verification Gate, Product Acceptance, the second materially independent adapter, #221 real-runtime fault injection, #218 release-evidence synchronization, or overall v0.3 release readiness.

Next legal role after trusted Gate materialization: Plan Orchestrator / Plan Author.
