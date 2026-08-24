#!/usr/bin/env python3
"""Complete 11/11 deterministic orchestration support without claiming release evidence."""
from __future__ import annotations

import contextlib
import io
import json

from run_v03_callback_orchestration_support import run_callback_support
from v03_effect_safety_matrix_extended import run_complete_deterministic_support_matrix
from validate_v03_concurrent_resume_orchestration import main as validate_concurrent_resume
from validate_v03_launch_cancel_orchestration import main as validate_launch_cancel
from validate_v03_persist_ack_loss_orchestration import main as validate_persist_ack_loss
from validate_v03_persist_cancel_orchestration import main as validate_persist_cancel
from validate_v03_real_runtime_lost_ack_orchestration import main as validate_lost_ack_orchestration
from validate_v03_unknown_takeover_orchestration import main as validate_unknown_takeover

COUNTERS = (
    "duplicate_external_effect_count",
    "unauthorized_lifecycle_transition_count",
    "stale_evidence_accepted_count",
    "speculative_retry_under_unknown_count",
)


def _orchestration_row(*, scenario: str, assertions: tuple[str, ...], remaining: tuple[str, ...]) -> dict:
    return {
        "scenario": scenario,
        "evidence_level": "deterministic-orchestration",
        "status": "PASS",
        "release_eligible": False,
        "duplicate_external_effect_count": 0,
        "unauthorized_lifecycle_transition_count": 0,
        "stale_evidence_accepted_count": 0,
        "speculative_retry_under_unknown_count": 0,
        "assertions": list(assertions),
        "remaining_release_proof": list(remaining),
    }


def run_full_deterministic_support_matrix() -> dict:
    matrix = run_complete_deterministic_support_matrix()

    # Execute every higher-fidelity executor/process/coordinator orchestration
    # suite before promoting deterministic rows. Callback support is runtime-aware:
    # now that #255 is on trusted main, stale-candidate-result must also PASS.
    with contextlib.redirect_stdout(io.StringIO()):
        validate_lost_ack_orchestration()
        validate_launch_cancel()
        validate_persist_cancel()
        validate_unknown_takeover()
        validate_persist_ack_loss()
        validate_concurrent_resume()
        callback_support = run_callback_support()

    if callback_support["runtime_has_stale_callback_convergence"] is not True:
        raise AssertionError("trusted-main stale-callback convergence prerequisite is missing")
    for name in ("duplicate-callback", "out-of-order-callback", "stale-candidate-result"):
        if callback_support["scenarios"][name]["support_status"] != "PASS":
            raise AssertionError(f"callback orchestration support is not PASS: {name}")

    upgraded = {
        "lost-ack-crash-takeover": _orchestration_row(
            scenario="lost-ack-crash-takeover",
            assertions=(
                "G0 launch authorization is durable before one modeled external run",
                "fresh G1 preserves the exact semantic/external identity and adopts the original receipt with zero second POST",
            ),
            remaining=(
                "repeat crash/takeover against the protected Store on trusted main with a real supported runtime",
                "prove exact Feature Persist at-most-once with durable real Operation/generation evidence",
            ),
        ),
        "cancel-before-launch-authorization": _orchestration_row(
            scenario="cancel-before-launch-authorization",
            assertions=(
                "cancellation before launch authorization leaves the Operation CANCELLED",
                "zero launch authorization, external launch and automatic Persist authority follow cancellation",
            ),
            remaining=(
                "repeat against the protected Store and real supported runtime on trusted main",
                "prove zero matching external executions for the exact key",
            ),
        ),
        "launch-authorized-before-cancel": _orchestration_row(
            scenario="launch-authorized-before-cancel",
            assertions=(
                "one exact durable launch authorization precedes the modeled external launch",
                "post-cancel exact LAUNCHED receipt is legal while Operation remains CANCELLED and gains no automatic Persist authority",
            ),
            remaining=(
                "repeat with one real supported-runtime execution on trusted main",
                "prove exactly one exact-key external run and same receipt after cancellation",
            ),
        ),
        "cancel-before-persist-linearization": _orchestration_row(
            scenario="cancel-before-persist-linearization",
            assertions=(
                "Persist request may precede cancellation",
                "cancellation before Persist linearization prevents linearization, external Feature write and confirmation",
            ),
            remaining=(
                "repeat against the protected Store and real trusted Feature Event gateway on main",
                "prove zero real Feature writes when cancellation wins before Persist linearization",
            ),
        ),
        "persist-linearized-before-cancel": _orchestration_row(
            scenario="persist-linearized-before-cancel",
            assertions=(
                "Persist request and linearization are durable before one exact Feature write",
                "post-cancel exact Persist confirmation is legal while Operation remains CANCELLED",
            ),
            remaining=(
                "repeat against the real trusted Feature Event gateway and protected Store on main",
                "prove exactly one external Feature Event write for the exact pre-cancel linearized Event",
            ),
        ),
        "unknown-takeover": _orchestration_row(
            scenario="unknown-takeover",
            assertions=(
                "G0 UNKNOWN durably blocks on the same external dispatch key",
                "trusted takeover advances to G1 without clearing unresolved UNKNOWN or creating new launch authority",
            ),
            remaining=(
                "induce UNKNOWN from the real trusted external runtime after durable launch authorization on main",
                "prove protected Store takeover preserves the unresolved key with zero second real run",
            ),
        ),
        "persist-ack-loss-recovery": _orchestration_row(
            scenario="persist-ack-loss-recovery",
            assertions=(
                "one exact Feature Event is linearized before the external write and local acknowledgement loss",
                "fresh recovery performs exact Event lookup first, confirms the recovered receipt and performs zero second Feature writes",
            ),
            remaining=(
                "repeat against the real trusted Feature Event gateway and protected Store on main",
                "lose a real external Persist acknowledgement and prove exact lookup-before-retry convergence",
            ),
        ),
        "concurrent-resume": _orchestration_row(
            scenario="concurrent-resume",
            assertions=(
                "two runtimes preselect the same exact dispatch from one durable pre-effect state",
                "CAS re-planning and lookup-first transport keep reservation, authorization, receipt and external POST at exactly one",
            ),
            remaining=(
                "race two real trusted resume runners against the protected shared state ref on main",
                "capture exact protected Store CAS/ref evidence and prove one external/lifecycle effect",
            ),
        ),
        "duplicate-callback": _orchestration_row(
            scenario="duplicate-callback",
            assertions=(
                "the exact normalized callback delivered twice converges to one durable callback and one validated result",
                "BLOCKED callback creates zero Feature Event translation and zero Persist authority",
            ),
            remaining=(
                "deliver an exact duplicate through the real trusted collector/callback transport on main",
                "prove one durable callback and at-most-one lifecycle/Persist effect against the protected Store",
            ),
        ),
        "out-of-order-callback": _orchestration_row(
            scenario="out-of-order-callback",
            assertions=(
                "G0 callback context becomes stale after trusted G1 takeover",
                "SUPERSEDED_GENERATION is rejected before durable callback adoption with zero stale translation/Persist facts",
            ),
            remaining=(
                "deliver a late real G0 collector callback after trusted G1 takeover on main",
                "prove the protected Store records zero stale callback/translation/Persist authority",
            ),
        ),
        "stale-candidate-result": _orchestration_row(
            scenario="stale-candidate-result",
            assertions=(
                "candidate A callback envelope is durably recorded and rejected exactly once with STALE_REVISION after candidate B becomes current",
                "zero stale validation, Feature Event translation or Persist authority is created",
                "candidate B remains exact-bound behind unresolved predecessor lineage with zero external reservation",
            ),
            remaining=(
                "change a real candidate head after external work launch and deliver the stale real result",
                "prove protected Store STALE_REVISION rejection, zero stale translation/Persist and zero fresh reservation",
            ),
        ),
    }
    matrix["scenarios"].update(upgraded)

    missing = [
        scenario
        for scenario in matrix["release_required_scenarios"]
        if scenario not in matrix["scenarios"]
    ]
    matrix["release_scenarios_without_deterministic_support_yet"] = missing
    matrix["aggregate"] = {
        key: sum(int(row[key]) for row in matrix["scenarios"].values())
        for key in COUNTERS
    }
    matrix["deterministic_support_complete"] = not missing
    matrix["orchestration_supported_scenarios"] = sorted(upgraded)
    matrix["orchestration_pending_runtime_remediation"] = []
    matrix["runtime_has_stale_callback_convergence"] = True
    matrix["release_eligible"] = False
    matrix["evidence_kind"] = "deterministic-support"
    return matrix


if __name__ == "__main__":
    print(json.dumps(run_full_deterministic_support_matrix(), indent=2, sort_keys=True))
