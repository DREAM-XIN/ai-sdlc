# Trusted Decision Feature Event gateway

Tracking issue: #246

## Purpose

`decision.respond` may need to advance Feature lifecycle state, but neither an AI-client adapter nor the Operator Store is allowed to edit the authoritative Feature Manifest directly. The production path must remain:

```text
Human response
  -> bounded Decision validation
  -> trusted Decision outcome translation
  -> exact Feature Event inbox file
  -> existing trusted Event/Persist machinery
  -> authoritative Feature Manifest
```

The files in this workstream deliberately separate transport power from the adapter-visible authority boundary.

## Layer 1 — create-only GitHub Event transport

`operator_github_feature_event_gateway.py` is low-level trusted infrastructure.

It may:

- GET an exact Feature Manifest;
- GET an exact Event inbox file;
- create `events/inbox/<event-id>.yaml` once;
- observe the trusted Persist receipt through `applied_events` and revision.

It never PUTs `state/features/*.yaml`.

It treats ambiguous GitHub write outcomes as a recovery problem, not permission to retry blindly. A lost write acknowledgement is followed by exact Event lookup. An existing exact Event converges; the same Event id with different bytes is a conflict.

This low-level class is **not** an AI-client API and must not be injected directly into a model-facing transport.

## Layer 2 — contract and revision fences

`operator_validated_feature_event_gateway.py` validates the Event against the repository Feature Event schema before any write.

`operator_exact_feature_event_gateway.py` additionally requires the current trusted Feature revision to equal the Event's expected revision before create. If another Event advances the Feature while this Event remains unapplied, the pending Event becomes `STALE_REVISION` instead of remaining retryable.

Trusted Persist still performs its own normal validation. These checks are defense in depth and reduce stale inbox artifacts; they do not replace lifecycle authority.

## Layer 3 — server-owned repository/ref scope

`operator_configured_feature_event_gateway.py` is the minimum layer suitable for a production Decision gateway.

Trusted installation/service configuration supplies:

- exactly one target repository;
- a one-to-one Feature id -> target ref map.

The public configured methods accept no repository or target-ref argument. A Decision path can request only a configured Feature id; it cannot redirect an Event to another repository/branch.

## Decision-specific layer still required

The next slice must implement the accepted `DecisionFeatureTruthGateway` contract above the configured gateway. It must not expose a generic `event` parameter to AI clients.

A trusted Decision outcome translator must derive the only allowed Feature Event from:

- the durable Decision id/type;
- exact Operation id/generation;
- expected Feature revision and trusted ref;
- candidate head when applicable;
- allowed choice from the durable Decision;
- trusted policy reference/digest;
- responder identity and expiry checks already enforced by DecisionCoordinator.

The translator then submits that bounded Event through the configured exact gateway and returns the exact trusted Persist receipt.

## Evidence boundary

Deterministic tests in this workstream prove transport/fencing behavior only. They do not prove:

- a particular Human Decision outcome;
- release-level dogfood;
- external Worker effect safety;
- Human/Product Acceptance;
- a Gate PASS.

Release evidence must come from a real Decision response through the approved adapter and the trusted default-branch runtime after all prerequisite PRs are merged.
