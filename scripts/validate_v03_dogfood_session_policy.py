#!/usr/bin/env python3
"""Adversarial authority coverage for the real session-recovery policy."""
import base64
from copy import deepcopy
import json
from pathlib import Path
from operator_store import StoreCommandError
from v03_dogfood_fixture_pool import require_slot
from v03_dogfood_session_policy import DogfoodSessionDecisionPolicyVerifier, POLICY_PATH

def main():
    sha = "a" * 40
    policy = json.loads((Path(__file__).resolve().parents[1] / POLICY_PATH).read_text())
    slot = require_slot("session_recovery")
    state = {"sha": sha, "policy": policy}
    def read(path):
        if path == "/git/ref/heads/main": return {"object": {"sha": state["sha"]}}
        assert path == "/contents/" + POLICY_PATH + "?ref=" + sha
        return {"type": "file", "path": POLICY_PATH, "encoding": "base64",
                "content": base64.b64encode(json.dumps(state["policy"]).encode()).decode()}
    verifier = DogfoodSessionDecisionPolicyVerifier(repository="DREAM-XIN/ai-sdlc", installation_sha=sha,
                                                    token="test-read", read_json=read)
    scope = dict(target_repository="dream-xin/ai-sdlc", feature_id=slot.feature_id,
                 target_ref=slot.target_ref, decision_type="NEEDS_AUTHORIZATION")
    verified = verifier.verify_current(**scope)
    assert verified.allowed_choices == ("approve", "deny")
    assert verified.allowed_responders == frozenset({"DREAM-XIN"})
    assert verified.policy_ref.endswith("@" + sha)
    def reject(**changes):
        try: verifier.verify_current(**{**scope, **changes})
        except StoreCommandError: return
        raise AssertionError("expanded session policy accepted")
    for key, value in (("target_repository", "dream-xin/other"), ("feature_id", "F-OTHER"),
                       ("target_ref", "main"), ("decision_type", "NEEDS_ACCEPTANCE")):
        reject(**{key: value})
    state["sha"] = "b"*40
    reject()
    state["sha"] = sha
    state["policy"] = deepcopy(policy)
    state["policy"]["target_ref"] = "main"
    reject()
    print("v0.3 session Decision policy: PASS (six authority expansions rejected)")

if __name__ == "__main__": main()
