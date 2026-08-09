# Design — F-GHAW-AUTONOMOUS-AUTHORING-ROLES-0001

## Status

Design v1 for bounded autonomous Product/Requirement, Architect/Design, and Orchestrator/Plan authoring.

## Goals

- Make only `product + requirement`, `architect + design`, and `orchestrator + plan` autonomous.
- Preserve manual Requirement Review, Design Review, and Acceptance.
- Keep authoring agents unable to write repository contents, lifecycle state, Gates, merge, or release directly.
- Reuse trusted profile routing, role-worker provenance, Safe Output, Runtime App, Feature Event, and Persist foundations already merged.

## Trusted architecture

```text
Commander/dispatch
  -> exact role+stage route
  -> trusted profile route
  -> registered authoring role-worker
  -> read-only checkout/context
  -> Safe Output authoring envelope
  -> trusted authoring collector
       - verifies run/workflow/task/revision provenance
       - validates closed result schema
       - derives canonical artifact type/path
       - rejects target/model/path selectors
       - writes canonical document with trusted GitHub credential
       - supersedes prior current draft of same artifact type
       - emits bounded Feature Event
  -> trusted Persist
  -> next independent/manual review or next stage
```

The model never receives repository write credentials and never supplies an arbitrary destination path.

## Exact autonomous scope

Only these exact matches are autonomous:

- `product + requirement`
- `architect + design`
- `orchestrator + plan`

Existing exact routes remain:

- `developer + implementation`
- `reviewer + code-review`
- `qa + verification`

All other role/stage combinations remain manual. In particular `product + acceptance`, `reviewer + requirement-review`, and `reviewer + design-review` are manual.

## Role-aware provider routing

Add deterministic non-experimental rules with Copilot fallback:

- Product/requirement: `claude -> copilot`
- Architect/design: `claude -> copilot`
- Orchestrator/plan: `codex -> copilot`

Profiles are selected only by trusted policy. Target repositories cannot choose provider/model/profile/worker/candidate order/experimental opt-in.

## Authoring result contract

A dedicated closed schema accepts only:

- protocol version;
- feature id;
- trusted task id echoed from the task payload;
- role;
- stage;
- expected revision;
- verdict: `COMPLETED | BLOCKED`;
- artifact body as UTF-8 Markdown;
- bounded summary/notes.

The envelope has no `path`, `changes`, `gate`, `artifact_id`, provider selector, workflow selector, or executable Event payload.

## Canonical artifact mapping

Trusted code derives destination from role+stage:

| role + stage | artifact type | canonical path | next stage |
|---|---|---|---|
| product + requirement | requirement | `docs/features/<feature>/requirement.md` | requirement-review READY |
| architect + design | design | `docs/features/<feature>/design.md` | design-review READY |
| orchestrator + plan | plan | `docs/features/<feature>/plan.md` | implementation READY |

No other destination is accepted.

## Writer boundary

The authoring worker remains read-only and uses Safe Output only. A separate trusted collector/writer:

1. re-reads authoritative Manifest;
2. verifies exact stage is WORKING and expected revision matches;
3. verifies Actions run id, registered workflow path/ref, task id, role/stage and repository scope;
4. validates the authoring envelope;
5. derives the canonical path from trusted mapping;
6. rejects empty/oversized/non-UTF8 payloads and forbidden lifecycle-state paths;
7. writes the document using trusted repository credentials;
8. verifies the resulting commit/path;
9. emits a bounded Event that registers the artifact as draft and completes only the authoring stage.

A worker can never directly PASS a review or release Gate.

## Idempotency and supersession

For each artifact type, at most one non-superseded draft may be current.

- First successful authoring creates `<type>-v1` draft.
- A remediation/retry that replaces content creates the next deterministic version id and supersedes the prior current draft in the same trusted Event.
- Replaying the same trusted `(task_id, expected_revision, source_run_id)` is rejected/idempotent and must not create another version.
- Approved historical versions remain historical; a new remediation draft supersedes only the prior current candidate according to lifecycle rules.
- Reviewers resolve the unique current draft by artifact type rather than a hard-coded id.

## Remediation

Requirement or Design review REWORK creates the existing bounded remediation task. The matching authoring role may run autonomously only when Commander assigns that task to the same artifact-producing stage. The collector preserves the same canonical path and creates a deterministic new draft version while retaining history.

## Safe Output and provenance

Authoring workers use no `create-pull-request`, push, repository-content write, lifecycle write, merge, or release capability. Compiled locks must be statically checked for those capabilities.

Collector provenance must bind:

- repository;
- feature id;
- expected revision;
- trusted task id;
- exact role/stage;
- source Actions run id;
- registered role-worker workflow path/ref;
- trusted control/default ref.

A generic Bot comment or unbound payload is insufficient.

## Compatibility

- Existing manual authoring remains valid fallback.
- Existing autonomous Developer/Reviewer/QA behavior is unchanged.
- Existing Provider Registry and role-routing fail-closed behavior remains unchanged.
- Experimental providers remain excluded from default production routing.
- No new Manifest authority is added.

## Deterministic validation

Tests must prove:

1. exact autonomous scope only;
2. Acceptance and review stages remain manual;
3. role routing and fallback are deterministic/non-experimental;
4. authoring result schema rejects arbitrary path/Event/Gate/provider fields;
5. canonical writer maps only the three approved paths;
6. wrong task/run/workflow/revision/role/stage fails closed;
7. duplicate replay is idempotent/rejected;
8. retry/remediation yields one current draft and supersedes previous current draft;
9. compiled authoring workers contain no repository-write/PR/merge/release Safe Outputs;
10. existing Developer/Reviewer/QA validation remains green;
11. same-repo and cross-repo control boundaries remain green.

## Requirement Review notes closure

- MINOR 1 is closed by the Safe Output payload + trusted canonical writer boundary; agents never select arbitrary repository paths and never receive repository write credentials.
- MINOR 2 is closed by deterministic versioning, provenance replay protection, and unique-current-draft supersession semantics.
