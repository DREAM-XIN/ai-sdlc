# Design Remediation — F-OPERATOR-OPERATION-STORE-0001

## Role

Architect remediation for `F-OPERATOR-OPERATION-STORE-0001-DESIGN-REMEDIATION-1`.

## Source findings

Independent Design Review identified two MAJOR findings:

1. the first Design selected a trusted state-ref name and CAS path but did not positively verify that the production remote ref is actually protected before semantic writes;
2. reservation/claim JSON ledger files were replaced when appending logical records, conflicting with the frozen requirement that consumed reservations and claims themselves are immutable while only projections are replaceable cache.

## Remediation delivered in Design v2

### MAJOR-1

Design v2 introduces a trusted `StateRefProtectionVerifier` and typed `ProtectionReceipt` with three states: PROTECTED / UNPROTECTED / UNKNOWN. Production semantic writes require a repository/ref-bound PROTECTED receipt and fail closed for absent, unprotected, unknown, or mismatched protection.

It also defines trusted installation/control provisioning and two safe first-ref initialization modes. No Operation/reservation/claim/Persist semantic state may be written until repository policy is positively verified as PROTECTED.

Feature/client/Worker input cannot self-attest protection or choose the state ref.

### MAJOR-2

Design v2 eliminates mutable reservation/claim ledgers:

- semantic reservation file is create-once immutable and permanently binds semantic inputs plus one stable external dispatch key;
- dispatch claim is create-once immutable;
- Feature claims are individual create-once immutable claim artifacts;
- launch authorization, receipt observations, callbacks, generation takeover, cancellation and Persist transitions are immutable Operation journal events;
- the trusted mutation planner has only `create_immutable` and `replace_projection`; only projection paths may be replaced.

Current state is reconstructed from immutable artifacts/events, not by rewriting claim/reservation history.

## Preserved design semantics

CAS + semantic re-evaluation, generation-independent semantic identity, launch/Persist linearization, UNKNOWN inheritance, canonical `operation.start/status/cancel` scope, internal unfinished-Operation query and honest `operator.inbox` unavailability remain unchanged in intent.

## Result

Both MAJOR findings are addressed at Design level. A fresh independent Design Re-review is required before `design-gate` may PASS.
