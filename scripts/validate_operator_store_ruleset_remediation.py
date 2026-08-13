#!/usr/bin/env python3
"""Regression coverage for PR #242 installation/runtime review remediation."""
from __future__ import annotations

import copy
import os
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch
from urllib.parse import urlparse

from operator_store_github_ruleset_protection import GitHubRulesetProtectionVerifier
from operator_store_github_ruleset_provision import (
    BOOTSTRAP_MARKER_PATH,
    GitHubOperatorStoreRulesetProvisioner,
    provision_operator_store_state_ref,
)
from operator_store_protection import PROTECTED, UNKNOWN, UNPROTECTED
from operator_store_remote_git import RemoteGitStateRefBackend

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "provision-operator-store-state.yml"
REPOSITORY = "DREAM-XIN/ai-sdlc"
STATE_REF = "refs/heads/ai-sdlc-operator-state"
APP_ID = 9001
TRUSTED_GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "AI-SDLC Operator Store",
    "GIT_AUTHOR_EMAIL": "operator-store@ai-sdlc.invalid",
    "GIT_COMMITTER_NAME": "AI-SDLC Operator Store",
    "GIT_COMMITTER_EMAIL": "operator-store@ai-sdlc.invalid",
}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


class FakeRulesetApi:
    def __init__(self):
        self.rulesets: dict[int, dict] = {}
        self.next_id = 101
        self.detail_mutator = None

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
        if self.detail_mutator is not None:
            result = self.detail_mutator(ruleset_id, result)
        return result

    def request(self, method: str, url: str, headers: dict[str, str], body: dict | None = None):
        path = urlparse(url).path
        rulesets_path = f"/repos/{REPOSITORY}/rulesets"
        if method == "GET" and path == rulesets_path:
            return 200, [self._summary(ruleset_id, payload) for ruleset_id, payload in sorted(self.rulesets.items())]
        if method == "POST" and path == rulesets_path:
            ruleset_id = self.next_id
            self.next_id += 1
            self.rulesets[ruleset_id] = copy.deepcopy(body or {})
            return 201, self._detail(ruleset_id, self.rulesets[ruleset_id])

        prefix = f"{rulesets_path}/"
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


def make_provisioner(api: FakeRulesetApi):
    return GitHubOperatorStoreRulesetProvisioner(
        admin_token="trusted-admin-token",
        operator_app_id=APP_ID,
        http_request=api.request,
        sleeper=lambda _: None,
    )


def validate_positive_repository_provenance_and_adversarial_cases():
    api = FakeRulesetApi()
    provisioner = make_provisioner(api)
    writer_id, integrity_id = provisioner.ensure_rulesets(REPOSITORY, STATE_REF)
    verifier = provisioner.protection_verifier()

    writer = api.rulesets[writer_id]
    integrity = api.rulesets[integrity_id]
    require(
        writer["bypass_actors"] == [
            {"actor_id": APP_ID, "actor_type": "Integration", "bypass_mode": "always"}
        ],
        "writer bypass is not the unique trusted Operator Integration",
    )
    require(
        {rule["type"] for rule in integrity["rules"]} == {"deletion", "non_fast_forward"}
        and integrity["bypass_actors"] == [],
        "delete/non-fast-forward integrity rules must both have zero bypass actors",
    )
    require(verifier.verify(REPOSITORY, STATE_REF).status == PROTECTED, "exact repository provenance was not accepted")

    original_detail = api.detail_mutator

    def expect_unknown(label, mutator):
        api.detail_mutator = lambda ruleset_id, detail: mutator(detail) if ruleset_id == writer_id else detail
        try:
            require(verifier.verify(REPOSITORY, STATE_REF).status == UNKNOWN, label)
        finally:
            api.detail_mutator = original_detail

    expect_unknown(
        "missing source_type did not fail closed UNKNOWN",
        lambda detail: ({key: value for key, value in detail.items() if key != "source_type"}),
    )
    expect_unknown(
        "unsupported/inherited source_type did not fail closed UNKNOWN",
        lambda detail: {**detail, "source_type": "Organization"},
    )
    expect_unknown(
        "missing repository source did not fail closed UNKNOWN",
        lambda detail: ({key: value for key, value in detail.items() if key != "source"}),
    )
    expect_unknown(
        "mismatched repository source did not fail closed UNKNOWN",
        lambda detail: {**detail, "source": "DREAM-XIN/other-control"},
    )
    expect_unknown(
        "unsupported repository source representation did not fail closed UNKNOWN",
        lambda detail: {**detail, "source": {"repository": REPOSITORY}},
    )

    foreign_writer = copy.deepcopy(writer)
    foreign_writer["bypass_actors"].append({"actor_id": 77, "actor_type": "User", "bypass_mode": "always"})
    api.rulesets[writer_id] = foreign_writer
    require(verifier.verify(REPOSITORY, STATE_REF).status != PROTECTED, "foreign writer bypass was accepted")
    api.rulesets[writer_id] = writer

    hidden_bypass = copy.deepcopy(writer)
    hidden_bypass.pop("bypass_actors")
    api.rulesets[writer_id] = hidden_bypass
    require(verifier.verify(REPOSITORY, STATE_REF).status == UNKNOWN, "unknown bypass visibility did not fail closed")
    api.rulesets[writer_id] = writer

    bypassable_integrity = copy.deepcopy(integrity)
    bypassable_integrity["bypass_actors"] = [
        {"actor_id": APP_ID, "actor_type": "Integration", "bypass_mode": "always"}
    ]
    api.rulesets[integrity_id] = bypassable_integrity
    require(verifier.verify(REPOSITORY, STATE_REF).status != PROTECTED, "bypassable integrity rules were accepted")
    api.rulesets[integrity_id] = integrity


def git(*args, cwd=None, input_text=None, env=None, check=True):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        input=input_text,
        text=True,
        capture_output=True,
        env=env,
        check=check,
    )


def clean_git_environment(root: Path) -> dict[str, str]:
    home = root / "clean-home"
    xdg = root / "clean-xdg"
    home.mkdir()
    xdg.mkdir()
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(xdg),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "user.useConfigOnly",
        "GIT_CONFIG_VALUE_0": "true",
    }


def validate_production_like_bootstrap_identity():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    for key, value in TRUSTED_GIT_IDENTITY.items():
        require(f"          {key}: {value}" in workflow, f"production workflow does not pin trusted {key}")

    api = FakeRulesetApi()
    provisioner = make_provisioner(api)
    with tempfile.TemporaryDirectory(prefix="ai-sdlc-ruleset-remediation-") as td:
        root = Path(td)
        remote = root / "control.git"
        writer = root / "writer"
        clean_env = clean_git_environment(root)
        git("init", "--bare", "-q", str(remote), env=clean_env)
        git("clone", "-q", str(remote), str(writer), env=clean_env)

        require(
            git("config", "--local", "--get", "user.name", cwd=writer, env=clean_env, check=False).returncode != 0,
            "production-like fixture unexpectedly has local user.name",
        )
        require(
            git("config", "--local", "--get", "user.email", cwd=writer, env=clean_env, check=False).returncode != 0,
            "production-like fixture unexpectedly has local user.email",
        )
        empty_tree = git("mktree", cwd=writer, input_text="", env=clean_env).stdout.strip()
        probe = git(
            "commit-tree",
            empty_tree,
            cwd=writer,
            input_text="identity probe\n",
            env=clean_env,
            check=False,
        )
        require(probe.returncode != 0, "clean fixture unexpectedly allowed commit-tree without trusted identity")

        production_env = {**clean_env, **TRUSTED_GIT_IDENTITY}
        with patch.dict(os.environ, production_env, clear=True):
            result = provision_operator_store_state_ref(
                provisioner=provisioner,
                repository=REPOSITORY,
                state_ref=STATE_REF,
                writer_checkout=writer,
            )
            require(result.created_state_ref is True, "production-like bootstrap did not create initialization ref")
            require(result.protection_receipt.status == PROTECTED, "post-bootstrap protection was not re-verified")

            listed = git("ls-tree", "-r", "--name-only", result.state_ref_sha, cwd=writer, env=production_env).stdout.splitlines()
            require(listed == [BOOTSTRAP_MARKER_PATH], f"bootstrap commit contained unexpected paths: {listed}")
            require(not any(path.endswith(".json") for path in listed), "bootstrap wrote semantic Operator Store JSON")

            identity = git(
                "show",
                "-s",
                "--format=%an%x00%ae%x00%cn%x00%ce",
                result.state_ref_sha,
                cwd=writer,
                env=production_env,
            ).stdout.strip().split("\x00")
            require(
                identity == [
                    TRUSTED_GIT_IDENTITY["GIT_AUTHOR_NAME"],
                    TRUSTED_GIT_IDENTITY["GIT_AUTHOR_EMAIL"],
                    TRUSTED_GIT_IDENTITY["GIT_COMMITTER_NAME"],
                    TRUSTED_GIT_IDENTITY["GIT_COMMITTER_EMAIL"],
                ],
                f"bootstrap commit identity drifted: {identity}",
            )

            backend = RemoteGitStateRefBackend(
                repo_path=writer,
                repository=REPOSITORY,
                state_ref=STATE_REF,
            )
            snapshot = backend.read_snapshot()
            require(snapshot.ref_sha == result.state_ref_sha, "Store backend did not read exact bootstrap ref")
            require(snapshot.files == {}, "bootstrap marker leaked into semantic Store snapshot")


def main():
    validate_positive_repository_provenance_and_adversarial_cases()
    validate_production_like_bootstrap_identity()
    print("Operator Store ruleset remediation validation passed")
    print("- repository provenance: positive proof only; ambiguous/inherited provenance => UNKNOWN")
    print("- writer bypass remains the unique trusted Operator Integration")
    print("- deletion/non-fast-forward remain zero-bypass integrity rules")
    print("- clean bootstrap uses deterministic workflow-owned Git identity")
    print("- bootstrap remains initialization-only with zero semantic Store JSON")


if __name__ == "__main__":
    main()
