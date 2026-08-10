#!/usr/bin/env python3
"""Concrete GitHub branch-protection verifier for Operator Store state refs."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from operator_store_protection import PROTECTED, UNKNOWN, UNPROTECTED, ProtectionReceipt


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _digest(value) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _default_get(url: str, headers: dict[str, str]) -> tuple[int, dict]:
    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=15) as response:  # noqa: S310 - trusted GitHub API URL
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = {}
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except Exception:
            pass
        return exc.code, body
    except (URLError, TimeoutError, OSError):
        return 0, {}


class GitHubBranchProtectionVerifier:
    """Prove protection using GitHub's trusted branch-protection API.

    This verifier intentionally accepts only trusted constructor configuration. It
    returns PROTECTED only when GitHub positively reports force-push/deletion disabled
    and push restrictions include the configured Operator GitHub App. Rulesets that
    cannot be represented by this endpoint fail closed as UNKNOWN/UNPROTECTED rather
    than being guessed safe.
    """

    test_only = False

    def __init__(
        self,
        *,
        token: str,
        operator_app_slug: str,
        api_base: str = "https://api.github.com",
        http_get: Callable[[str, dict[str, str]], tuple[int, dict]] = _default_get,
        clock: Callable[[], str] = _utc_now,
    ):
        if not token:
            raise ValueError("trusted GitHub token is required for protection verification")
        if not operator_app_slug:
            raise ValueError("trusted Operator GitHub App slug is required")
        if not api_base.startswith("https://"):
            raise ValueError("GitHub protection API base must use HTTPS")
        self.token = token
        self.operator_app_slug = operator_app_slug
        self.api_base = api_base.rstrip("/")
        self.http_get = http_get
        self.clock = clock

    def verify(self, repository: str, state_ref: str) -> ProtectionReceipt:
        if not state_ref.startswith("refs/heads/"):
            return ProtectionReceipt(repository, state_ref, UNKNOWN, "github-branch-protection", self.clock(), None)
        branch = state_ref[len("refs/heads/"):]
        url = f"{self.api_base}/repos/{repository}/branches/{quote(branch, safe='')}/protection"
        status, payload = self.http_get(
            url,
            {
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "ai-sdlc-operator-store",
            },
        )
        verified_at = self.clock()
        if status == 404:
            return ProtectionReceipt(repository, state_ref, UNPROTECTED, "github-branch-protection", verified_at, None)
        if status != 200 or not isinstance(payload, dict):
            return ProtectionReceipt(repository, state_ref, UNKNOWN, "github-branch-protection", verified_at, None)

        allow_force = bool((payload.get("allow_force_pushes") or {}).get("enabled", False))
        allow_delete = bool((payload.get("allow_deletions") or {}).get("enabled", False))
        restrictions = payload.get("restrictions")
        if not isinstance(restrictions, dict):
            return ProtectionReceipt(repository, state_ref, UNPROTECTED, "github-branch-protection", verified_at, _digest(payload))
        app_slugs = {
            str(item.get("slug"))
            for item in (restrictions.get("apps") or [])
            if isinstance(item, dict) and item.get("slug")
        }
        protected = (not allow_force) and (not allow_delete) and self.operator_app_slug in app_slugs
        return ProtectionReceipt(
            repository=repository,
            state_ref=state_ref,
            status=PROTECTED if protected else UNPROTECTED,
            verifier_identity=f"github-branch-protection:{self.operator_app_slug}",
            verified_at=verified_at,
            policy_digest=_digest(payload),
        )
