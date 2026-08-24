#!/usr/bin/env python3
"""Single-dispatch four-process release sequence for the first two Issue #221 rows.

The workflow invokes this script in four separate Python processes.  The two
fault phases still hard-exit through the reviewed adapters.  Only the final
Persist-recovery process writes the trusted provenance envelope and evaluates the
closed live ledger, which must remain PENDING with exactly two satisfied rows.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Callable

from v03_effect_safety_live_ledger import ReleaseAuthority, evaluate_issue_221
from v03_live_evidence_provenance import write_live_evidence_envelope
from v03_lost_ack_live_runner import (
    FINAL_EVIDENCE as LOST_ACK_EVIDENCE,
    run_phase1 as run_lost_ack_phase1,
    run_phase2 as run_lost_ack_phase2,
)
from v03_persist_ack_loss_live_runner import (
    FINAL_EVIDENCE as PERSIST_EVIDENCE,
    run_phase1 as run_persist_phase1,
    run_phase2 as run_persist_phase2,
)
from v03_real_runtime_driver import assemble_live_preflight

AUTHORITY_EVIDENCE = Path("evidence/v03-effect-safety-live-authority.json")
PROVENANCE_EVIDENCE = Path("evidence/v03-live-lost-ack-persist-provenance.json")
PARTIAL_LEDGER_EVIDENCE = Path("evidence/v03-effect-safety-live-ledger-partial.json")
EXPECTED_SATISFIED = ["lost-ack-crash-takeover", "persist-ack-loss-recovery"]


class V03LiveSequenceError(RuntimeError):
    pass


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _require_pending_lost_ack(evidence: dict[str, Any]) -> None:
    if evidence.get("schema_version") != "ai-sdlc.v03-live-lost-ack/v1":
        raise V03LiveSequenceError("lost-ACK phase2 returned unexpected evidence schema")
    if evidence.get("phase_status") != "PASS" or evidence.get("status") != "PENDING":
        raise V03LiveSequenceError("lost-ACK takeover must be phase PASS but scenario PENDING")
    if evidence.get("overall_issue_221_pass") is not False:
        raise V03LiveSequenceError("lost-ACK takeover phase overclaimed overall Issue #221 PASS")


def run_lost_ack_takeover(*, preflight, phase2_fn: Callable[..., dict[str, Any]] = run_lost_ack_phase2) -> dict[str, Any]:
    evidence = phase2_fn(preflight=preflight)
    _require_pending_lost_ack(evidence)
    return evidence


def finalize_persist_recovery(
    *,
    preflight,
    github_workflow_run_id: object,
    workflow_sha: str,
    phase2_fn: Callable[..., dict[str, Any]] = run_persist_phase2,
    envelope_writer: Callable[..., tuple[dict[str, Any], dict[str, Any]]] = write_live_evidence_envelope,
    ledger_evaluator: Callable[..., dict[str, Any]] = evaluate_issue_221,
    evidence_path: Path = PERSIST_EVIDENCE,
    authority_path: Path = AUTHORITY_EVIDENCE,
    provenance_path: Path = PROVENANCE_EVIDENCE,
    ledger_path: Path = PARTIAL_LEDGER_EVIDENCE,
) -> dict[str, Any]:
    """Finish exact Persist recovery, seal provenance, then prove only 2/13 rows."""
    evidence = phase2_fn(preflight=preflight, final_path=evidence_path)
    if evidence.get("status") != "PASS":
        raise V03LiveSequenceError("Persist ACK-loss final phase did not PASS")
    if evidence.get("completed_issue_221_scenarios") != EXPECTED_SATISFIED:
        raise V03LiveSequenceError("combined result/Persist evidence completed unexpected scenarios")
    if evidence.get("overall_issue_221_pass") is not False:
        raise V03LiveSequenceError("combined two-scenario evidence overclaimed overall Issue #221 PASS")

    authority_document, provenance_document = envelope_writer(
        preflight=preflight,
        evidence_path=evidence_path,
        provenance_path=provenance_path,
        authority_path=authority_path,
        github_workflow_run_id=github_workflow_run_id,
        workflow_sha=workflow_sha,
        record_id=None,
    )
    raw = evidence_path.read_bytes()
    exact_document = json.loads(raw.decode("utf-8"))
    authority = ReleaseAuthority.from_document(authority_document)
    ledger = ledger_evaluator(
        authority=authority,
        evidence=[(raw, exact_document, provenance_document)],
    )
    if ledger.get("status") != "PENDING" or ledger.get("overall_issue_221_pass") is not False:
        raise V03LiveSequenceError("two-scenario live sequence overclaimed overall Issue #221 PASS")
    if ledger.get("satisfied_scenarios") != EXPECTED_SATISFIED:
        raise V03LiveSequenceError("partial ledger satisfied rows differ from the exact two-scenario sequence")
    if len(ledger.get("unresolved_scenarios") or []) != 11:
        raise V03LiveSequenceError("partial ledger does not retain exactly eleven unresolved Issue #221 rows")
    _write_json(ledger_path, ledger)
    return {
        "schema_version": "ai-sdlc.v03-live-lost-ack-persist-sequence/v1",
        "status": "PASS",
        "completed_issue_221_scenarios": list(EXPECTED_SATISFIED),
        "overall_issue_221_pass": False,
        "evidence_path": str(evidence_path),
        "authority_path": str(authority_path),
        "provenance_path": str(provenance_path),
        "partial_ledger_path": str(ledger_path),
        "unresolved_scenario_count": 11,
    }


def _preflight():
    return assemble_live_preflight(env=os.environ, checkout_sha=os.environ["GITHUB_SHA"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--phase",
        required=True,
        choices=(
            "lost-ack-phase1",
            "lost-ack-phase2",
            "persist-phase1",
            "persist-phase2-finalize",
        ),
    )
    args = parser.parse_args()
    preflight = _preflight()
    if args.phase == "lost-ack-phase1":
        run_lost_ack_phase1(preflight=preflight)
        return
    if args.phase == "lost-ack-phase2":
        print(json.dumps(run_lost_ack_takeover(preflight=preflight), indent=2, sort_keys=True))
        return
    if args.phase == "persist-phase1":
        run_persist_phase1(preflight=preflight)
        return
    result = finalize_persist_recovery(
        preflight=preflight,
        github_workflow_run_id=os.environ["GITHUB_RUN_ID"],
        workflow_sha=os.environ["GITHUB_SHA"],
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
