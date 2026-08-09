# gh-aw trusted profile routing

AI-SDLC supports multiple trusted gh-aw engine/provider profiles, but normal autonomous execution should not require a target repository to choose a provider or model.

This document describes the trusted role-aware routing contract introduced by `F-GHAW-ROLE-ROUTING-0001`.

## Compatibility default versus routed execution

`runtimes/gh-aw/runtime.yaml` keeps:

```yaml
default_engine_profile: copilot
```

That value remains the global compatibility/default profile. It is not a statement that every autonomous task must execute with Copilot.

Normal autonomous gh-aw dispatch uses the trusted routing policy in:

```text
runtimes/gh-aw/profile-routing.yaml
```

The initial policy is:

| Lifecycle context | Preferred profile | Fallback |
| --- | --- | --- |
| Developer / implementation | `codex` | `copilot` |
| Reviewer / code-review | `claude` | `copilot` |
| QA / verification | `gemini` | `copilot` |

Only Developer is autonomous in the current dispatch policy. Reviewer and QA routes are available for deterministic validation/audit and for future autonomous-role work; this routing Feature does not make those roles autonomous.

## Static readiness only

Routing is intentionally non-invasive. It evaluates trusted credential-presence booleans and the exact registered compiled worker identity.

For a Developer task:

1. if Codex is statically ready, select Codex;
2. otherwise, if Copilot is statically ready, select Copilot and record deterministic fallback evidence;
3. otherwise fail closed with no ready candidate.

Credential presence means only that the runtime has the credential material required to attempt the selected worker. It does **not** prove:

- provider entitlement;
- billing or quota;
- model availability;
- endpoint health;
- rate-limit headroom;
- successful inference.

The v1 router does not automatically retry another provider after a live runtime/inference failure. Such retry semantics require a separate reviewed lifecycle Feature.

## Credential-source metadata

Provider Registry entries declare a validated `credential_source` capability.

Current supported values are:

- `secret` — readiness comes from approved repository/control-plane secret presence;
- `github-token` — readiness comes from the trusted GitHub Actions runtime token presence.

The routing resolver does not branch on provider/profile names. Workflow presence expressions are generated from Registry metadata, and Python receives booleans only. Secret values are never serialized into routing input or audit output.

Credential aliases are supported only for `secret`-backed profiles. For example, Codex may be ready through either its primary or Registry-approved alias credential. A `github-token` profile may not define aliases.

## Experimental profiles

The current experimental profiles are not part of the default production routing set.

A trusted routing rule may contain an experimental candidate only when that trusted rule explicitly sets:

```yaml
allow_experimental: true
```

Target repositories, Project Adapters, task payloads, and Issue Comment commands cannot set or override this property.

## Target command boundary

The normal Issue Comment command selects lifecycle context only:

```text
/ai-sdlc dispatch-gh-aw target_ref=<feature-branch> manifest=state/features/<feature>.yaml policy=dispatch/<policy>.yaml
```

The target command cannot select:

- engine profile;
- provider;
- model;
- credential;
- compiled worker;
- candidate ordering;
- `allow_experimental`;
- profile-routing policy path.

The trusted core runtime reads the Commander result, resolves role/stage through the trusted routing policy, and passes only the selected exact Registry worker into the existing dispatch pipeline.

## Manual trusted profile diagnostics

`.github/workflows/ai-sdlc-gh-aw-dispatch-profile.yml` remains available to trusted control-plane operators for explicit profile diagnostics and bounded dogfood.

This is deliberately separate from normal policy routing. Its audit mode is:

```text
manual-trusted-profile
```

Normal role-aware routing emits:

```text
policy
```

A manual diagnostic selection does not modify the routing policy and is not a target-repository fallback mechanism.

## Routing audit

A policy resolution records non-secret data including:

- selection mode;
- policy version;
- routing rule id;
- role and stage;
- ordered candidates evaluated;
- readiness/skip reason for evaluated candidates;
- selected profile, engine, provider, protocol, model and worker workflow;
- whether fallback occurred and the fallback reason;
- `entitlement_verified: false`.

The runtime gateways preserve this routing JSON with the dispatch-plan artifacts and surface it in the workflow summary.

## Failure behavior

Routing fails closed when, among other cases:

- policy YAML is malformed or contains duplicate keys;
- role/stage has no trusted rule;
- a rule references an unregistered profile;
- candidate identities are duplicated;
- an experimental profile is used without trusted opt-in;
- the readiness map is incomplete or contains non-boolean data;
- no candidate is statically ready;
- the selected registered compiled worker is missing or invalid.

Routing does not grant Feature Manifest, Gate, merge, or release authority. Existing Feature Event, trusted Persist, Safe Output, Runtime App, independent Review/QA, and release boundaries remain authoritative.
