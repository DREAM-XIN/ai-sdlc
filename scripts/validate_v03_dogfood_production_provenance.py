#!/usr/bin/env python3
"""Deterministic adversarial checks for production dogfood provenance."""
from __future__ import annotations

from copy import deepcopy

from v03_dogfood_production_provenance import (
    ProductionDogfoodProvenanceConfig,
    ProductionDogfoodProvenanceVerifier,
)
from v03_dogfood_trusted_provenance import DogfoodProvenanceVerificationError

REPOSITORY = "dream-xin/ai-sdlc"
HEAD = "a" * 40
RUN_ID = 7001
ADAPTER_ID = "ai-sdlc.openai.responses"
RUNTIME_KIND = "github-actions-gh-aw"
VERIFIER = "ai-sdlc-v03-dogfood-production-provenance"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def record():
    return {
        "repository": REPOSITORY,
        "candidate": {"pr_number": 401, "head_sha": HEAD},
        "adapter": {"adapter_id": ADAPTER_ID},
        "runtime": {
            "runtime_kind": RUNTIME_KIND,
            "receipt_identity": "gh-aw-run:7001:first-attempt",
            "workflow_run_ids": [RUN_ID],
        },
        "provenance": {"verifier_identity": VERIFIER},
        "milestones": [
            {"name": "operation-started", "evidence_categories": ["operation", "persisted_state"]},
            {"name": "developer-completed", "evidence_categories": ["candidate", "runtime_receipt", "persisted_state"]},
        ],
    }


def github_get(url, _headers):
    if url.endswith("/pulls/401"):
        return 200, {
            "state": "open",
            "draft": False,
            "head": {"ref": "dogfood/v0.3-happy", "sha": HEAD, "repo": {"full_name": REPOSITORY}},
            "base": {"ref": "main", "repo": {"full_name": REPOSITORY}},
        }
    if url.endswith(f"/actions/runs/{RUN_ID}"):
        return 200, {
            "event": "workflow_dispatch",
            "conclusion": "success",
            "head_sha": HEAD,
            "repository": {"full_name": REPOSITORY},
        }
    return 404, {"message": "not found"}


def runtime_resolver(_record):
    return {"receipt_identity": "gh-aw-run:7001:first-attempt", "workflow_run_ids": [RUN_ID]}


def milestone_resolver(_record):
    return {
        "operation-started": {"operation", "persisted_state"},
        "developer-completed": {"candidate", "runtime_receipt", "persisted_state"},
    }


def verifier(**overrides):
    return ProductionDogfoodProvenanceVerifier(
        config=ProductionDogfoodProvenanceConfig(
            repository=REPOSITORY,
            verifier_identity=VERIFIER,
            supported_adapter_id=ADAPTER_ID,
            runtime_kind=RUNTIME_KIND,
            github_token="test-token",
            github_api_base="https://api.github.test",
        ),
        runtime_receipt_resolver=overrides.get("runtime_receipt_resolver", runtime_resolver),
        milestone_resolver=overrides.get("milestone_resolver", milestone_resolver),
        http_get=overrides.get("http_get", github_get),
    )


def expect_failure(candidate, *, subject=None, label):
    try:
        (subject or verifier()).verify(candidate)
    except DogfoodProvenanceVerificationError:
        return
    raise AssertionError(f"{label} unexpectedly passed production provenance")


def main():
    base = record()
    attestation = verifier().verify(base)
    require(attestation.repository == REPOSITORY, "repository attestation drifted")
    require(attestation.candidate_head_sha == HEAD, "candidate attestation drifted")
    require([row.run_id for row in attestation.workflow_runs] == [RUN_ID], "workflow run attestation drifted")
    require(attestation.receipt_identity == base["runtime"]["receipt_identity"], "receipt attestation drifted")
    require(attestation.milestone_evidence_categories == milestone_resolver(base), "milestone attestation drifted")
    require(verifier().test_only is False, "production verifier became test-only")

    wrong_repo = deepcopy(base)
    wrong_repo["repository"] = "dream-xin/other"
    expect_failure(wrong_repo, label="cross-repository record")

    wrong_adapter = deepcopy(base)
    wrong_adapter["adapter"]["adapter_id"] = "fixture.adapter"
    expect_failure(wrong_adapter, label="unsupported adapter")

    wrong_runtime = deepcopy(base)
    wrong_runtime["runtime"]["runtime_kind"] = "fixture"
    expect_failure(wrong_runtime, label="fixture runtime")

    changed_head = deepcopy(base)
    changed_head["candidate"]["head_sha"] = "b" * 40
    expect_failure(changed_head, label="stale candidate head")

    duplicate_run = deepcopy(base)
    duplicate_run["runtime"]["workflow_run_ids"] = [RUN_ID, RUN_ID]
    expect_failure(duplicate_run, label="duplicate workflow run")

    failed_run = verifier(http_get=lambda url, headers: (
        (200, {
            "state": "open", "draft": False,
            "head": {"ref": "dogfood/v0.3-happy", "sha": HEAD, "repo": {"full_name": REPOSITORY}},
            "base": {"ref": "main", "repo": {"full_name": REPOSITORY}},
        }) if "/pulls/" in url else
        (200, {"event": "workflow_dispatch", "conclusion": "failure", "head_sha": HEAD, "repository": {"full_name": REPOSITORY}})
    ))
    expect_failure(base, subject=failed_run, label="failed workflow run")

    wrong_receipt = verifier(runtime_receipt_resolver=lambda _record: {
        "receipt_identity": "other", "workflow_run_ids": [RUN_ID]
    })
    expect_failure(base, subject=wrong_receipt, label="runtime receipt mismatch")

    wrong_run_binding = verifier(runtime_receipt_resolver=lambda _record: {
        "receipt_identity": base["runtime"]["receipt_identity"], "workflow_run_ids": [RUN_ID + 1]
    })
    expect_failure(base, subject=wrong_run_binding, label="runtime receipt run mismatch")

    wrong_milestone = verifier(milestone_resolver=lambda _record: {
        "operation-started": {"operation", "persisted_state"},
        "developer-completed": {"candidate"},
    })
    expect_failure(base, subject=wrong_milestone, label="milestone evidence mismatch")

    wrong_identity = deepcopy(base)
    wrong_identity["provenance"]["verifier_identity"] = "self-attested"
    expect_failure(wrong_identity, label="self-selected verifier identity")


    # Production control workflows execute trusted main, while their input and
    # terminal candidate heads are independently established in the target ref.
    from dataclasses import replace
    from validate_v03_dogfood_evidence import _trusted_provenance_errors
    control = "b" * 40
    bound_record = {**record(), "feature_id": "F-DOGFOOD", "target_ref": "dogfood/v0.3-happy"}
    attestation_uri = f"https://github.com/{REPOSITORY}/actions/runs/{RUN_ID}#attestation"
    bound_record["provenance"] = dict(verifier_identity=VERIFIER, verification_status="VERIFIED", attestation_uri=attestation_uri)
    bound_record["evidence_uris"] = [attestation_uri]
    binding = dict(repository=REPOSITORY, feature_id="F-DOGFOOD", target_ref=bound_record["target_ref"],
                   candidate_pr_number=401, candidate_input_head_sha=HEAD, role="reviewer",
                   workflow="reviewer.yml", external_dispatch_key="key-1")
    run = dict(id=RUN_ID, event="workflow_dispatch", status="completed", conclusion="success",
               head_branch="main", head_sha=control, path=".github/workflows/reviewer.yml",
               run_attempt=1, display_title="AI-SDLC gh-aw key-1", repository={"full_name": REPOSITORY})
    def bound_verifier(*, changed_run=None, changed_binding=None, ancestor=True):
        def get(url, headers):
            if "/actions/runs/" in url: return 200, {**run, **(changed_run or {})}
            if "/compare/" in url:
                return 200, dict(status="ahead" if ancestor else "diverged", behind_by=0 if ancestor else 1,
                                 merge_base_commit={"sha": "c"*40})
            return github_get(url, headers)
        return ProductionDogfoodProvenanceVerifier(
            config=ProductionDogfoodProvenanceConfig(repository=REPOSITORY, verifier_identity=VERIFIER,
                supported_adapter_id=ADAPTER_ID, runtime_kind=RUNTIME_KIND, github_token="test",
                github_api_base="https://api.github.test", installation_commit_sha=control),
            runtime_receipt_resolver=runtime_resolver, milestone_resolver=milestone_resolver, http_get=get,
            runtime_binding_resolver=lambda record: {RUN_ID: {**binding, **(changed_binding or {})}},
        )
    proof = bound_verifier().verify(bound_record)
    require(proof.workflow_runs[0].head_sha == control, "actual Actions source SHA was relabeled")
    require(proof.workflow_runs[0].candidate_head_sha == HEAD, "terminal target SHA was not separately bound")
    errors = _trusted_provenance_errors(bound_record, bound_verifier())
    require(not errors, f"shared validator rejected a verified split-source run: {errors}")
    for changed in ({"head_sha": "d"*40}, {"run_attempt": 2}, {"head_branch": "feature"},
                    {"path": ".github/workflows/untrusted.yml"}, {"display_title": "unrelated"}):
        expect_failure(bound_record, subject=bound_verifier(changed_run=changed), label="control source drift")
    for changed in ({"target_ref": "main"}, {"candidate_pr_number": 402},
                    {"candidate_input_head_sha": None}, {"feature_id": "F-OTHER"}):
        expect_failure(bound_record, subject=bound_verifier(changed_binding=changed), label="target binding drift")
    bound_verifier(changed_binding={"candidate_input_head_sha": "c"*40}).verify(bound_record)
    expect_failure(bound_record, subject=bound_verifier(changed_binding={"candidate_input_head_sha": "c"*40}, ancestor=False),
                   label="diverged historical input")
    class BadProof:
        test_only = False
        def verify(self, record):
            return replace(proof, workflow_runs=(replace(proof.workflow_runs[0], target_ref="main"),))
    require(_trusted_provenance_errors(bound_record, BadProof()), "shared validator accepted target-ref drift")

    print("v0.3 production dogfood provenance verifier: PASS")


if __name__ == "__main__":
    main()
