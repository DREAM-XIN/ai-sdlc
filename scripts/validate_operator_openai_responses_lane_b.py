#!/usr/bin/env python3
"""Current-runtime Lane-B adapter over the historical Responses conformance body.

The historical Lane-B validator used a raw in-process dispatch gateway. Trusted
main now requires every production external create to carry the frozen Worker /
workflow execution binding before the Store-backed one-shot gate permits the
create boundary. Keep the existing Lane-B assertions unchanged, but exercise
those assertions through the reviewed production gh-aw role gateway and a
transport-only deterministic seam.
"""
from __future__ import annotations

import validate_operator_openai_responses_lane_b_legacy as _legacy
from operator_vertical import VerticalInvariantError
from operator_vertical_gh_aw import GhAwVerticalRoleDispatchGateway, GhAwVerticalWorkflowMap

# Re-export the historical validator surface so existing imports/probes continue
# to observe the same Lane-B helpers and assertions.
for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_legacy, _name)


class _DeterministicGhAwTransport:
    """Outer Actions seam only; workflow/provider identity stays production-shaped."""

    def __init__(self):
        self.launches: list[dict] = []
        self.receipts: dict[tuple[str, str], str] = {}

    def dispatch(self, *, workflow: str, ref: str, inputs: dict[str, str]) -> dict:
        key = str(inputs.get("dispatch_key") or "")
        if not key:
            raise VerticalInvariantError("INVALID_REQUEST", "Lane-B dispatch lacks stable dispatch key")
        identity = (workflow, key)
        if identity not in self.receipts:
            self.receipts[identity] = f"responses-lane-b-run-{len(self.receipts) + 1}"
            self.launches.append({"workflow": workflow, "ref": ref, "inputs": dict(inputs)})
        return {"lookup_state": "LAUNCHED", "receipt_id": self.receipts[identity]}

    def lookup(self, *, workflow: str, ref: str, dispatch_key: str) -> dict:
        receipt = self.receipts.get((workflow, dispatch_key))
        if receipt is None:
            return {"lookup_state": "NOT_LAUNCHED", "receipt_id": None}
        return {"lookup_state": "LAUNCHED", "receipt_id": receipt}


class DispatchTransport(GhAwVerticalRoleDispatchGateway):
    """Production role gateway with deterministic Actions I/O for Lane-B."""

    def __init__(self):
        transport = _DeterministicGhAwTransport()
        super().__init__(
            transport=transport,
            workflows=GhAwVerticalWorkflowMap(
                default_branch="main",
                developer_workflow="ai-sdlc-gh-aw-worker-codex.lock.yml",
                reviewer_workflow="ai-sdlc-gh-aw-reviewer-claude.lock.yml",
                qa_workflow="ai-sdlc-gh-aw-qa-gemini.lock.yml",
            ),
        )
        self.launches = transport.launches
        self.receipts = transport.receipts


# The unchanged historical run_lane_b() resolves DispatchTransport from its own
# module globals. Replace only that fixture class; all assertions stay intact.
_legacy.DispatchTransport = DispatchTransport
run_lane_b = _legacy.run_lane_b
main = _legacy.main


if __name__ == "__main__":
    main()
