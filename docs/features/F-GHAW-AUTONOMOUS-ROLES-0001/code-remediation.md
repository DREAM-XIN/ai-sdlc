# Code Remediation — F-GHAW-AUTONOMOUS-ROLES-0001

Task: `F-GHAW-AUTONOMOUS-ROLES-0001-CODE-REMEDIATION-1`

Source finding: `CR-MAJOR-1` in `code-review.md`

Role: Developer remediation

## Scope

This remediation is intentionally bounded to Gate-result provenance. It does not alter the autonomous role set, provider routing, candidate SHA semantics, Reviewer/QA read-only capability boundary, Gate verdict translation, Developer behavior, merge authority, or release authority.

## Changes

### 1. Trusted role-worker run identity

`render_gh_aw_role_workers.py` now renders each Reviewer/QA worker with a deterministic Actions `run-name` containing:

- Feature id;
- task id derived from the trusted `task_payload` via `fromJSON(inputs.task_payload).task.id`;
- expected Manifest revision;
- immutable candidate SHA.

The role-worker conclusion forwards only trusted runtime context for provenance:

- trusted task id derived from `task_payload`;
- `github.run_id`;
- `github.workflow_ref`;
- existing feature/revision/role/stage/candidate identity;
- Safe Output comment id/url.

No target command or model field selects these values.

### 2. Trusted collector provenance validation

`ai-sdlc-gh-aw-gate-result.yml` now requires `task_id`, `source_run_id`, and `source_workflow_ref` and grants only control-repository `actions: read` for source-run inspection.

Before reading the Safe Output recommendation, the collector:

1. fetches the exact Actions run from the trusted control repository;
2. validates the supplied run id against returned run metadata;
3. requires `workflow_dispatch` and trusted default-branch execution;
4. validates the run workflow path against `role-workers.yaml` for the exact `(role, stage)`;
5. validates `github.workflow_ref` against that exact registered workflow path/default branch;
6. validates the run display title against the trusted Feature/task/revision/candidate tuple.

The result envelope's `task_id` must then equal the trusted task id supplied by the role-worker conclusion. Bot author type alone is no longer sufficient provenance.

### 3. Deterministic fail-closed regression

Added `gh_aw_gate_provenance.py` and `validate_gh_aw_gate_provenance.py`.

Negative fixtures reject:

- wrong task id;
- wrong source run id;
- wrong source workflow ref;
- a non-role-worker workflow path;
- a run from another repository;
- missing run metadata / Bot-comment-only provenance;
- a run title that is not bound to the trusted Feature/task/revision/candidate tuple.

`validate_gh_aw_gate_worker_security.py` invokes these provenance fixtures as part of the standard Protocol validation and also verifies all four generated role-worker sources carry the trusted task/run/workflow fields while retaining their no-code-write boundaries.

### 4. Deterministic materialization

The four Gate-role worker sources and strict lock workflows were regenerated through the dedicated `gh-aw/compile-*` materialization path using pinned `github/gh-aw v0.83.4 --strict`; generated locks were not hand-edited.

## CI evidence

Remediation code candidate `ab9bb4add54e40fdb9aea01a9a08e8a599c7e062`:

- Validate AI-SDLC protocol: run `31318702922` — SUCCESS;
- Validate Public Runtime Distribution: run `31318702917` — SUCCESS;
- Required PR Gate: run `31318702913` — SUCCESS;
- Validate AI-SDLC gh-aw Worker Compile: run `31318702960` — SUCCESS.

The compile workflow covers the existing eight engine/profile workers plus the four autonomous Gate-role workers.

## Developer conclusion

CR-MAJOR-1 is remediated in implementation and deterministic tests. This Developer evidence does **not** approve `code-gate`; independent Code Reviewer v2 must re-read the final PR head and decide whether the provenance boundary is sufficient.
