#!/usr/bin/env python3
"""Trusted install/control provisioning for GitHub Operator Store rulesets.

An admin/control credential manages rulesets. A separately authenticated trusted
writer checkout creates the initialization-only state ref only after protection
has been positively proved. Existing refs are never implicitly adopted as a
Store: only the exact capabilityless bootstrap root created by this module can
be re-used by an idempotent provisioning rerun.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from operator_store_github_ruleset_protection import GitHubRulesetProtectionVerifier
from operator_store_protection import PROTECTED, ProtectionReceipt, require_protected

RULESET_WRITER_NAME = "AI-SDLC Operator Store writer"
RULESET_INTEGRITY_NAME = "AI-SDLC Operator Store integrity"
RULESET_LIST_PAGE_SIZE = 100
BOOTSTRAP_MARKER_PATH = "state/operator/v1/.bootstrap"
BOOTSTRAP_MARKER = "ai-sdlc-operator-store-bootstrap-v1\n"
BOOTSTRAP_COMMIT_MESSAGE = "AI-SDLC Operator Store initialization-only bootstrap"
# Keep the identity exactly aligned with the reviewed installation workflow.
BOOTSTRAP_GIT_NAME = "AI-SDLC Operator Store"
BOOTSTRAP_GIT_EMAIL = "operator-store@ai-sdlc.invalid"
BOOTSTRAP_GIT_ENV = {
    "GIT_AUTHOR_NAME": BOOTSTRAP_GIT_NAME,
    "GIT_AUTHOR_EMAIL": BOOTSTRAP_GIT_EMAIL,
    "GIT_COMMITTER_NAME": BOOTSTRAP_GIT_NAME,
    "GIT_COMMITTER_EMAIL": BOOTSTRAP_GIT_EMAIL,
}


class RulesetProvisioningError(RuntimeError):
    pass


@dataclass(frozen=True)
class RulesetProvisioningResult:
    writer_ruleset_id: int
    integrity_ruleset_id: int
    state_ref_sha: str
    protection_receipt: ProtectionReceipt
    created_state_ref: bool


def _default_request(
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict | None = None,
) -> tuple[int, object]:
    data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
    request = Request(url, data=data, headers=headers, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urlopen(request, timeout=20) as response:  # noqa: S310 - trusted GitHub API URL
            raw = response.read()
            return response.status, json.loads(raw.decode("utf-8")) if raw else {}
    except HTTPError as exc:
        raw = exc.read()
        try:
            payload: object = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            payload = {}
        return exc.code, payload
    except (URLError, TimeoutError, OSError):
        return 0, {}


def _headers(token: str, api_version: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": api_version,
        "User-Agent": "ai-sdlc-operator-store-installer",
    }


def _ruleset_conditions(state_ref: str) -> dict:
    return {"ref_name": {"include": [state_ref], "exclude": []}}


def writer_ruleset_payload(state_ref: str, operator_app_id: int) -> dict:
    return {
        "name": RULESET_WRITER_NAME,
        "target": "branch",
        "enforcement": "active",
        "bypass_actors": [
            {
                "actor_id": operator_app_id,
                "actor_type": "Integration",
                "bypass_mode": "always",
            }
        ],
        "conditions": _ruleset_conditions(state_ref),
        "rules": [
            {"type": "creation"},
            {"type": "update", "parameters": {"update_allows_fetch_and_merge": False}},
        ],
    }


def integrity_ruleset_payload(state_ref: str) -> dict:
    return {
        "name": RULESET_INTEGRITY_NAME,
        "target": "branch",
        "enforcement": "active",
        "bypass_actors": [],
        "conditions": _ruleset_conditions(state_ref),
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
        ],
    }


class GitHubOperatorStoreRulesetProvisioner:
    """Create/update exactly the two bounded Operator Store rulesets."""

    def __init__(
        self,
        *,
        admin_token: str,
        operator_app_id: int,
        api_base: str = "https://api.github.com",
        api_version: str = "2022-11-28",
        http_request: Callable[[str, str, dict[str, str], dict | None], tuple[int, object]] = _default_request,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        if not admin_token:
            raise ValueError("trusted repository-administration token is required")
        if not isinstance(operator_app_id, int) or operator_app_id <= 0:
            raise ValueError("trusted Operator GitHub App integration id is required")
        if not api_base.startswith("https://"):
            raise ValueError("GitHub ruleset API base must use HTTPS")
        self.admin_token = admin_token
        self.operator_app_id = operator_app_id
        self.api_base = api_base.rstrip("/")
        self.api_version = api_version
        self.http_request = http_request
        self.sleeper = sleeper

    def _request(self, method: str, url: str, body: dict | None = None) -> tuple[int, object]:
        return self.http_request(method, url, _headers(self.admin_token, self.api_version), body)

    def _list_rulesets(self, repository: str) -> list[dict]:
        """Read every repository branch-ruleset summary or fail closed.

        GitHub paginates repository-ruleset discovery. Provisioning must not
        create or update anything from a partial listing because a same-name
        ruleset on a later page would otherwise be missed and duplicated.
        """
        rows: list[dict] = []
        page = 1
        while True:
            query = urlencode(
                {
                    "targets": "branch",
                    "per_page": str(RULESET_LIST_PAGE_SIZE),
                    "page": str(page),
                }
            )
            status, payload = self._request("GET", f"{self.api_base}/repos/{repository}/rulesets?{query}")
            if status != 200 or not isinstance(payload, list):
                raise RulesetProvisioningError(
                    f"unable to list complete repository rulesets page {page}: HTTP {status}"
                )
            if any(not isinstance(row, dict) for row in payload):
                raise RulesetProvisioningError(
                    f"repository ruleset listing page {page} contains malformed rows"
                )
            rows.extend(payload)
            if len(payload) < RULESET_LIST_PAGE_SIZE:
                return rows
            page += 1

    @staticmethod
    def _existing_id(rows: list[dict], name: str) -> int | None:
        matches = [
            row for row in rows
            if row.get("name") == name and row.get("source_type") in {None, "Repository"}
        ]
        if len(matches) > 1:
            raise RulesetProvisioningError(f"multiple repository rulesets named {name!r}")
        if not matches:
            return None
        value = matches[0].get("id")
        if not isinstance(value, int) or value <= 0:
            raise RulesetProvisioningError(f"ruleset {name!r} has invalid id")
        return value

    def _upsert(self, repository: str, ruleset_id: int | None, payload: dict) -> int:
        if ruleset_id is None:
            status, result = self._request("POST", f"{self.api_base}/repos/{repository}/rulesets", payload)
            expected = 201
        else:
            status, result = self._request("PUT", f"{self.api_base}/repos/{repository}/rulesets/{ruleset_id}", payload)
            expected = 200
        if status != expected or not isinstance(result, dict):
            action = "create" if ruleset_id is None else "update"
            raise RulesetProvisioningError(f"unable to {action} ruleset {payload['name']!r}: HTTP {status}")
        value = result.get("id", ruleset_id)
        if not isinstance(value, int) or value <= 0:
            raise RulesetProvisioningError(f"ruleset {payload['name']!r} response lacks id")
        return value

    def ensure_rulesets(self, repository: str, state_ref: str) -> tuple[int, int]:
        if "/" not in repository:
            raise ValueError("repository must be owner/name")
        if not state_ref.startswith("refs/heads/"):
            raise ValueError("Operator Store state ref must be a branch ref")
        rows = self._list_rulesets(repository)
        writer_id = self._upsert(
            repository,
            self._existing_id(rows, RULESET_WRITER_NAME),
            writer_ruleset_payload(state_ref, self.operator_app_id),
        )
        rows = self._list_rulesets(repository)
        integrity_id = self._upsert(
            repository,
            self._existing_id(rows, RULESET_INTEGRITY_NAME),
            integrity_ruleset_payload(state_ref),
        )
        return writer_id, integrity_id

    def protection_verifier(self) -> GitHubRulesetProtectionVerifier:
        def get(url: str, headers: dict[str, str]):
            return self.http_request("GET", url, headers, None)

        return GitHubRulesetProtectionVerifier(
            token=self.admin_token,
            operator_app_id=self.operator_app_id,
            api_base=self.api_base,
            api_version=self.api_version,
            http_get=get,
        )

    def verify_pre_targeted_protection(
        self,
        repository: str,
        state_ref: str,
        *,
        attempts: int = 5,
        interval_seconds: float = 1.0,
    ) -> ProtectionReceipt:
        if attempts < 1:
            raise ValueError("verification attempts must be positive")
        verifier = self.protection_verifier()
        last = None
        for attempt in range(attempts):
            last = verifier.verify(repository, state_ref)
            if last.status == PROTECTED:
                return last
            if attempt + 1 < attempts:
                self.sleeper(interval_seconds)
        raise RulesetProvisioningError(
            f"Operator Store rulesets did not become PROTECTED: {getattr(last, 'status', 'UNKNOWN')}"
        )


def _git(
    repo_path: Path,
    *args: str,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
):
    merged = os.environ.copy()
    merged.update(env or {})
    return subprocess.run(
        ["git", *args],
        cwd=repo_path,
        input=input_text,
        text=True,
        capture_output=True,
        env=merged,
        check=check,
    )


def _remote_ref_sha(repo_path: Path, remote_name: str, state_ref: str) -> str | None:
    result = _git(repo_path, "ls-remote", "--refs", remote_name, state_ref, check=False)
    if result.returncode != 0:
        raise RulesetProvisioningError("unable to inspect remote Operator Store state ref")
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return None
    if len(lines) != 1:
        raise RulesetProvisioningError("ambiguous remote Operator Store state-ref response")
    sha, separator, ref = lines[0].partition("\t")
    if (
        separator != "\t"
        or ref != state_ref
        or len(sha) != 40
        or any(ch not in "0123456789abcdefABCDEF" for ch in sha)
    ):
        raise RulesetProvisioningError("unexpected remote Operator Store state-ref response")
    return sha.lower()


def _verify_existing_bootstrap_sentinel(
    repo_path: Path,
    remote_name: str,
    state_ref: str,
    expected_sha: str,
) -> None:
    """Fail closed unless an existing ref is the exact trusted bootstrap root."""
    fetched = _git(repo_path, "fetch", "--quiet", "--no-tags", remote_name, state_ref, check=False)
    if fetched.returncode != 0:
        raise RulesetProvisioningError("unable to fetch existing Operator Store state ref for provenance proof")
    fetched_sha = _git(repo_path, "rev-parse", "FETCH_HEAD", check=False)
    if fetched_sha.returncode != 0 or fetched_sha.stdout.strip().lower() != expected_sha:
        raise RulesetProvisioningError("existing Operator Store state ref changed during provenance proof")

    object_type = _git(repo_path, "cat-file", "-t", expected_sha, check=False)
    if object_type.returncode != 0 or object_type.stdout.strip() != "commit":
        raise RulesetProvisioningError("existing Operator Store state ref is not a commit")

    identity = _git(
        repo_path,
        "show", "-s", "--format=%an%x00%ae%x00%cn%x00%ce%x00%P", expected_sha,
        check=False,
    )
    if identity.returncode != 0:
        raise RulesetProvisioningError("unable to inspect existing Operator Store bootstrap identity")
    fields = identity.stdout.rstrip("\n").split("\x00")
    expected_identity = [
        BOOTSTRAP_GIT_NAME,
        BOOTSTRAP_GIT_EMAIL,
        BOOTSTRAP_GIT_NAME,
        BOOTSTRAP_GIT_EMAIL,
        "",
    ]
    if fields != expected_identity:
        raise RulesetProvisioningError("existing Operator Store state ref lacks trusted bootstrap identity/root shape")

    count = _git(repo_path, "rev-list", "--count", expected_sha, check=False)
    if count.returncode != 0 or count.stdout.strip() != "1":
        raise RulesetProvisioningError("existing Operator Store state ref is not the one-commit bootstrap root")

    paths = _git(repo_path, "ls-tree", "-r", "--name-only", expected_sha, check=False)
    if paths.returncode != 0 or paths.stdout.splitlines() != [BOOTSTRAP_MARKER_PATH]:
        raise RulesetProvisioningError("existing Operator Store state ref contains non-bootstrap content")

    marker = _git(repo_path, "show", f"{expected_sha}:{BOOTSTRAP_MARKER_PATH}", check=False)
    if marker.returncode != 0 or marker.stdout != BOOTSTRAP_MARKER:
        raise RulesetProvisioningError("existing Operator Store bootstrap marker is not exact")

    message = _git(repo_path, "show", "-s", "--format=%B", expected_sha, check=False)
    if message.returncode != 0 or message.stdout.strip() != BOOTSTRAP_COMMIT_MESSAGE:
        raise RulesetProvisioningError("existing Operator Store bootstrap commit message is not trusted")

    if _remote_ref_sha(repo_path, remote_name, state_ref) != expected_sha:
        raise RulesetProvisioningError("existing Operator Store state ref changed after provenance proof")


def bootstrap_state_ref(
    *,
    writer_checkout: str | Path,
    remote_name: str,
    repository: str,
    state_ref: str,
    protection_receipt: ProtectionReceipt,
) -> tuple[str, bool]:
    """Create or strictly re-verify the initialization-only state ref."""
    require_protected(protection_receipt, repository=repository, state_ref=state_ref)
    repo_path = Path(writer_checkout)
    existing = _remote_ref_sha(repo_path, remote_name, state_ref)
    if existing is not None:
        _verify_existing_bootstrap_sentinel(repo_path, remote_name, state_ref, existing)
        return existing, False

    fd, index_path = tempfile.mkstemp(prefix="ai-sdlc-operator-bootstrap-index-")
    os.close(fd)
    os.unlink(index_path)
    try:
        index_env = {"GIT_INDEX_FILE": index_path}
        _git(repo_path, "read-tree", "--empty", env=index_env)
        blob = _git(repo_path, "hash-object", "-w", "--stdin", input_text=BOOTSTRAP_MARKER).stdout.strip()
        _git(
            repo_path,
            "update-index", "--add", "--cacheinfo",
            f"100644,{blob},{BOOTSTRAP_MARKER_PATH}",
            env=index_env,
        )
        tree = _git(repo_path, "write-tree", env=index_env).stdout.strip()
        commit = _git(
            repo_path,
            "commit-tree", tree,
            input_text=f"{BOOTSTRAP_COMMIT_MESSAGE}\n",
            env=BOOTSTRAP_GIT_ENV,
        ).stdout.strip()
        pushed = _git(repo_path, "push", "--porcelain", remote_name, f"{commit}:{state_ref}", check=False)
        if pushed.returncode != 0:
            raise RulesetProvisioningError("Operator Store initialization-only ref push rejected")
        confirmed = _remote_ref_sha(repo_path, remote_name, state_ref)
        if confirmed != commit:
            raise RulesetProvisioningError("Operator Store bootstrap ref did not confirm exact commit")
        _verify_existing_bootstrap_sentinel(repo_path, remote_name, state_ref, commit)
        return commit, True
    finally:
        try:
            os.unlink(index_path)
        except FileNotFoundError:
            pass


def provision_operator_store_state_ref(
    *,
    provisioner: GitHubOperatorStoreRulesetProvisioner,
    repository: str,
    state_ref: str,
    writer_checkout: str | Path,
    remote_name: str = "origin",
) -> RulesetProvisioningResult:
    """Protect the future ref, bootstrap/re-verify it, then re-prove protection."""
    writer_id, integrity_id = provisioner.ensure_rulesets(repository, state_ref)
    before = provisioner.verify_pre_targeted_protection(repository, state_ref)
    state_ref_sha, created = bootstrap_state_ref(
        writer_checkout=writer_checkout,
        remote_name=remote_name,
        repository=repository,
        state_ref=state_ref,
        protection_receipt=before,
    )
    after = provisioner.verify_pre_targeted_protection(repository, state_ref)
    if after.status != PROTECTED:
        raise RulesetProvisioningError("Operator Store protection was lost after bootstrap verification")
    return RulesetProvisioningResult(
        writer_ruleset_id=writer_id,
        integrity_ruleset_id=integrity_id,
        state_ref_sha=state_ref_sha,
        protection_receipt=after,
        created_state_ref=created,
    )
