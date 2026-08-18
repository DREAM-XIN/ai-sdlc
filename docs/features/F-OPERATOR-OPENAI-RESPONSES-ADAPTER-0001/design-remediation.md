# Design Remediation — F-OPERATOR-OPENAI-RESPONSES-ADAPTER-0001

## Role

Architect / Design Remediation Author for `F-OPERATOR-OPENAI-RESPONSES-ADAPTER-0001-DESIGN-REMEDIATION-1`.

## Source review

Independent Design Review / PR Review `4905115842`:

`REWORK — 0 BLOCKER / 2 MAJOR / 0 MINOR`.

The reviewed Design blob remains preserved in `design.md`; `design-v2.md` is the remediated candidate for fresh independent Design Re-review.

## Outcome

**DONE** — `design-v2.md` closes exactly the two MAJOR findings without expanding the adapter capability or authority surface.

## MAJOR-1 closure — Supported proof now crosses real production composition

Design v2 replaces the old Supported write proof based on “production-shaped Responses adapter + Store/Vertical/gateway doubles” with two explicit lanes:

- Lane A may retain deterministic lower-level doubles for protocol and adversarial fault injection, but can never earn Supported status;
- Lane B is mandatory for Supported status and drives provider-side deterministic Responses objects through the real production registry/parser/call journal/request builder/output encoder and then through the actual final trusted production composition constructor/interfaces present on the implementation baseline.

Lane B must prove:

- `operation.start` reaches the trusted profile-bound Vertical canonical backend;
- equivalent duplicate start converges against that production backend, satisfying Requirement scenario #18;
- adapter, Vertical, Decision, Notification and Persist share the same protected Store authority;
- only start/cancel/respond/ack are client writes;
- `operation.resume` remains server-only;
- Persist crosses the final Durable Persist gateway / trusted Feature Event transport exactly once;
- no second Persist or dispatch authority exists;
- production construction cannot silently fall back to test-only/in-memory authority.

## MAJOR-2 closure — stale recorded callback convergence is a hard prerequisite

Design v2 removes the prior optional “when/if” / “relevant #255 semantics” posture.

The Supported label is now blocked unless the implementation baseline contains PR #255’s reviewed semantic contract or a separately reviewed semantically equivalent implementation.

The deterministic contract requires, for an already-durable callback that becomes stale against fresh Feature/ref/revision/stage/candidate truth:

- exactly one durable deterministic rejection;
- stable reviewed fail-closed/BLOCKED convergence;
- fresh-process/repeated recovery performs zero further Store mutation and does not append duplicate rejection;
- zero `feature.event.translated` and zero Persist authority;
- Effect Lineage predecessor fencing remains intact;
- zero successor reservation, new `external_dispatch_key`, or second external launch while the predecessor is unresolved;
- transient Feature-read failures remain transient and are not durably misclassified as stale.

## Retained reviewed properties

The remediation intentionally preserves the areas that the Independent Design Review found sufficient:

- exact ten-tool Responses surface;
- no `operation.resume` or generic escape hatch;
- independent Responses protocol/parser/conformance boundary;
- strict schema and `call_id` protocol handling;
- durable call replay journal;
- trusted context ownership;
- generation-independent semantic identity and stable external dispatch identity;
- `dispatch.launch.authorized`, cancellation, UNKNOWN and Effect Lineage safety;
- bounded Decision and Notification semantics;
- provider replay/retrieval/fresh-process safety;
- public packaging and later real-service dogfood separation.

## Dependency posture

PRs #245/#247/#249/#251/#253/#255 were re-read during remediation and remain unmerged workstreams at authoring time. Design v2 therefore depends on the final reviewed semantics/interfaces available on the implementation baseline rather than treating any current PR head as `main` authority.

## Re-review request

A fresh Independent Design Reviewer should review `design-v2.md` against approved Requirement and PR Review `4905115842`, focusing on the two MAJOR closures above.

No waiver is requested. The Architect does not self-pass `design-gate`, does not enter Plan and does not implement the Feature in this remediation step.
