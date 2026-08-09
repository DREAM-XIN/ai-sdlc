# Design — Bounded autonomous Reviewer and QA gh-aw roles

Feature: `F-GHAW-AUTONOMOUS-ROLES-0001`

## 1. Design summary

Extend the existing gh-aw autonomous execution architecture with two **read-only Gate-role worker classes**:

- Code Reviewer (`reviewer + code-review`);
- Verification QA (`qa + verification`).

The design deliberately does not reuse the Developer worker's write-producing Safe Output contract. Reviewer/QA run with read-only agent permissions and emit a bounded structured recommendation through gh-aw Safe Output comments. A trusted control-plane collector retrieves and validates that recommendation, verifies immutable candidate identity, translates the verdict into the existing Feature Event vocabulary, and delegates final state mutation to the existing Event Inbox / transition / optimistic-concurrency / Persist path.

The worker never writes `state/features/**`, `state/events/**`, source code, Gate state, merge state, or release state.

## 2. Existing constraints that drive the design

### 2.1 Current result adapter is Developer-shaped

`gh_aw_adapter.result_to_event()` currently treats a generic `COMPLETED` stage result as `stage DONE`. That behavior remains valid for bounded implementation work but is unsafe for Code Review or Verification because Gate stages require a role-specific verdict plus Evidence and follow-on lifecycle changes.

Gate-role results therefore use separate schemas and a separate trusted translator; they are not accepted by the generic Developer result path.

### 2.2 `reviewer` is a shared lifecycle role id

The standard profile uses `reviewer` for requirement-review, design-review and code-review. Runtime routing must therefore use exact role+stage matches. `match: {role: reviewer}` is forbidden for the new autonomous route because it would accidentally automate Requirement and Design Review.

### 2.3 Current Developer worker is write-producing

The current compiled Developer worker has `create-pull-request` Safe Output and a mandatory Draft PR submission. That worker cannot be reused for independent Gate roles because the agent would retain a code-write request capability and its completion contract would be wrong.

Gate-role workers are separate compiled workflows with read-only checkout/tool permissions and non-code Safe Outputs only.

## 3. Trusted autonomous dispatch policy

Create/replace the autonomous runtime policy with a policy equivalent to:

```yaml
routes:
  - id: gh-aw-autonomous-developer
    priority: 100
    match: {role: developer, stage: implementation}
    runtime: {id: gh-aw, mode: autonomous}

  - id: gh-aw-autonomous-code-reviewer
    priority: 100
    match: {role: reviewer, stage: code-review}
    runtime: {id: gh-aw, mode: autonomous}

  - id: gh-aw-autonomous-qa
    priority: 100
    match: {role: qa, stage: verification}
    runtime: {id: gh-aw, mode: autonomous}
```

Manual routes remain for all roles/stages and act only as lower-priority fallback where no exact autonomous route applies. Deterministic tests explicitly prove:

- requirement-review remains manual;
- design-review remains manual;
- Product/Architect/Orchestrator/Acceptance remain manual;
- code-review and verification alone gain autonomous gh-aw routes.

Task templates for `code-review` and `verification` are copied from the trusted default policy so the gh-aw adapter can build valid Task Packages without broadening role scope.

## 4. Candidate identity model

### 4.1 Trusted candidate artifacts

Reviewer/QA must never derive the candidate solely from mutable Feature branch HEAD or model-provided text.

When the Developer Safe Output PR is created, the trusted Developer conclusion/collector resolves the PR through GitHub and records two durable artifacts through the normal Feature Event path:

- `implementation-pr`: canonical target PR URL;
- `implementation-head`: immutable commit URL containing the exact PR head SHA.

The agent does not choose either identity. The trusted conclusion job obtains the PR number/URL from Safe Output and resolves the head SHA through GitHub API using the bounded target credential.

`gh_aw_adapter.result_to_event()` is extended only for trusted Developer artifacts so completed implementation results add validated `artifact-record` entries. The existing implementation stage semantics remain otherwise unchanged.

### 4.2 Review candidate

Before Reviewer dispatch, the trusted gateway:

1. resolves the implementation PR artifact;
2. queries the PR through the Runtime App;
3. confirms current PR head equals the recorded immutable implementation-head SHA;
4. passes `candidate_pr_number` and `candidate_head_sha` as trusted workflow inputs;
5. checks out that exact SHA for the Reviewer.

If the PR head has moved, dispatch fails closed and requires a new candidate record / implementation result instead of reviewing a silently changed head.

### 4.3 QA candidate

A successful Code Review PASS records a `reviewed-candidate-head` artifact/evidence bound to the exact reviewed SHA.

Before QA dispatch, the trusted gateway confirms:

- current implementation PR head equals `reviewed-candidate-head`;
- the current Manifest revision/stage matches the Verification dispatch;
- the exact candidate SHA is passed to the QA worker and checked out.

If the candidate changed after Code Review, QA does not run against the newer SHA; the flow fails closed and requires the candidate to return through independent Code Review.

This closes Requirement Review MINOR-1.

## 5. Role-worker registry and compiled worker variants

Provider profile selection and role execution identity are separate concerns.

### 5.1 Profile routing remains unchanged

`profile-routing.yaml` continues to select:

- Developer: `codex -> copilot`;
- Reviewer: `claude -> copilot`;
- QA: `gemini -> copilot`.

Experimental profiles remain excluded.

### 5.2 Role-worker registry

Add a trusted, strict `runtimes/gh-aw/role-workers.yaml` mapping exact role/stage/profile triples to compiled worker source/workflow identities.

Initial entries are only:

- reviewer + code-review + claude;
- reviewer + code-review + copilot;
- qa + verification + gemini;
- qa + verification + copilot.

A validator atomically cross-checks:

- role/stage is one of the approved Gate-role pairs;
- profile exists in the Provider Registry;
- profile appears in the corresponding trusted routing rule;
- source/workflow paths are canonical and unique;
- compiled lock metadata matches the selected profile engine/model and pinned compiler;
- no role-worker entry exists for Product/Architect/Requirement Review/Design Review/Acceptance.

The normal profile resolver selects the profile first. A generic role-worker resolver then resolves `(role, stage, profile)` to the exact compiled worker. No provider-name-specific control branches are introduced.

### 5.3 Materialization

Add a deterministic renderer/materializer that creates four role-worker `.md` sources and their strict gh-aw lock workflows from shared role templates plus Registry/profile metadata.

Generated workers are part of the strict compile matrix. Adding a future Gate role/profile must go through the role-worker registry and deterministic renderer rather than hand-coded workflow branches.

## 6. Reviewer worker contract

The Reviewer worker:

- checks out the exact `candidate_head_sha` read-only;
- reads Feature Issue, approved Requirement/Design/Plan, implementation evidence, current candidate diff/PR context, role rubric and required CI evidence;
- may use read-only GitHub tools;
- has no create-PR/push safe output;
- may not edit source or lifecycle state;
- produces exactly one bounded result comment on the trusted candidate PR using gh-aw `add-comment` Safe Output targeted to the exact PR;
- the comment clearly labels itself as `AI-SDLC worker recommendation; not authoritative Gate state`.

The comment contains a hidden machine-readable envelope followed by a human summary.

The machine payload is validated by `reviewer-result.schema.json` and contains:

- version/contract;
- feature/task/stage/role;
- expected revision;
- candidate repository/ref/PR/head SHA;
- verdict: `PASS`, `REWORK`, or `BLOCKED`;
- severity counts;
- findings array with bounded codes/severity/message;
- Evidence records;
- occurred_at.

No arbitrary lifecycle changes are accepted from the agent.

## 7. QA worker contract

The QA worker uses the same read-only architecture but a distinct `qa-result.schema.json`.

It receives the exact reviewed candidate and reads approved artifacts, Code Review evidence, CI and acceptance criteria. It emits one bounded non-authoritative Safe Output comment on the candidate PR.

The QA result contains:

- trusted identity fields;
- verdict: `PASS`, `FAIL`, or `BLOCKED`;
- deterministic command/check records;
- acceptance-criterion coverage records;
- Verification Evidence records;
- occurred_at.

The QA worker cannot emit or request Release Gate state.

## 8. Safe Output transport

Use gh-aw built-in `add-comment` Safe Output with:

- the exact trusted candidate PR as target;
- target repository fixed by workflow inputs/control configuration;
- max 1 result comment;
- no code-writing safe outputs;
- agent job remains read-only.

This follows gh-aw's security model: the agent requests a structured output and a separate permission-controlled job performs the GitHub write.

The role-worker conclusion job does **not** trust the comment as lifecycle authority. It dispatches only a transport envelope to the control collector containing trusted workflow inputs plus the Safe Output comment id/url.

The control collector mints a bounded Runtime App token, fetches the exact comment, verifies repository/PR/comment identity and expected workflow marker/author context, extracts the machine payload, then performs role-specific schema/identity validation.

This closes Requirement Review MINOR-2: Reviewer/QA have no source-write Safe Output and do not create PRs.

## 9. Trusted Gate-role collector

Add a dedicated control workflow such as `ai-sdlc-gh-aw-gate-result.yml` and adapter functions separate from generic Developer result ingestion.

The collector loads current target Manifest and validates:

- Feature id;
- target repository/ref;
- task/stage/role;
- exact expected revision;
- candidate PR number/head SHA;
- current PR head remains equal to candidate SHA;
- result contract/version;
- required Evidence;
- verdict-specific fields;
- no unsupported fields.

The collector then creates a normal Feature Event and sends it through `ingest_feature_event.py` and existing Manifest validation/persistence.

## 10. Reviewer verdict translation

### PASS

A valid Reviewer PASS Event includes only trusted deterministic changes:

- persist review Evidence as pass;
- record reviewed candidate head evidence/artifact;
- approve implementation artifact;
- `code-gate: PASS` with Evidence;
- `code-review: DONE`;
- `verification: READY`.

### REWORK

A valid Reviewer REWORK Event:

- persists failed review Evidence;
- leaves `code-gate` PENDING;
- creates a new bounded Developer remediation task with:
  - stage `implementation`;
  - role `developer`;
  - source_stage `code-review`;
  - actionable feedback derived from validated findings;
  - target PR identity;
  - status `TODO`;
  - runtime eligible for existing Developer execution policy as approved by implementation tests;
- keeps code-review in its lifecycle state required by the existing remediation protocol.

Historical review Evidence remains append-only. After remediation DONE, a fresh independent Reviewer dispatch is mandatory.

### BLOCKED

Persists failure Evidence and blocks/records the current stage only according to existing transition rules; never converts uncertainty into PASS.

## 11. QA verdict translation

### PASS

A valid QA PASS Event:

- persists Verification Evidence as pass;
- records verified candidate SHA;
- `verification-gate: PASS`;
- `verification: DONE`;
- `acceptance: READY`.

### FAIL/BLOCKED

Persists truthful Verification Evidence, keeps `verification-gate` PENDING/FAIL as allowed by existing lifecycle semantics, and does not advance Acceptance.

If implementation remediation is required, the collector creates an explicit bounded Developer remediation task according to transition rules; QA never edits source and self-reverifies within the same result.

## 12. Dispatch gateway changes

The same-repository and cross-repository gateways are generalized from Developer-only to autonomous-stage dispatch while preserving one-dispatch-per-revision semantics.

For each Commander dispatch:

1. Runtime Router decides manual vs `gh-aw/autonomous` using the exact role+stage policy;
2. profile routing resolves a ready profile;
3. role-worker resolver selects the exact worker variant;
4. for Gate roles, candidate artifacts are resolved and immutable SHA checks run;
5. adapter builds a role-appropriate Task Package;
6. START reservation Event is persisted before worker dispatch when required;
7. exact semantic dispatch identity includes Feature/task/stage/role/revision and candidate SHA for Gate-role work;
8. worker result returns through the role-appropriate trusted collector.

The target Issue Comment command remains provider/role/verdict neutral.

## 13. Idempotency and concurrency

Gate-role dispatch keys include the immutable candidate SHA, preventing a queued Reviewer/QA run for candidate A from being mistaken for candidate B.

The existing Feature branch serialization and expected-revision checks remain authoritative. Duplicate queued/in-progress/successful semantic runs suppress redispatch; failed/cancelled runs remain retryable.

A result for a stale revision or changed candidate is rejected even if the underlying model verdict would otherwise be PASS.

## 14. Security boundaries

The Design preserves:

- read-only agent permissions for Gate-role workers;
- no source Safe Output for Reviewer/QA;
- Runtime App target-repository scoping;
- no provider secret serialized to payload/result/evidence;
- target commands cannot choose role/profile/model/provider/worker/candidate/verdict;
- no worker can write Feature Manifest/Event files;
- only trusted collector builds lifecycle Events;
- only trusted Persist writes authoritative state;
- no autonomous Acceptance/merge/release.

## 15. Backward compatibility

- Developer worker/result behavior remains valid.
- Existing Developer role routing remains `codex -> copilot`.
- Existing eight profile Registry/strict locks remain valid.
- Existing manual lifecycle remains available.
- Existing manual profile diagnostics remain trusted-only.
- `copilot` remains global compatibility default.

## 16. Deterministic verification strategy

Add tests for:

1. exact autonomous route set and manual negative fixtures for requirement-review/design-review/acceptance;
2. role-worker registry schema/cross-link validation;
3. strict compile of all four role-worker variants plus existing eight workers;
4. candidate artifact persistence from Developer result;
5. candidate PR/head mismatch fail closed;
6. Reviewer PASS translation;
7. Reviewer REWORK -> bounded Developer remediation task;
8. stale Reviewer result;
9. Reviewer wrong role/stage/candidate/unknown fields/malformed verdict;
10. QA PASS translation;
11. QA FAIL/BLOCKED no Acceptance advance;
12. stale/wrong QA candidate;
13. Reviewer/QA workers contain no code-write Safe Outputs;
14. result comments are non-authoritative and require collector validation;
15. target command selector rejection;
16. cross-repo dispatch idempotency with candidate SHA;
17. existing Developer result/dispatch regressions;
18. existing Provider Registry, role routing, preflight, effective-model, command-boundary, workflow/action security, public runtime and release-readiness suites.

## 17. Implementation sequencing

1. trusted candidate artifact persistence;
2. role-worker registry/validator/renderer;
3. strict role-worker materialization;
4. autonomous role dispatch policy;
5. Gate-role worker Safe Output contracts;
6. reviewer/QA result schemas;
7. trusted Gate-result collector/translator;
8. gateway candidate binding/idempotency;
9. deterministic tests/security validators;
10. docs and end-to-end dry-run/live-bounded dogfood evidence where credentials permit.

## 18. Explicit non-goals

No autonomous Product, Requirement Reviewer, Architect, Design Reviewer, Orchestrator or Acceptance; no new providers; no maturity promotion; no inference-time provider retry; no cost/quality adaptive routing; no worker direct Gate/Event/Manifest authority; no merge/release authority change.
