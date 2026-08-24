#!/usr/bin/env python3
"""Read-only final Issue #221 ledger aggregation for trusted-main live evidence.

This module performs no Worker dispatch, protected Store mutation, Feature Event
write, or fixture mutation. It selects exactly one immutable successful artifact
for each closed live producer on the exact trusted-main installation, downloads
those artifacts read-only, and feeds their exact evidence bytes/provenance into
the existing closed authority-set ledger.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Any, Callable, Iterable
from urllib import error, parse, request
import zipfile
import io

from v03_effect_safety_live_ledger import REQUIRED_SCENARIOS
from v03_effect_safety_live_ledger_authority_set import (
    ReleaseAuthoritySet,
    evaluate_issue_221_authority_set,
)
from v03_real_runtime_live_authority import require_trusted_main_execution

_SHA40 = re.compile(r"^[0-9a-f]{40}$")
OUTPUT_DIR = Path("evidence/v03-final-live-ledger")


class V03FinalLiveLedgerError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProducerSpec:
    workflow_file: str
    artifact_name: str
    evidence_name: str
    provenance_name: str
    scenarios: tuple[str, ...]
    authority_set_name: str | None = None


@dataclass(frozen=True)
class SelectedArtifact:
    workflow_file: str
    artifact_name: str
    artifact_id: int
    workflow_run_id: int
    scenarios: tuple[str, ...]


ORIGINAL_SEQUENCE = ProducerSpec(
    workflow_file="v03-live-lost-ack-persist-sequence.yml",
    artifact_name="v03-live-lost-ack-persist-sequence",
    evidence_name="v03-live-persist-ack-loss.json",
    provenance_name="v03-live-lost-ack-persist-provenance.json",
    scenarios=("lost-ack-crash-takeover", "persist-ack-loss-recovery"),
)
LAUNCH_CANCEL = ProducerSpec(
    workflow_file="v03-live-launch-cancel-pair.yml",
    artifact_name="v03-live-launch-cancel-pair",
    evidence_name="v03-live-launch-cancel-pair.json",
    provenance_name="v03-live-launch-cancel-provenance.json",
    scenarios=(
        "cancellation-before-launch-authorization",
        "launch-authorization-before-cancellation",
    ),
)
DISPATCH_RECOVERY_SCENARIOS = (
    "unknown-takeover",
    "concurrent-resume",
    "reservation-committed-pre-authorization-crash-recovery",
)
REMAINING_SIX_SCENARIOS = (
    "cancel-before-persist-linearization",
    "persist-linearized-before-cancel",
    "duplicate-callback",
    "out-of-order-callback",
    "duplicate-worker-completion",
    "stale-candidate-result",
)


def producer_plan() -> tuple[ProducerSpec, ...]:
    rows = [ORIGINAL_SEQUENCE, LAUNCH_CANCEL]
    rows.extend(
        ProducerSpec(
            workflow_file="v03-live-dispatch-recovery-trio.yml",
            artifact_name=f"v03-live-dispatch-recovery-{scenario}",
            evidence_name=f"{scenario}-evidence.json",
            provenance_name=f"{scenario}-provenance.json",
            authority_set_name=f"{scenario}-authority-set.json",
            scenarios=(scenario,),
        )
        for scenario in DISPATCH_RECOVERY_SCENARIOS
    )
    rows.extend(
        ProducerSpec(
            workflow_file="v03-live-remaining-six.yml",
            artifact_name=f"v03-live-remaining-six-{scenario}",
            evidence_name=f"{scenario}-evidence.json",
            provenance_name=f"{scenario}-provenance.json",
            authority_set_name=f"{scenario}-authority-set.json",
            scenarios=(scenario,),
        )
        for scenario in REMAINING_SIX_SCENARIOS
    )
    return tuple(rows)


def validate_closed_plan(plan: Iterable[ProducerSpec]) -> tuple[ProducerSpec, ...]:
    rows = tuple(plan)
    if len(rows) != 11:
        raise V03FinalLiveLedgerError("final Issue #221 producer plan must contain exactly 11 records")
    claimed = tuple(scenario for row in rows for scenario in row.scenarios)
    if claimed != tuple(REQUIRED_SCENARIOS):
        if len(claimed) != len(REQUIRED_SCENARIOS) or len(set(claimed)) != len(claimed):
            raise V03FinalLiveLedgerError("final producer plan contains duplicate/missing scenario rows")
        if set(claimed) != set(REQUIRED_SCENARIOS):
            raise V03FinalLiveLedgerError("final producer plan differs from closed Issue #221 matrix")
    if len({row.artifact_name for row in rows}) != len(rows):
        raise V03FinalLiveLedgerError("final producer plan reuses an artifact name")
    if any(not row.workflow_file.endswith(".yml") or not row.artifact_name for row in rows):
        raise V03FinalLiveLedgerError("final producer plan contains malformed workflow/artifact identity")
    singleton_authority = [row for row in rows if row.authority_set_name]
    if len(singleton_authority) != 9 or any(len(row.scenarios) != 1 for row in singleton_authority):
        raise V03FinalLiveLedgerError("exactly nine #310 singleton authority-set records are required")
    return rows


def _exact_int(value: Any, label: str) -> int:
    if type(value) is not int or value < 1:
        raise V03FinalLiveLedgerError(f"{label} must be a positive integer")
    return value


def select_exact_artifacts(
    *,
    plan: Iterable[ProducerSpec],
    installation_sha: str,
    list_runs: Callable[[str], list[dict[str, Any]]],
    list_artifacts: Callable[[int], list[dict[str, Any]]],
) -> tuple[SelectedArtifact, ...]:
    rows = validate_closed_plan(plan)
    sha = str(installation_sha or "").lower()
    if not _SHA40.fullmatch(sha):
        raise V03FinalLiveLedgerError("exact trusted-main installation SHA is required")
    selections: list[SelectedArtifact] = []
    run_ids: set[int] = set()
    artifact_ids: set[int] = set()

    for spec in rows:
        candidates: list[SelectedArtifact] = []
        runs = list_runs(spec.workflow_file)
        if not isinstance(runs, list):
            raise V03FinalLiveLedgerError(f"workflow run listing is malformed: {spec.workflow_file}")
        for run in runs:
            if not isinstance(run, dict):
                continue
            if (
                run.get("event") != "workflow_dispatch"
                or run.get("status") != "completed"
                or run.get("conclusion") != "success"
                or str(run.get("head_branch") or "") != "main"
                or str(run.get("head_sha") or "").lower() != sha
            ):
                continue
            run_id = run.get("id")
            if type(run_id) is not int or run_id < 1:
                continue
            artifacts = list_artifacts(run_id)
            if not isinstance(artifacts, list):
                raise V03FinalLiveLedgerError(f"artifact listing is malformed for run {run_id}")
            matches = [
                artifact for artifact in artifacts
                if isinstance(artifact, dict)
                and artifact.get("name") == spec.artifact_name
                and artifact.get("expired") is False
                and isinstance(artifact.get("id"), int)
                and artifact["id"] > 0
            ]
            if len(matches) > 1:
                raise V03FinalLiveLedgerError(
                    f"run {run_id} contains ambiguous duplicate artifact {spec.artifact_name}"
                )
            if len(matches) == 1:
                candidates.append(
                    SelectedArtifact(
                        workflow_file=spec.workflow_file,
                        artifact_name=spec.artifact_name,
                        artifact_id=int(matches[0]["id"]),
                        workflow_run_id=run_id,
                        scenarios=spec.scenarios,
                    )
                )
        if len(candidates) != 1:
            raise V03FinalLiveLedgerError(
                f"expected exactly one successful exact-main artifact {spec.artifact_name}; found {len(candidates)}"
            )
        chosen = candidates[0]
        if chosen.workflow_run_id in run_ids:
            raise V03FinalLiveLedgerError("one workflow run cannot satisfy multiple final evidence records")
        if chosen.artifact_id in artifact_ids:
            raise V03FinalLiveLedgerError("one artifact cannot satisfy multiple final evidence records")
        run_ids.add(chosen.workflow_run_id)
        artifact_ids.add(chosen.artifact_id)
        selections.append(chosen)

    if len(selections) != 11 or len(run_ids) != 11:
        raise V03FinalLiveLedgerError("final evidence selection must bind exactly 11 distinct workflow runs")
    return tuple(selections)


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHubReadApi:
    def __init__(self, *, repository: str, token: str, api_base: str = "https://api.github.com"):
        self.repository = str(repository or "").lower()
        self.token = str(token or "")
        self.api_base = str(api_base or "").rstrip("/")
        if "/" not in self.repository or not self.token or not self.api_base.startswith("https://"):
            raise V03FinalLiveLedgerError("GitHub read authority is incomplete")
        self._opener = request.build_opener()

    def _json(self, url: str) -> dict[str, Any]:
        req = request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "ai-sdlc-v03-final-live-ledger",
            },
            method="GET",
        )
        try:
            with self._opener.open(req, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise V03FinalLiveLedgerError(f"GitHub read failed: {url}") from exc
        if not isinstance(payload, dict):
            raise V03FinalLiveLedgerError(f"GitHub response is not an object: {url}")
        return payload

    def list_runs(self, workflow_file: str) -> list[dict[str, Any]]:
        workflow = parse.quote(workflow_file, safe="")
        url = (
            f"{self.api_base}/repos/{self.repository}/actions/workflows/{workflow}/runs"
            "?event=workflow_dispatch&branch=main&status=completed&per_page=100&page=1"
        )
        payload = self._json(url)
        rows = payload.get("workflow_runs")
        if not isinstance(rows, list):
            raise V03FinalLiveLedgerError(f"workflow run response malformed: {workflow_file}")
        if int(payload.get("total_count") or 0) > 100:
            raise V03FinalLiveLedgerError(
                f"workflow run search exceeds bounded first-page authority: {workflow_file}"
            )
        return rows

    def list_artifacts(self, run_id: int) -> list[dict[str, Any]]:
        run_id = _exact_int(run_id, "workflow run id")
        url = (
            f"{self.api_base}/repos/{self.repository}/actions/runs/{run_id}/artifacts"
            "?per_page=100&page=1"
        )
        payload = self._json(url)
        rows = payload.get("artifacts")
        if not isinstance(rows, list):
            raise V03FinalLiveLedgerError(f"artifact response malformed for run {run_id}")
        if int(payload.get("total_count") or 0) > 100:
            raise V03FinalLiveLedgerError(f"artifact listing exceeds bounded first page for run {run_id}")
        return rows

    def download_artifact(self, artifact_id: int) -> bytes:
        artifact_id = _exact_int(artifact_id, "artifact id")
        url = f"{self.api_base}/repos/{self.repository}/actions/artifacts/{artifact_id}/zip"
        req = request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "ai-sdlc-v03-final-live-ledger",
            },
            method="GET",
        )
        no_redirect = request.build_opener(_NoRedirect)
        location = ""
        try:
            with no_redirect.open(req, timeout=30) as response:
                if response.status not in {301, 302, 303, 307, 308}:
                    raise V03FinalLiveLedgerError(
                        f"artifact archive endpoint did not return a signed redirect: HTTP {response.status}"
                    )
                location = str(response.headers.get("Location") or "")
        except error.HTTPError as exc:
            if exc.code not in {301, 302, 303, 307, 308}:
                raise V03FinalLiveLedgerError(
                    f"artifact archive request failed: HTTP {exc.code}"
                ) from exc
            location = str(exc.headers.get("Location") or "")
        except V03FinalLiveLedgerError:
            raise
        except Exception as exc:
            raise V03FinalLiveLedgerError("artifact archive request failed") from exc

        if not location.startswith("https://"):
            raise V03FinalLiveLedgerError("artifact archive redirect is not HTTPS")
        follow = request.Request(
            location,
            headers={"User-Agent": "ai-sdlc-v03-final-live-ledger"},
            method="GET",
        )
        try:
            with request.urlopen(follow, timeout=60) as response:
                raw = response.read()
        except Exception as exc:
            raise V03FinalLiveLedgerError("artifact archive signed download failed") from exc
        if not raw.startswith(b"PK"):
            raise V03FinalLiveLedgerError("artifact archive is not a ZIP payload")
        return raw


def _zip_member(archive: bytes, basename: str) -> bytes:
    try:
        with zipfile.ZipFile(io.BytesIO(archive), "r") as bundle:
            matches = [
                name for name in bundle.namelist()
                if not name.endswith("/") and PurePosixPath(name).name == basename
            ]
            if len(matches) != 1:
                raise V03FinalLiveLedgerError(
                    f"artifact must contain exactly one {basename}; found {len(matches)}"
                )
            return bundle.read(matches[0])
    except V03FinalLiveLedgerError:
        raise
    except Exception as exc:
        raise V03FinalLiveLedgerError("live evidence artifact ZIP is invalid") from exc


def _json_bytes(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise V03FinalLiveLedgerError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise V03FinalLiveLedgerError(f"{label} is not a JSON object")
    return value


def aggregate_selected_artifacts(
    *,
    plan: Iterable[ProducerSpec],
    selections: Iterable[SelectedArtifact],
    download_artifact: Callable[[int], bytes],
    installation_sha: str,
    evaluator: Callable[..., dict[str, Any]] = evaluate_issue_221_authority_set,
) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = validate_closed_plan(plan)
    chosen = tuple(selections)
    if len(chosen) != len(rows):
        raise V03FinalLiveLedgerError("selection count differs from closed producer plan")
    by_name = {row.artifact_name: row for row in chosen}
    if len(by_name) != len(chosen):
        raise V03FinalLiveLedgerError("selected artifacts contain duplicate names")
    sha = str(installation_sha or "").lower()
    if not _SHA40.fullmatch(sha):
        raise V03FinalLiveLedgerError("exact trusted-main installation SHA is required")

    evidence: list[tuple[bytes, dict[str, Any], dict[str, Any]]] = []
    authority_docs: list[dict[str, Any]] = []
    selection_rows: list[dict[str, Any]] = []

    for spec in rows:
        selected = by_name.get(spec.artifact_name)
        if selected is None or selected.workflow_file != spec.workflow_file or selected.scenarios != spec.scenarios:
            raise V03FinalLiveLedgerError(f"selection identity drifted: {spec.artifact_name}")
        archive = download_artifact(selected.artifact_id)
        raw_evidence = _zip_member(archive, spec.evidence_name)
        raw_provenance = _zip_member(archive, spec.provenance_name)
        evidence_doc = _json_bytes(raw_evidence, spec.evidence_name)
        provenance_doc = _json_bytes(raw_provenance, spec.provenance_name)
        provenance_run_id = provenance_doc.get("github_workflow_run_id")
        if type(provenance_run_id) is not int or provenance_run_id != selected.workflow_run_id:
            raise V03FinalLiveLedgerError(
                f"artifact/provenance workflow run binding differs: {spec.artifact_name}"
            )
        if spec.authority_set_name:
            authority_doc = _json_bytes(
                _zip_member(archive, spec.authority_set_name),
                spec.authority_set_name,
            )
            authority_docs.append(authority_doc)
        evidence.append((raw_evidence, evidence_doc, provenance_doc))
        selection_rows.append({
            **asdict(selected),
            "scenarios": list(selected.scenarios),
            "evidence_name": spec.evidence_name,
            "provenance_name": spec.provenance_name,
            "evidence_sha256": hashlib.sha256(raw_evidence).hexdigest(),
            "provenance_sha256": hashlib.sha256(raw_provenance).hexdigest(),
        })

    if len(authority_docs) != 9:
        raise V03FinalLiveLedgerError("final aggregation requires exactly nine #310 authority-set documents")
    canonical = json.dumps(authority_docs[0], sort_keys=True, separators=(",", ":"))
    if any(json.dumps(row, sort_keys=True, separators=(",", ":")) != canonical for row in authority_docs[1:]):
        raise V03FinalLiveLedgerError("scenario authority-set documents differ across live runs")
    authority_set = ReleaseAuthoritySet.from_document(authority_docs[0])
    if authority_set.trusted_main_head_sha != sha:
        raise V03FinalLiveLedgerError("final authority set is not bound to exact current trusted main")

    ledger = evaluator(authority_set=authority_set, evidence=evidence)
    expected_satisfied = list(REQUIRED_SCENARIOS)
    if (
        not isinstance(ledger, dict)
        or ledger.get("status") != "PASS"
        or ledger.get("overall_issue_221_pass") is not True
        or ledger.get("satisfied_scenarios") != expected_satisfied
        or ledger.get("unresolved_scenarios") != []
        or ledger.get("accepted_record_count") != 11
        or ledger.get("accepted_workflow_run_count") != 11
        or ledger.get("deterministic_evidence_accepted") is not False
    ):
        raise V03FinalLiveLedgerError("closed authority-set ledger did not reach exact 13-row PASS")

    selection_doc = {
        "schema_version": "ai-sdlc.v03-effect-safety-final-selection/v1",
        "issue": 221,
        "trusted_main_head_sha": sha,
        "record_count": 11,
        "scenario_count": 13,
        "workflow_run_ids": [row["workflow_run_id"] for row in selection_rows],
        "records": selection_rows,
        "authority_set_sha256": hashlib.sha256(
            (canonical + "\n").encode("utf-8")
        ).hexdigest(),
        "release_eligible": True,
    }
    return selection_doc, ledger


def _checkout_sha() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise V03FinalLiveLedgerError("cannot resolve exact final-ledger checkout HEAD")
    return completed.stdout.strip()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def main() -> None:
    plan = validate_closed_plan(producer_plan())
    execution = require_trusted_main_execution(
        event_name=str(os.environ.get("GITHUB_EVENT_NAME") or ""),
        ref=str(os.environ.get("GITHUB_REF") or ""),
        repository=str(os.environ.get("GITHUB_REPOSITORY") or ""),
        workflow_sha=str(os.environ.get("GITHUB_SHA") or ""),
        checkout_sha=_checkout_sha(),
    )
    token = str(os.environ.get("AI_SDLC_ACTIONS_READ_TOKEN") or "")
    api = GitHubReadApi(
        repository=execution.repository,
        token=token,
        api_base=str(os.environ.get("GITHUB_API_URL") or "https://api.github.com"),
    )
    selections = select_exact_artifacts(
        plan=plan,
        installation_sha=execution.installation_commit_sha,
        list_runs=api.list_runs,
        list_artifacts=api.list_artifacts,
    )
    selection_doc, ledger = aggregate_selected_artifacts(
        plan=plan,
        selections=selections,
        download_artifact=api.download_artifact,
        installation_sha=execution.installation_commit_sha,
    )
    _write_json(OUTPUT_DIR / "v03-effect-safety-final-selection.json", selection_doc)
    _write_json(OUTPUT_DIR / "v03-effect-safety-final-ledger.json", ledger)
    print(json.dumps({
        "issue": 221,
        "status": ledger["status"],
        "trusted_main_head_sha": execution.installation_commit_sha,
        "accepted_record_count": ledger["accepted_record_count"],
        "accepted_workflow_run_count": ledger["accepted_workflow_run_count"],
        "satisfied_scenarios": ledger["satisfied_scenarios"],
        "overall_issue_221_pass": ledger["overall_issue_221_pass"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
