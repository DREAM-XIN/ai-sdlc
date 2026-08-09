#!/usr/bin/env python3
"""Regression: completed remediation history must not block independent review completion."""
from __future__ import annotations

from apply_feature_event import apply_event
from validate_feature_transition import event
from validate_orchestrator_examples import base_manifest


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    initial = base_manifest()
    design_started = apply_event(initial, event("F-0030", [{"kind":"stage","id":"design","status":"WORKING"}], expected_revision=0, event_id="EVT-REMEDIATION-CLOSE-DESIGN-START"))
    require(design_started["outcome"] == "APPLIED", f"design start failed: {design_started}")
    design_done = apply_event(design_started["manifest"], event("F-0030", [{"kind":"stage","id":"design","status":"DONE"}], expected_revision=1, event_id="EVT-REMEDIATION-CLOSE-DESIGN-DONE"))
    require(design_done["outcome"] == "APPLIED", f"design completion failed: {design_done}")
    review_started = apply_event(design_done["manifest"], event("F-0030", [{"kind":"stage","id":"design-review","status":"WORKING"}], expected_revision=2, event_id="EVT-REMEDIATION-CLOSE-REVIEW-START"))
    require(review_started["outcome"] == "APPLIED", f"review start failed: {review_started}")
    remediation_created = apply_event(review_started["manifest"], event("F-0030", [{"kind":"task-record","record":{"id":"F-0030-DESIGN-REMEDIATION-CLOSE","kind":"remediation","stage":"design","role":"architect","source_stage":"design-review","feedback":"Close the review finding before independent review completion.","status":"TODO"}}], expected_revision=3, event_id="EVT-REMEDIATION-CLOSE-CREATE"))
    require(remediation_created["outcome"] == "APPLIED", f"remediation create failed: {remediation_created}")
    remediation_started = apply_event(remediation_created["manifest"], event("F-0030", [{"kind":"task","id":"F-0030-DESIGN-REMEDIATION-CLOSE","status":"WORKING"}], expected_revision=4, event_id="EVT-REMEDIATION-CLOSE-START"))
    require(remediation_started["outcome"] == "APPLIED", f"remediation start failed: {remediation_started}")
    premature_review_pass = apply_event(remediation_started["manifest"], event("F-0030", [{"kind":"evidence","record":{"id":"EVID-PREMATURE-REVIEW","type":"review","status":"pass","uri":"review://premature"}},{"kind":"gate","id":"design-gate","status":"PASS","evidence":["EVID-PREMATURE-REVIEW"]},{"kind":"stage","id":"design-review","status":"DONE"}], expected_revision=5, event_id="EVT-REMEDIATION-CLOSE-PREMATURE-PASS"))
    require(premature_review_pass["outcome"] == "INVALID", "unfinished remediation unexpectedly allowed source review completion")
    require("unfinished remediation" in "\n".join(premature_review_pass["errors"]), f"premature completion did not identify unfinished remediation: {premature_review_pass}")
    remediation_done = apply_event(remediation_started["manifest"], event("F-0030", [{"kind":"task","id":"F-0030-DESIGN-REMEDIATION-CLOSE","status":"DONE"}], expected_revision=5, event_id="EVT-REMEDIATION-CLOSE-DONE"))
    require(remediation_done["outcome"] == "APPLIED", f"remediation completion failed: {remediation_done}")
    final_review_pass = apply_event(remediation_done["manifest"], event("F-0030", [{"kind":"evidence","record":{"id":"EVID-FINAL-REVIEW","type":"review","status":"pass","uri":"review://final"}},{"kind":"gate","id":"design-gate","status":"PASS","evidence":["EVID-FINAL-REVIEW"]},{"kind":"stage","id":"design-review","status":"DONE"},{"kind":"stage","id":"plan","status":"READY"}], expected_revision=6, event_id="EVT-REMEDIATION-CLOSE-FINAL-PASS"))
    require(final_review_pass["outcome"] == "APPLIED", f"completed remediation history blocked final review completion: {final_review_pass}")
    require(next(task for task in final_review_pass["manifest"]["tasks"] if task["id"] == "F-0030-DESIGN-REMEDIATION-CLOSE")["status"] == "DONE", "completed remediation history was not retained")
    print("remediation review completion lifecycle checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
