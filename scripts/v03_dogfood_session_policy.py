#!/usr/bin/env python3
"""Exact-main, session-slot-only Decision policy through the production verifier."""
from __future__ import annotations
import base64
import json
from urllib import request
from operator_decision_policy import ProtectedDecisionPolicyVerifier
from operator_store import StoreCommandError
from operator_store_model import digest_json, normalize_repository
from operator_vertical import VERTICAL_PROFILE
from v03_dogfood_fixture_pool import require_slot

POLICY_PATH = "release/v0.3-dogfood-session-policy.json"


class DogfoodSessionDecisionPolicyVerifier(ProtectedDecisionPolicyVerifier):
    def __init__(self, *, repository, installation_sha, token, api_base="https://api.github.com", read_json=None):
        self.installation_sha = str(installation_sha)
        self.token = str(token)
        self.api_base = str(api_base).rstrip("/")
        self.read_json = read_json or self._read_json
        if len(self.installation_sha) != 40 or any(c not in "0123456789abcdef" for c in self.installation_sha) or not self.token or not self.api_base.startswith("https://"):
            raise ValueError("session policy requires exact installation/read authority")
        super().__init__(repository=repository, state_ref="refs/heads/ai-sdlc-operator-state",
                         operation_profile=VERTICAL_PROFILE, policy_loader=self._load_policy)

    def _read_json(self, path):
        req = request.Request(self.api_base + "/repos/" + self.repository + path, method="GET",
                              headers={"Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json",
                                       "X-GitHub-Api-Version": "2022-11-28"})
        with request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode())

    def _load_policy(self, repository, state_ref, operation_profile):
        current = self.read_json("/git/ref/heads/main")
        if not isinstance(current, dict) or (current.get("object") or {}).get("sha") != self.installation_sha:
            raise StoreCommandError("POLICY_DENIED", "session policy installation is no longer exact main")
        file = self.read_json("/contents/" + POLICY_PATH + "?ref=" + self.installation_sha)
        if not isinstance(file, dict) or file.get("type") != "file" or file.get("encoding") != "base64" or file.get("path") != POLICY_PATH:
            raise StoreCommandError("POLICY_DENIED", "session policy lacks exact default-branch provenance")
        try:
            policy = json.loads(base64.b64decode(file["content"]).decode())
        except Exception as exc:
            raise StoreCommandError("POLICY_DENIED", "invalid session Decision policy") from exc
        slot = require_slot("session_recovery")
        if (policy.get("repository"), policy.get("state_ref"), policy.get("operation_profile"),
            policy.get("feature_id"), policy.get("target_ref")) != (
            self.repository, self.state_ref, self.operation_profile, slot.feature_id, slot.target_ref
        ) or set(policy.get("decision_types") or {}) != {"NEEDS_AUTHORIZATION"}:
            raise StoreCommandError("POLICY_DENIED", "session policy escaped its frozen target/type")
        policy["policy_ref"] = "default-branch://" + self.repository + "/" + POLICY_PATH + "@" + self.installation_sha
        policy["policy_epoch"] = "v03-session-dogfood:" + self.installation_sha
        policy["policy_digest"] = digest_json(policy)
        return policy

    def verify_current(self, *, target_repository, feature_id, target_ref, decision_type):
        slot = require_slot("session_recovery")
        if (normalize_repository(target_repository), feature_id, target_ref, decision_type) != (
            self.repository, slot.feature_id, slot.target_ref, "NEEDS_AUTHORIZATION"
        ):
            raise StoreCommandError("POLICY_DENIED", "Decision request escaped the exact session dogfood slot")
        return super().verify_current(target_repository=target_repository, feature_id=feature_id,
                                      target_ref=target_ref, decision_type=decision_type)
