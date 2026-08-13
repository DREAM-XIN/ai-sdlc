#!/usr/bin/env python3
"""Deterministic validation for personal-repository Store ruleset protection."""
from __future__ import annotations

import copy
from pathlib import Path
import subprocess
import tempfile
from urllib.parse import urlparse

from operator_store_github_protection_composite import GitHubRepositoryProtectionVerifier
from operator_store_github_ruleset_provision import (
    BOOTSTRAP_COMMIT_MESSAGE,
    BOOTSTRAP_GIT_EMAIL,
    BOOTSTRAP_GIT_NAME,
    BOOTSTRAP_MARKER,
    BOOTSTRAP_MARKER_PATH,
    GitHubOperatorStoreRulesetProvisioner,
    RULESET_INTEGRITY_NAME,
    RULESET_WRITER_NAME,
    RulesetProvisioningError,
    bootstrap_state_ref,
    provision_operator_store_state_ref,
)
from operator_store_protection import PROTECTED, UNKNOWN, UNPROTECTED, ProtectionError, ProtectionReceipt
from operator_store_remote_git import RemoteGitStateRefBackend

REPOSITORY = "DREAM-XIN/ai-sdlc"
STATE_REF = "refs/heads/ai-sdlc-operator-state"
APP_ID = 9001
APP_SLUG = "ai-sdlc-operator"
NOW = "2026-08-11T04:00:00Z"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


class FakeRulesetApi:
    def __init__(self):
        self.rulesets: dict[int, dict] = {}
        self.next_id = 101
        self.calls: list[tuple[str, str, dict | None]] = []

    def _summary(self, ruleset_id: int, payload: dict) -> dict:
        return {
            "id": ruleset_id,
            "name": payload["name"],
            "source_type": "Repository",
            "source": REPOSITORY,
            "enforcement": payload["enforcement"],
        }

    def _detail(self, ruleset_id: int, payload: dict) -> dict:
        result = copy.deepcopy(payload)
        result.update({"id": ruleset_id, "source_type": "Repository", "source": REPOSITORY})
        return result

    def request(self, method: str, url: str, headers: dict[str, str], body: dict | None = None):
        self.calls.append((method, url, copy.deepcopy(body)))
        parsed = urlparse(url)
        path = parsed.path

        if method == "GET" and path.endswith(f"/repos/{REPOSITORY}/rulesets"):
            return 200, [self._summary(ruleset_id, payload) for ruleset_id, payload in sorted(self.rulesets.items())]

        if method == "POST" and path.endswith(f"/repos/{REPOSITORY}/rulesets"):
            ruleset_id = self.next_id
            self.next_id += 1
            self.rulesets[ruleset_id] = copy.deepcopy(body or {})
            return 201, self._detail(ruleset_id, self.rulesets[ruleset_id])

        prefix = f"/repos/{REPOSITORY}/rulesets/"
        if path.startswith(prefix):
            try:
                ruleset_id = int(path[len(prefix):])
            except ValueError:
                return 404, {}
            if ruleset_id not in self.rulesets:
                return 404, {}
            if method == "GET":
                return 200, self._detail(ruleset_id, self.rulesets[ruleset_id])
            if method == "PUT":
                self.rulesets[ruleset_id] = copy.deepcopy(body or {})
                return 200, self._detail(ruleset_id, self.rulesets[ruleset_id])

        branch_rules = f"/repos/{REPOSITORY}/rules/branches/ai-sdlc-operator-state"
        if method == "GET" and path == branch_rules:
            rows = []
            for ruleset_id, payload in sorted(self.rulesets.items()):
                if payload.get("enforcement") != "active" or payload.get("target") != "branch":
                    continue
                includes = (((payload.get("conditions") or {}).get("ref_name") or {}).get("include") or [])
                if STATE_REF not in includes:
                    continue
                for rule in payload.get("rules") or []:
                    rows.append(
                        {
                            "type": rule.get("type"),
                            "ruleset_id": ruleset_id,
                            "ruleset_source_type": "Repository",
                            "ruleset_source": REPOSITORY,
                        }
                    )
            return 200, rows

        return 404, {}

    def get(self, url: str, headers: dict[str, str]):
        return self.request("GET", url, headers, None)


def make_provisioner(api: FakeRulesetApi):
    return GitHubOperatorStoreRulesetProvisioner(
        admin_token="trusted-admin-token",
        operator_app_id=APP_ID,
        http_request=api.request,
        sleeper=lambda _: None,
    )


def _update_rule(writer: dict) -> dict:
    matches = [row for row in writer.get("rules") or [] if isinstance(row, dict) and row.get("type") == "update"]
    require(len(matches) == 1, "writer ruleset must have exactly one update rule")
    return matches[0]


def validate_ruleset_proof_and_provisioning():
    api = FakeRulesetApi()
    provisioner = make_provisioner(api)
    writer_id, integrity_id = provisioner.ensure_rulesets(REPOSITORY, STATE_REF)
    require(writer_id != integrity_id, "writer and integrity rulesets must be separate")

    writer = copy.deepcopy(api.rulesets[writer_id])
    integrity = copy.deepcopy(api.rulesets[integrity_id])
    require(writer["name"] == RULESET_WRITER_NAME, "writer ruleset name drifted")
    require(integrity["name"] == RULESET_INTEGRITY_NAME, "integrity ruleset name drifted")
    require({rule["type"] for rule in writer["rules"]} == {"creation", "update"}, "writer ruleset scope drifted")
    require(
        _update_rule(writer).get("parameters") == {"update_allows_fetch_and_merge": False},
        "writer update rule is not the exact bounded shape",
    )
    require(writer["bypass_actors"] == [{"actor_id": APP_ID, "actor_type": "Integration", "bypass_mode": "always"}], "writer bypass must be exact Operator Integration")
    require({rule["type"] for rule in integrity["rules"]} == {"deletion", "non_fast_forward"}, "integrity ruleset scope drifted")
    require(integrity["bypass_actors"] == [], "integrity ruleset must have no bypass actors")

    verifier = provisioner.protection_verifier()
    receipt = verifier.verify(REPOSITORY, STATE_REF)
    require(receipt.status == PROTECTED and receipt.policy_digest, "layered rulesets were not proven PROTECTED")

    again = provisioner.ensure_rulesets(REPOSITORY, STATE_REF)
    require(again == (writer_id, integrity_id), "ruleset provisioning is not idempotent")
    require(len(api.rulesets) == 2, "idempotent provisioning created duplicate rulesets")
    writer = copy.deepcopy(api.rulesets[writer_id])
    integrity = copy.deepcopy(api.rulesets[integrity_id])

    permissive_update = copy.deepcopy(writer)
    _update_rule(permissive_update)["parameters"] = {"update_allows_fetch_and_merge": True}
    api.rulesets[writer_id] = permissive_update
    require(verifier.verify(REPOSITORY, STATE_REF).status == UNKNOWN, "permissive update rule parameters did not fail closed UNKNOWN")
    api.rulesets[writer_id] = copy.deepcopy(writer)

    missing_update_parameters = copy.deepcopy(writer)
    _update_rule(missing_update_parameters).pop("parameters", None)
    api.rulesets[writer_id] = missing_update_parameters
    require(verifier.verify(REPOSITORY, STATE_REF).status == UNKNOWN, "missing update rule parameters did not fail closed UNKNOWN")
    api.rulesets[writer_id] = copy.deepcopy(writer)

    expanded_update_parameters = copy.deepcopy(writer)
    _update_rule(expanded_update_parameters)["parameters"]["future_relaxation"] = False
    api.rulesets[writer_id] = expanded_update_parameters
    require(verifier.verify(REPOSITORY, STATE_REF).status == UNKNOWN, "ambiguous expanded update parameters did not fail closed UNKNOWN")
    api.rulesets[writer_id] = copy.deepcopy(writer)

    other_writer = copy.deepcopy(writer)
    other_writer["bypass_actors"].append({"actor_id": 77, "actor_type": "User", "bypass_mode": "always"})
    api.rulesets[writer_id] = other_writer
    require(verifier.verify(REPOSITORY, STATE_REF).status == UNPROTECTED, "second writer bypass was not rejected")
    api.rulesets[writer_id] = copy.deepcopy(writer)

    exempt_writer = copy.deepcopy(writer)
    exempt_writer["bypass_actors"][0]["bypass_mode"] = "exempt"
    api.rulesets[writer_id] = exempt_writer
    require(verifier.verify(REPOSITORY, STATE_REF).status == UNPROTECTED, "non-auditable exempt writer bypass was not rejected")
    api.rulesets[writer_id] = copy.deepcopy(writer)

    bypass_integrity = copy.deepcopy(integrity)
    bypass_integrity["bypass_actors"] = [{"actor_id": APP_ID, "actor_type": "Integration", "bypass_mode": "always"}]
    api.rulesets[integrity_id] = bypass_integrity
    require(verifier.verify(REPOSITORY, STATE_REF).status == UNPROTECTED, "bypassable delete/force-push fence was not rejected")
    api.rulesets[integrity_id] = copy.deepcopy(integrity)

    hidden_bypass = copy.deepcopy(writer)
    hidden_bypass.pop("bypass_actors")
    api.rulesets[writer_id] = hidden_bypass
    require(verifier.verify(REPOSITORY, STATE_REF).status == UNKNOWN, "missing bypass actor visibility did not fail closed UNKNOWN")
    api.rulesets[writer_id] = copy.deepcopy(writer)

    original_detail = api._detail
    def parent_detail(ruleset_id, payload):
        result = original_detail(ruleset_id, payload)
        if ruleset_id == writer_id:
            result["source_type"] = "Organization"
        return result
    api._detail = parent_detail
    require(verifier.verify(REPOSITORY, STATE_REF).status == UNKNOWN, "unsupported parent ruleset proof did not fail closed")
    api._detail = original_detail
    api.rulesets[writer_id] = copy.deepcopy(writer)


def validate_composite_compatibility():
    api = FakeRulesetApi()
    provisioner = make_provisioner(api)
    provisioner.ensure_rulesets(REPOSITORY, STATE_REF)

    def classic_missing(url, headers):
        return 404, {}

    composite = GitHubRepositoryProtectionVerifier(
        token="trusted-token",
        operator_app_slug=APP_SLUG,
        operator_app_id=APP_ID,
        branch_http_get=classic_missing,
        ruleset_http_get=api.get,
        clock=lambda: NOW,
    )
    receipt = composite.verify(REPOSITORY, STATE_REF)
    require(receipt.status == PROTECTED and receipt.verifier_identity.startswith("github-ruleset:"), "personal-repo ruleset fallback was not accepted")

    classic_payload = {
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
        "restrictions": {"apps": [{"slug": APP_SLUG}]},
    }
    def classic_protected(url, headers):
        return 200, classic_payload
    def ruleset_must_not_run(url, headers):
        raise AssertionError("ruleset fallback ran after classic protection already proved safe")

    organization = GitHubRepositoryProtectionVerifier(
        token="trusted-token",
        operator_app_slug=APP_SLUG,
        operator_app_id=APP_ID,
        branch_http_get=classic_protected,
        ruleset_http_get=ruleset_must_not_run,
        clock=lambda: NOW,
    )
    receipt = organization.verify(REPOSITORY, STATE_REF)
    require(receipt.status == PROTECTED and receipt.verifier_identity.startswith("github-branch-protection:"), "classic branch-protection compatibility regressed")


def git(*args, cwd=None, check=True):
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=check)


def _init_remote(root: Path, name: str) -> tuple[Path, Path]:
    remote = root / f"{name}.git"
    writer = root / f"{name}-writer"
    git("init", "--bare", "-q", str(remote))
    git("clone", "-q", str(remote), str(writer))
    git("config", "user.name", "untrusted-preseed", cwd=writer)
    git("config", "user.email", "untrusted-preseed@example.invalid", cwd=writer)
    return remote, writer


def _seed_existing_ref(writer: Path, files: dict[str, str], *, second_commit: bool = False):
    for relative, content in files.items():
        path = writer / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    git("add", ".", cwd=writer)
    git("commit", "-q", "-m", BOOTSTRAP_COMMIT_MESSAGE, cwd=writer)
    if second_commit:
        extra = writer / "untrusted-history.txt"
        extra.write_text("second commit\n", encoding="utf-8")
        git("add", ".", cwd=writer)
        git("commit", "-q", "-m", "untrusted second commit", cwd=writer)
    git("push", "-q", "origin", f"HEAD:{STATE_REF}", cwd=writer)


def _expect_existing_ref_rejected(writer: Path, reason: str):
    provisioner = make_provisioner(FakeRulesetApi())
    try:
        provision_operator_store_state_ref(
            provisioner=provisioner,
            repository=REPOSITORY,
            state_ref=STATE_REF,
            writer_checkout=writer,
        )
        raise AssertionError(f"{reason} was unexpectedly adopted")
    except RulesetProvisioningError:
        pass


def validate_initialization_only_bootstrap():
    api = FakeRulesetApi()
    provisioner = make_provisioner(api)

    with tempfile.TemporaryDirectory(prefix="ai-sdlc-ruleset-bootstrap-") as td:
        root = Path(td)
        _, writer = _init_remote(root, "trusted")

        bad = ProtectionReceipt(REPOSITORY, STATE_REF, UNPROTECTED, "test", NOW, None)
        try:
            bootstrap_state_ref(
                writer_checkout=writer,
                remote_name="origin",
                repository=REPOSITORY,
                state_ref=STATE_REF,
                protection_receipt=bad,
            )
            raise AssertionError("unprotected bootstrap unexpectedly created state ref")
        except ProtectionError:
            pass
        require(not git("ls-remote", "--refs", "origin", STATE_REF, cwd=writer).stdout.strip(), "unprotected bootstrap mutated remote")

        result = provision_operator_store_state_ref(
            provisioner=provisioner,
            repository=REPOSITORY,
            state_ref=STATE_REF,
            writer_checkout=writer,
        )
        require(result.created_state_ref is True, "first provisioning did not create initialization ref")
        require(result.protection_receipt.status == PROTECTED, "post-bootstrap protection was not re-verified")

        listed = git("ls-tree", "-r", "--name-only", result.state_ref_sha, cwd=writer).stdout.splitlines()
        require(listed == [BOOTSTRAP_MARKER_PATH], f"bootstrap commit contained unexpected files: {listed}")
        require(git("show", f"{result.state_ref_sha}:{BOOTSTRAP_MARKER_PATH}", cwd=writer).stdout == BOOTSTRAP_MARKER, "bootstrap marker bytes drifted")
        require(not any(path.endswith(".json") for path in listed), "bootstrap commit contained semantic Store JSON")

        identity = git("show", "-s", "--format=%an%x00%ae%x00%cn%x00%ce", result.state_ref_sha, cwd=writer).stdout.rstrip("\n").split("\x00")
        require(
            identity == [BOOTSTRAP_GIT_NAME, BOOTSTRAP_GIT_EMAIL, BOOTSTRAP_GIT_NAME, BOOTSTRAP_GIT_EMAIL],
            f"bootstrap commit identity is not trusted: {identity}",
        )
        require(git("rev-list", "--count", result.state_ref_sha, cwd=writer).stdout.strip() == "1", "bootstrap ref is not one root commit")

        backend = RemoteGitStateRefBackend(
            repo_path=writer,
            repository=REPOSITORY,
            state_ref=STATE_REF,
        )
        snapshot = backend.read_snapshot()
        require(snapshot.ref_sha == result.state_ref_sha, "fresh Store backend did not see bootstrap ref")
        require(snapshot.files == {}, "initialization-only marker leaked into semantic Store snapshot")

        second = provision_operator_store_state_ref(
            provisioner=provisioner,
            repository=REPOSITORY,
            state_ref=STATE_REF,
            writer_checkout=writer,
        )
        require(second.created_state_ref is False, "trusted bootstrap sentinel was not idempotently re-verified")
        require(second.state_ref_sha == result.state_ref_sha, "idempotent provisioning changed bootstrap ref")


def validate_existing_ref_adoption_fails_closed():
    with tempfile.TemporaryDirectory(prefix="ai-sdlc-ruleset-adoption-") as td:
        root = Path(td)

        _, wrong_identity = _init_remote(root, "wrong-identity")
        _seed_existing_ref(wrong_identity, {BOOTSTRAP_MARKER_PATH: BOOTSTRAP_MARKER})
        _expect_existing_ref_rejected(wrong_identity, "bootstrap-shaped ref with untrusted identity")

        _, semantic = _init_remote(root, "semantic")
        _seed_existing_ref(
            semantic,
            {
                BOOTSTRAP_MARKER_PATH: BOOTSTRAP_MARKER,
                "state/operator/v1/operations/preseeded.json": '{"operation_id":"untrusted"}\n',
            },
        )
        _expect_existing_ref_rejected(semantic, "pre-seeded semantic Store content")

        _, history = _init_remote(root, "multi-commit")
        _seed_existing_ref(history, {BOOTSTRAP_MARKER_PATH: BOOTSTRAP_MARKER}, second_commit=True)
        _expect_existing_ref_rejected(history, "multi-commit pre-existing Store history")


def main():
    validate_ruleset_proof_and_provisioning()
    validate_composite_compatibility()
    validate_initialization_only_bootstrap()
    validate_existing_ref_adoption_fails_closed()
    print("Operator Store GitHub ruleset protection validation passed")
    print("- update rule parameters: exact update_allows_fetch_and_merge=false, fail closed otherwise")
    print("- personal repository: layered creation/update + deletion/non-fast-forward proof")
    print("- exclusive auditable Operator Integration writer bypass")
    print("- integrity rules: no bypass, including Operator writer")
    print("- classic organization branch-protection path: preserved")
    print("- Mode A bootstrap: protection proved before initialization-only state ref creation")
    print("- existing ref adoption: exact trusted one-commit bootstrap sentinel only")
    print("- pre-seeded semantic JSON / wrong identity / multi-commit history: rejected")


if __name__ == "__main__":
    main()
