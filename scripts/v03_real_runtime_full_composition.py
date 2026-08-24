#!/usr/bin/env python3
"""Release-only full-runtime composition for v0.3 Issue #221 fault injection.

This module creates no separate Store, Persist, dispatch, or Feature authority. It
binds the fixed release fixture to the reviewed production components already on
trusted main. Construction alone performs no Worker dispatch; live execution stays
behind the trusted-main prerequisite/fault-injection workflow.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Callable
from urllib import error, parse, request

from operator_decision_feature_truth import (
    DurableDecisionFeatureTruthGateway,
    TrustedCandidateSnapshot,
)
from operator_production_runtime import TrustedOperatorRuntimeConfig
from operator_release_feature_event_gateway import build_release_decision_event_gateway
from operator_store_model import normalize_repository
from operator_vertical_gh_aw import GhAwVerticalRoleDispatchGateway, GhAwVerticalWorkflowMap
from operator_vertical_gh_aw_actions_transport import (
    GitHubActionsVerticalGhAwTransport,
    GitHubActionsWorkflowTransportConfig,
)
from operator_vertical_gh_aw_attempt_binding import FirstAttemptDigestBoundGhAwResultSource
from operator_vertical_gh_aw_github_source import (
    GitHubActionsGhAwResultSourceConfig,
    ProductionGhAwVerticalResultCollector,
)
from operator_v03_write_runtime import build_v03_vertical_write_ready_operator_bundle
from provision_v03_real_runtime_fixture import FEATURE_ID as FIXTURE_FEATURE_ID, TARGET_REF as FIXTURE_TARGET_REF

_SHA40 = re.compile(r"^[0-9a-f]{40}$")
DEFAULT_BRANCH = "main"
COLLECTOR_IDENTITY = "ai-sdlc-v03-real-runtime-release-collector"


class V03RealRuntimeCompositionError(RuntimeError):
    pass


class FixedFixtureGitHubCandidateProvider:
    """Fresh-read the one exact open PR for the frozen #221 fixture branch."""

    def __init__(
        self,
        *,
        repository: str,
        token: str,
        api_base: str = "https://api.github.com",
        default_branch: str = DEFAULT_BRANCH,
        http_get: Callable[[str, dict[str, str]], tuple[int, object]] | None = None,
    ):
        self.repository = normalize_repository(repository)
        self.token = str(token or "")
        self.api_base = str(api_base or "").rstrip("/")
        self.default_branch = str(default_branch or "")
        self.http_get = http_get or self._default_get
        if not self.token:
            raise ValueError("fixture candidate provider requires trusted GitHub read token")
        if not self.api_base.startswith("https://"):
            raise ValueError("fixture candidate provider requires HTTPS GitHub API")
        if self.default_branch != DEFAULT_BRANCH:
            raise ValueError("v0.3 fixture candidate provider is bound to main")

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ai-sdlc-v03-real-runtime-candidate",
        }

    @staticmethod
    def _default_get(url: str, headers: dict[str, str]) -> tuple[int, object]:
        req = request.Request(url, headers=headers, method="GET")
        try:
            with request.urlopen(req, timeout=30) as response:
                raw = response.read()
                return int(response.status), json.loads(raw.decode("utf-8")) if raw else []
        except error.HTTPError as exc:
            return int(exc.code), []
        except Exception:
            return 0, []

    def _fixture_pr(self) -> dict[str, Any]:
        owner = self.repository.split("/", 1)[0]
        query = parse.urlencode(
            {
                "state": "open",
                "head": f"{owner}:{FIXTURE_TARGET_REF}",
                "base": self.default_branch,
                "per_page": 100,
            }
        )
        url = f"{self.api_base}/repos/{self.repository}/pulls?{query}"
        status, payload = self.http_get(url, self._headers())
        if status != 200 or not isinstance(payload, list):
            raise V03RealRuntimeCompositionError(
                f"fixture PR truth lookup failed closed with HTTP {status}"
            )
        exact = [
            row
            for row in payload
            if isinstance(row, dict)
            and row.get("state") == "open"
            and row.get("draft") is False
            and str((row.get("head") or {}).get("ref") or "") == FIXTURE_TARGET_REF
            and str((row.get("base") or {}).get("ref") or "") == self.default_branch
        ]
        if len(exact) != 1:
            raise V03RealRuntimeCompositionError(
                "fixed fixture must resolve exactly one open non-draft PR"
            )
        row = exact[0]
        head_repo_raw = str(((row.get("head") or {}).get("repo") or {}).get("full_name") or "")
        if not head_repo_raw:
            raise V03RealRuntimeCompositionError(
                "fixed fixture PR lacks exact repository/number/head authority"
            )
        try:
            head_repo = normalize_repository(head_repo_raw)
        except Exception as exc:
            raise V03RealRuntimeCompositionError(
                "fixed fixture PR lacks exact repository/number/head authority"
            ) from exc
        number = row.get("number")
        head_sha = str((row.get("head") or {}).get("sha") or "").lower()
        if (
            head_repo != self.repository
            or not isinstance(number, int)
            or number < 1
            or not _SHA40.fullmatch(head_sha)
        ):
            raise V03RealRuntimeCompositionError(
                "fixed fixture PR lacks exact repository/number/head authority"
            )
        return row

    def current_candidate(
        self,
        *,
        operation_id: str,
        repository: str,
        feature_id: str,
        target_ref: str,
    ) -> TrustedCandidateSnapshot:
        if (
            not operation_id
            or normalize_repository(repository) != self.repository
            or feature_id != FIXTURE_FEATURE_ID
            or target_ref != FIXTURE_TARGET_REF
        ):
            raise V03RealRuntimeCompositionError(
                "candidate lookup escaped the fixed #221 fixture identity"
            )
        row = self._fixture_pr()
        return TrustedCandidateSnapshot(
            candidate_pr_number=int(row["number"]),
            candidate_head_sha=str(row["head"]["sha"]).lower(),
        )


class DeferredFixtureFeatureTruthGateway:
    """One-time fail-closed bridge to the exact Store runtime created by the bundle."""

    def __init__(self):
        self._delegate: DurableDecisionFeatureTruthGateway | None = None

    def bind(self, delegate: DurableDecisionFeatureTruthGateway) -> None:
        if self._delegate is not None:
            raise V03RealRuntimeCompositionError("fixture FeatureTruth gateway is already bound")
        if not isinstance(delegate, DurableDecisionFeatureTruthGateway):
            raise ValueError("fixture FeatureTruth delegate must use durable production gateway")
        self._delegate = delegate

    @property
    def delegate(self) -> DurableDecisionFeatureTruthGateway:
        if self._delegate is None:
            raise V03RealRuntimeCompositionError("fixture FeatureTruth gateway is not bound")
        return self._delegate

    def read_feature(self, *, operation_id: str):
        return self.delegate.read_feature(operation_id=operation_id)


@dataclass(frozen=True)
class V03RealRuntimeFullComposition:
    feature_id: str
    target_ref: str
    workflows: GhAwVerticalWorkflowMap
    candidate_provider: FixedFixtureGitHubCandidateProvider
    feature_truth_gateway: DeferredFixtureFeatureTruthGateway
    feature_event_gateway: Any
    actions_transport: GitHubActionsVerticalGhAwTransport
    dispatch_gateway: GhAwVerticalRoleDispatchGateway
    result_source: FirstAttemptDigestBoundGhAwResultSource
    bundle: Any
    collector: ProductionGhAwVerticalResultCollector
    policy_authority: Any

    @property
    def runtime(self):
        return self.bundle.runtime

    @property
    def operation_start(self):
        return self.bundle.backends["operation.start"]


def build_v03_real_runtime_full_composition(
    *,
    config: TrustedOperatorRuntimeConfig,
    adapter_id: str,
    target_read_token: str,
    actions_token: str,
    event_write_token: str,
    control_repository: str,
    workflows: GhAwVerticalWorkflowMap,
    protection_verifier: Any,
    policy_authority: Any,
    trusted_context_digest: str,
    collector_namespace_policy: str,
    trusted_role_policy: str,
    clock: Callable[[], Any],
    github_api_base: str = "https://api.github.com",
    persist_poll_attempts: int = 60,
    persist_poll_seconds: float = 2.0,
) -> V03RealRuntimeFullComposition:
    """Compose the fixed #221 fixture through one real production authority graph."""
    if not isinstance(config, TrustedOperatorRuntimeConfig):
        raise ValueError("trusted Operator runtime config is required")
    if normalize_repository(control_repository) != config.target_repository:
        raise ValueError("v0.3 real-runtime control/target repository must be identical")
    if config.feature_ids != frozenset({FIXTURE_FEATURE_ID}):
        raise ValueError("real-runtime composition must be scoped only to fixed fixture Feature")
    if config.feature_ref(FIXTURE_FEATURE_ID) != FIXTURE_TARGET_REF:
        raise ValueError("real-runtime composition target ref differs from fixed fixture")
    if workflows.default_branch != DEFAULT_BRANCH:
        raise ValueError("real-runtime gh-aw workflows must be bound to main")
    if not all((adapter_id, target_read_token, actions_token, event_write_token, trusted_context_digest)):
        raise ValueError("real-runtime composition requires explicit bounded credentials/context")
    if actions_token == event_write_token:
        raise ValueError("Actions/read authority and canonical Feature Event write authority must remain split")
    if not callable(clock):
        raise ValueError("real-runtime composition requires trusted clock")
    if persist_poll_attempts < 8 or persist_poll_attempts > 120:
        raise ValueError("Persist polling attempts must remain bounded at 8..120")
    if persist_poll_seconds < 0 or persist_poll_seconds > 30:
        raise ValueError("Persist polling interval is invalid")
    for name in ("rollout_verifier", "resolution_policy_verifier", "decision_policy_verifier"):
        if getattr(policy_authority, name, None) is None:
            raise ValueError(f"protected policy authority lacks {name}")

    feature_event_gateway = build_release_decision_event_gateway(
        token=event_write_token,
        repository=config.target_repository,
        default_branch=DEFAULT_BRANCH,
        feature_refs={FIXTURE_FEATURE_ID: FIXTURE_TARGET_REF},
        api_base=github_api_base,
        poll_attempts=persist_poll_attempts,
        poll_seconds=persist_poll_seconds,
    )

    candidate_provider = FixedFixtureGitHubCandidateProvider(
        repository=config.target_repository,
        token=target_read_token,
        api_base=github_api_base,
        default_branch=DEFAULT_BRANCH,
    )
    feature_truth = DeferredFixtureFeatureTruthGateway()

    source_config = GitHubActionsGhAwResultSourceConfig(
        control_repository=control_repository,
        control_token=actions_token,
        target_token=target_read_token,
        workflows=workflows,
        collector_identity=COLLECTOR_IDENTITY,
        api_url=github_api_base,
    )
    result_source = FirstAttemptDigestBoundGhAwResultSource(
        source_config,
        target_repository=config.target_repository,
    )
    actions_transport = GitHubActionsVerticalGhAwTransport(
        GitHubActionsWorkflowTransportConfig(
            control_repository=control_repository,
            token=actions_token,
            workflows=workflows,
            api_url=github_api_base,
        )
    )
    dispatch_gateway = GhAwVerticalRoleDispatchGateway(
        transport=actions_transport,
        workflows=workflows,
    )

    bundle = build_v03_vertical_write_ready_operator_bundle(
        config=config,
        adapter_id=adapter_id,
        feature_id=FIXTURE_FEATURE_ID,
        target_read_token=target_read_token,
        protection_verifier=protection_verifier,
        rollout_verifier=policy_authority.rollout_verifier,
        resolution_policy_verifier=policy_authority.resolution_policy_verifier,
        feature_gateway=feature_truth,
        feature_event_gateway=feature_event_gateway,
        dispatch_gateway=dispatch_gateway,
        collector_content_loader=result_source.load_content,
        policy_verifier=policy_authority.decision_policy_verifier,
        trusted_context_digest=trusted_context_digest,
        collector_namespace_policy=collector_namespace_policy,
        trusted_role_policy=trusted_role_policy,
        github_api_base=github_api_base,
        clock=clock,
    )

    durable_truth = DurableDecisionFeatureTruthGateway(
        runtime=bundle.runtime,
        feature_gateway=feature_event_gateway,
        candidate_provider=candidate_provider,
    )
    feature_truth.bind(durable_truth)

    collector = ProductionGhAwVerticalResultCollector(
        callback_coordinator=bundle.callback_coordinator,
        result_source=result_source,
        workflows=workflows,
        control_repository=control_repository,
        clock=clock,
    )

    if durable_truth.runtime is not bundle.runtime:
        raise V03RealRuntimeCompositionError("FeatureTruth escaped the unique production Store runtime")
    if collector.result_source is not result_source:
        raise V03RealRuntimeCompositionError("collector does not share the first-attempt result source")
    if collector.callback_coordinator is not bundle.callback_coordinator:
        raise V03RealRuntimeCompositionError("collector escaped the bundle callback coordinator")
    if getattr(result_source.load_content, "__self__", None) is not result_source:
        raise V03RealRuntimeCompositionError("collector content loader is not bound to exact result source")
    if "operation.resume" in bundle.backends:
        raise V03RealRuntimeCompositionError("server-only operation.resume leaked into adapter backends")

    return V03RealRuntimeFullComposition(
        feature_id=FIXTURE_FEATURE_ID,
        target_ref=FIXTURE_TARGET_REF,
        workflows=workflows,
        candidate_provider=candidate_provider,
        feature_truth_gateway=feature_truth,
        feature_event_gateway=feature_event_gateway,
        actions_transport=actions_transport,
        dispatch_gateway=dispatch_gateway,
        result_source=result_source,
        bundle=bundle,
        collector=collector,
        policy_authority=policy_authority,
    )
