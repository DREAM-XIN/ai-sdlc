# Plan — F-GHAW-AUTONOMOUS-ROLES-0001

## Goal

Implement bounded autonomous Code Reviewer and Verification QA gh-aw execution without moving Feature/Gate authority into the worker, while preserving the existing Developer path, role-aware provider routing, immutable candidate identity, and manual lifecycle fallbacks.

## Work units

### WU-1 — Trusted implementation candidate records

Scope:

- extend the trusted Developer result/conclusion path so autonomous implementation completion records a deterministic draft implementation candidate artifact;
- resolve the Safe Output PR through trusted GitHub identity and bind it to immutable PR head SHA evidence;
- add a shared candidate resolver that supports manual and autonomous implementation artifacts without hard-coded ids;
- preserve/supersede historical draft candidates when a later candidate replaces them.

Likely files:

- `scripts/gh_aw_adapter.py`;
- new `scripts/gh_aw_candidate.py`;
- `.github/workflows/ai-sdlc-gh-aw-worker.md`;
- `.github/workflows/ai-sdlc-gh-aw-result.yml`;
- deterministic candidate tests.

Definition of Done:

- autonomous Developer result creates exactly one current draft implementation candidate;
- PR/head mismatch fails closed;
- manual `implementation-v1` remains compatible;
- zero/multiple/ambiguous candidate resolution fails closed;
- old candidate history is preserved.

### WU-2 — Exact autonomous role dispatch policy

Scope:

- generalize the current Developer-only gh-aw dispatch policy to exact role+stage autonomous routes;
- retain manual fallback for all out-of-scope role/stage pairs;
- add trusted `code-review` and `verification` task templates.

Required routes:

- `developer + implementation` -> gh-aw/autonomous;
- `reviewer + code-review` -> gh-aw/autonomous;
- `qa + verification` -> gh-aw/autonomous.

Negative assertions:

- `reviewer + requirement-review` remains manual;
- `reviewer + design-review` remains manual;
- Product/Architect/Orchestrator/Acceptance remain manual.

Definition of Done:

- Runtime Router deterministically selects only the approved autonomous set;
- no role-only reviewer rule can accidentally automate Requirement/Design Review.

### WU-3 — Role-worker registry and resolver

Scope:

- add strict trusted `runtimes/gh-aw/role-workers.yaml`;
- add parser/validator/resolver for exact `(role, stage, profile)` worker identity;
- atomically cross-check Provider Registry and profile-routing policy.

Initial worker variants:

- reviewer/code-review/claude;
- reviewer/code-review/copilot;
- qa/verification/gemini;
- qa/verification/copilot.

Definition of Done:

- unknown/duplicate/malformed entries fail closed;
- profiles must be registered and allowed by the corresponding role-routing rule;
- no entries exist for Product, Architect, Requirement Review, Design Review or Acceptance;
- source/workflow identity is canonical and unique.

### WU-4 — Deterministic Gate-role worker generation and strict compile

Scope:

- add renderer/materializer for Reviewer and QA role-worker source variants;
- generate four source workflows from shared role templates plus trusted Registry metadata;
- strict compile with pinned gh-aw compiler;
- include role-worker locks in CI compilation/validation.

Gate-role workers must:

- checkout exact trusted candidate SHA read-only;
- read approved Feature context and PR/CI evidence;
- never edit source/lifecycle state;
- expose only bounded non-code result Safe Output;
- emit exactly one non-authoritative result comment/envelope for trusted collection.

Design Review MINOR requirement:

- static validator must prove compiled/source Gate workers contain no `create-pull-request`, `push-to-pull-request-branch`, or equivalent code-writing Safe Output/capability;
- result target must be the trusted candidate PR/repository.

Definition of Done:

- all four role-worker variants strict-compile;
- security validator positive/negative fixtures prove write capability guard.

### WU-5 — Reviewer and QA result schemas

Scope:

- add strict `reviewer-result.schema.json` and `qa-result.schema.json`;
- reject additional/unsupported fields;
- require trusted identity and candidate fields.

Reviewer closed verdict set:

- `PASS`;
- `REWORK`;
- `BLOCKED`.

QA closed verdict set:

- `PASS`;
- `FAIL`;
- `BLOCKED`.

Both schemas include:

- contract/version;
- feature/task/stage/role;
- expected revision;
- target repository/ref;
- candidate PR/head SHA;
- occurred-at;
- bounded evidence structures;
- no secret-bearing fields.

Definition of Done:

- malformed verdict, wrong role/stage, unknown fields and incomplete evidence fixtures fail closed.

### WU-6 — Trusted Gate-result translator and candidate enforcement

Scope:

- add a dedicated Gate-role translator separate from generic Developer `result_to_event()`;
- resolve current candidate artifact and immutable head;
- produce only approved deterministic Feature Event changes.

Reviewer PASS translation:

- review Evidence pass;
- reviewed-candidate identity record;
- approve resolved current draft implementation candidate artifact;
- code-gate PASS;
- code-review DONE;
- verification READY.

Reviewer REWORK translation:

- review Evidence fail;
- leave code-gate PENDING;
- create bounded Developer remediation task with source_stage code-review and trusted target PR context;
- require later independent re-review.

QA PASS translation:

- verification Evidence pass;
- verification-gate PASS;
- verification DONE;
- acceptance READY.

QA FAIL/BLOCKED translation:

- persist truthful failure/block Evidence;
- do not advance Acceptance;
- create remediation only through explicit valid lifecycle semantics where required.

Definition of Done:

- Gate-role generic `COMPLETED => stage DONE` path is impossible;
- stale revision or candidate head mismatch fails closed;
- Reviewer/QA cannot emit release-gate/merge authority.

### WU-7 — Trusted Gate-result collector workflow

Scope:

- add `.github/workflows/ai-sdlc-gh-aw-gate-result.yml` or equivalent;
- accept only a trusted transport envelope identifying target repo/ref, role worker run and Safe Output comment;
- mint bounded Runtime App token;
- fetch exact candidate PR/comment;
- extract machine result envelope;
- validate author/workflow marker, contract, role/stage/revision/candidate;
- call Gate-role translator;
- pass resulting Event through `ingest_feature_event.py`, Manifest validator and trusted Persist.

Definition of Done:

- worker comment by itself has zero lifecycle authority;
- wrong comment/repo/PR/head/workflow identity fails closed;
- result persistence is Event-sourced and revision checked.

### WU-8 — Same-repo and cross-repo gateway integration

Scope:

- generalize current autonomous gateway from Developer-only work to approved autonomous stage set;
- after Runtime Router, use existing profile routing then role-worker resolver;
- for Gate roles, resolve candidate artifact/head before dispatch;
- pass immutable candidate identity to worker;
- include candidate SHA in Gate-role semantic dispatch identity/lease;
- keep target Issue Comment selector-neutral.

Definition of Done:

- Developer remains compatible;
- Reviewer routes `claude -> copilot`;
- QA routes `gemini -> copilot`;
- candidate A run cannot be reused for candidate B;
- target cannot choose role/profile/model/worker/verdict.

### WU-9 — Security and lifecycle regression suite

Required deterministic cases:

1. exact autonomous role set;
2. manual Requirement/Design Reviewer negative fixtures;
3. implementation candidate creation/resolution/supersession;
4. Reviewer PASS;
5. Reviewer REWORK -> Developer remediation task;
6. Reviewer stale revision/wrong role-stage/wrong candidate/malformed result;
7. QA PASS;
8. QA FAIL/BLOCKED no Acceptance advance;
9. QA stale/moved candidate;
10. no Gate-role source-write Safe Output;
11. result comment non-authority;
12. target selector rejection;
13. cross-repo identity/candidate SHA binding;
14. existing Developer implementation/result path;
15. existing role routing;
16. Provider Registry/preflight/effective-model/command-boundary;
17. existing Feature transition/remediation lifecycle;
18. public runtime distribution/action/workflow security.

All tests must be integrated into the standard repository validation path.

### WU-10 — Documentation and durable implementation evidence

Update user/operator docs:

- role guide;
- autonomous/cross-repository execution docs;
- troubleshooting where needed.

Document clearly:

- Reviewer/QA are autonomous execution roles but not direct Gate-state writers;
- logical independence and remediation/re-review behavior;
- role/provider routing;
- static credential fallback versus runtime failure;
- immutable candidate binding;
- manual fallback;
- Product Acceptance remains manual.

Produce Feature implementation evidence mapping work units and Requirement ACs to tests/CI.

## Implementation order

1. WU-1 candidate records;
2. WU-2 dispatch policy;
3. WU-3 role-worker registry;
4. WU-4 worker generation/strict compile;
5. WU-5 result schemas;
6. WU-6 translator;
7. WU-7 collector;
8. WU-8 gateway integration;
9. WU-9 regressions;
10. WU-10 docs/evidence.

WU-1 precedes Gate dispatch because no autonomous Reviewer/QA may run without a trusted immutable candidate. WU-3 precedes WU-4. WU-5 precedes WU-6/7. WU-6/7 must be validated before WU-8 enables production autonomous routes.

## Code Review checkpoints

Independent Code Review must explicitly check:

- Design Review MINOR static no-write capability guard;
- no role-only Reviewer auto-route;
- generic Developer result cannot complete Gate stages;
- candidate artifact/head binding and supersession;
- Reviewer REWORK cannot self-fix or self-pass;
- QA cannot advance release-gate;
- target selectors remain closed;
- trusted collector, not worker, owns Event construction;
- all new workflows use bounded credentials and pinned actions/compiler.

## Verification checkpoints

QA for this Feature must independently prove:

- all Requirement ACs;
- exact role-routing behavior;
- role independence/write boundaries;
- Gate translation positive and negative cases;
- candidate head movement fail-closed behavior;
- existing Developer flow;
- eight existing profile workers plus four new Gate-role variants strict compile;
- final required CI on final lifecycle candidate.

## Definition of Done

Implementation is complete only when:

- all WUs are complete;
- Reviewer and QA autonomous paths are usable through trusted gateway on approved role/stages;
- no Gate-role worker has source-write lifecycle authority;
- all deterministic/security regressions pass;
- existing Developer and 8-profile behavior is preserved;
- final required PR checks are green:
  - Validate AI-SDLC protocol;
  - Validate Public Runtime Distribution;
  - Validate AI-SDLC gh-aw Worker Compile;
  - Required PR Gate;
- durable implementation evidence is committed;
- Developer advances only to independent Code Review READY and does not self-PASS code-gate.
