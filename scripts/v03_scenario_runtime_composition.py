#!/usr/bin/env python3
"""Closed nine-slot production runtime composition for remaining Issue #221 faults.

This module deliberately does not expose Feature/ref selectors.  The caller may
name only one of the nine scenario ids frozen by Issue #310; Feature/ref identity
is derived from ``v03_scenario_fixture_pool.SLOTS`` and remains immutable.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Callable
from urllib import error, parse, request

from operator_decision_feature_truth import DurableDecisionFeatureTruthGateway, TrustedCandidateSnapshot
from operator_production_runtime import TrustedOperatorRuntimeConfig
from operator_release_feature_event_gateway import build_release_decision_event_gateway
from operator_store_model import normalize_repository
from operator_vertical_gh_aw import GhAwVerticalRoleDispatchGateway, GhAwVerticalWorkflowMap
from operator_vertical_gh_aw_actions_transport import GitHubActionsVerticalGhAwTransport, GitHubActionsWorkflowTransportConfig
from operator_vertical_gh_aw_attempt_binding import FirstAttemptDigestBoundGhAwResultSource
from operator_vertical_gh_aw_github_source import GitHubActionsGhAwResultSourceConfig, ProductionGhAwVerticalResultCollector
from operator_v03_write_runtime import build_v03_vertical_write_ready_operator_bundle
from v03_scenario_fixture_pool import SLOTS, SlotSpec, validate_inventory

_SHA40 = re.compile(r"^[0-9a-f]{40}$")
DEFAULT_BRANCH = "main"
COLLECTOR_IDENTITY = "ai-sdlc-v03-scenario-release-collector"


class V03ScenarioRuntimeCompositionError(RuntimeError):
    pass


def slot_for_scenario(scenario: str) -> SlotSpec:
    """Resolve one and only one frozen #310 slot; arbitrary Feature/ref is impossible."""
    validate_inventory()
    value = str(scenario or "")
    matches = [slot for slot in SLOTS if slot.scenario == value]
    if len(matches) != 1:
        raise V03ScenarioRuntimeCompositionError("scenario is outside the closed #310 nine-slot inventory")
    return matches[0]


class ScenarioFixtureGitHubCandidateProvider:
    """Fresh-read exactly one open non-draft PR for one frozen scenario slot."""

    def __init__(
        self,
        *,
        repository: str,
        slot: SlotSpec,
        token: str,
        api_base: str = "https://api.github.com",
        default_branch: str = DEFAULT_BRANCH,
        http_get: Callable[[str, dict[str, str]], tuple[int, object]] | None = None,
    ):
        canonical = slot_for_scenario(slot.scenario)
        if canonical != slot:
            raise V03ScenarioRuntimeCompositionError("scenario slot differs from canonical #310 inventory")
        self.repository = normalize_repository(repository)
        self.slot = canonical
        self.token = str(token or "")
        self.api_base = str(api_base or "").rstrip("/")
        self.default_branch = str(default_branch or "")
        self.http_get = http_get or self._default_get
        if not self.token:
            raise ValueError("scenario candidate provider requires trusted GitHub read token")
        if not self.api_base.startswith("https://"):
            raise ValueError("scenario candidate provider requires HTTPS GitHub API")
        if self.default_branch != DEFAULT_BRANCH:
            raise ValueError("v0.3 scenario candidate provider is bound to main")

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ai-sdlc-v03-scenario-runtime-candidate",
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
        query = parse.urlencode({
            "state": "open",
            "head": f"{owner}:{self.slot.target_ref}",
            "base": self.default_branch,
            "per_page": 100,
        })
        status, payload = self.http_get(
            f"{self.api_base}/repos/{self.repository}/pulls?{query}",
            self._headers(),
        )
        if status != 200 or not isinstance(payload, list):
            raise V03ScenarioRuntimeCompositionError(
                f"scenario fixture PR truth lookup failed closed with HTTP {status}"
            )
        exact = [
            row for row in payload
            if isinstance(row, dict)
            and row.get("state") == "open"
            and row.get("draft") is False
            and str((row.get("head") or {}).get("ref") or "") == self.slot.target_ref
            and str((row.get("base") or {}).get("ref") or "") == self.default_branch
        ]
        if len(exact) != 1:
            raise V03ScenarioRuntimeCompositionError(
                "scenario fixture must resolve exactly one open non-draft PR"
            )
        row = exact[0]
        head_repo_raw = str(((row.get("head") or {}).get("repo") or {}).get("full_name") or "")
        if not head_repo_raw:
            raise V03ScenarioRuntimeCompositionError(
                "scenario fixture PR lacks exact repository/number/head authority"
            )
        try:
            head_repo = normalize_repository(head_repo_raw)
        except Exception as exc:
            raise V03ScenarioRuntimeCompositionError(
                "scenario fixture PR lacks exact repository/number/head authority"
            ) from exc
        number = row.get("number")
        head_sha = str((row.get("head") or {}).get("sha") or "").lower()
        if head_repo != self.repository or not isinstance(number, int) or number < 1 or not _SHA40.fullmatch(head_sha):
            raise V03ScenarioRuntimeCompositionError(
                "scenario fixture PR lacks exact repository/number/head authority"
            )
        return row

    def current_candidate(self, *, operation_id: str, repository: str, feature_id: str, target_ref: str) -> TrustedCandidateSnapshot:
        if (
            not operation_id
            or normalize_repository(repository) != self.repository
            or feature_id != self.slot.feature_id
            or target_ref != self.slot.target_ref
        ):
            raise V03ScenarioRuntimeCompositionError("candidate lookup escaped the fixed scenario slot identity")
        row = self._fixture_pr()
        return TrustedCandidateSnapshot(
            candidate_pr_number=int(row["number"]),
            candidate_head_sha=str(row["head"]["sha"]).lower(),
        )


class DeferredScenarioFeatureTruthGateway:
    """One-time bridge to the exact durable FeatureTruth using the bundle Store runtime."""

    def __init__(self):
        self._delegate: DurableDecisionFeatureTruthGateway | None = None

    def bind(self, delegate: DurableDecisionFeatureTruthGateway) -> None:
        if self._delegate is not None:
            raise V03ScenarioRuntimeCompositionError("scenario FeatureTruth gateway is already bound")
        if not isinstance(delegate, DurableDecisionFeatureTruthGateway):
            raise ValueError("scenario FeatureTruth delegate must use durable production gateway")
        self._delegate = delegate

    @property
    def delegate(self) -> DurableDecisionFeatureTruthGateway:
        if self._delegate is None:
            raise V03ScenarioRuntimeCompositionError("scenario FeatureTruth gateway is not bound")
        return self._delegate

    def read_feature(self, *, operation_id: str):
        return self.delegate.read_feature(operation_id=operation_id)


@dataclass(frozen=True)
class V03ScenarioRuntimeComposition:
    scenario: str
    slot: SlotSpec
    feature_id: str
    target_ref: str
    workflows: GhAwVerticalWorkflowMap
    candidate_provider: ScenarioFixtureGitHubCandidateProvider
    feature_truth_gateway: DeferredScenarioFeatureTruthGateway
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


def build_v03_scenario_runtime_composition(
    *,
    scenario: str,
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
) -> V03ScenarioRuntimeComposition:
    """Compose exactly one frozen scenario slot through the reviewed production authority graph."""
    slot = slot_for_scenario(scenario)
    if not isinstance(config, TrustedOperatorRuntimeConfig):
        raise ValueError("trusted Operator runtime config is required")
    if normalize_repository(control_repository) != config.target_repository:
        raise ValueError("v0.3 scenario control/target repository must be identical")
    if config.feature_ids != frozenset({slot.feature_id}):
        raise ValueError("scenario runtime config must contain only the frozen slot Feature")
    if config.feature_ref(slot.feature_id) != slot.target_ref:
        raise ValueError("scenario runtime config target ref differs from frozen slot")
    if workflows.default_branch != DEFAULT_BRANCH:
        raise ValueError("scenario gh-aw workflows must be bound to main")
    if not all((adapter_id, target_read_token, actions_token, event_write_token, trusted_context_digest)):
        raise ValueError("scenario runtime composition requires explicit bounded credentials/context")
    if actions_token == event_write_token:
        raise ValueError("Actions/read and canonical Feature Event write authority must remain split")
    if not callable(clock):
        raise ValueError("scenario runtime composition requires trusted clock")
    if persist_poll_attempts < 8 or persist_poll_attempts > 120 or persist_poll_seconds < 0 or persist_poll_seconds > 30:
        raise ValueError("scenario Persist polling bounds are invalid")
    for name in ("rollout_verifier", "resolution_policy_verifier", "decision_policy_verifier"):
        if getattr(policy_authority, name, None) is None:
            raise ValueError(f"protected policy authority lacks {name}")

    feature_event_gateway = build_release_decision_event_gateway(
        token=event_write_token,
        repository=config.target_repository,
        default_branch=DEFAULT_BRANCH,
        feature_refs={slot.feature_id: slot.target_ref},
        api_base=github_api_base,
        poll_attempts=persist_poll_attempts,
        poll_seconds=persist_poll_seconds,
    )
    candidate_provider = ScenarioFixtureGitHubCandidateProvider(
        repository=config.target_repository,
        slot=slot,
        token=target_read_token,
        api_base=github_api_base,
        default_branch=DEFAULT_BRANCH,
    )
    feature_truth = DeferredScenarioFeatureTruthGateway()
    source_config = GitHubActionsGhAwResultSourceConfig(
        control_repository=control_repository,
        control_token=actions_token,
        target_token=target_read_token,
        workflows=workflows,
        collector_identity=COLLECTOR_IDENTITY,
        api_url=github_api_base,
    )
    result_source = FirstAttemptDigestBoundGhAwResultSource(source_config, target_repository=config.target_repository)
    actions_transport = GitHubActionsVerticalGhAwTransport(
        GitHubActionsWorkflowTransportConfig(
            control_repository=control_repository,
            token=actions_token,
            workflows=workflows,
            api_url=github_api_base,
        )
    )
    dispatch_gateway = GhAwVerticalRoleDispatchGateway(transport=actions_transport, workflows=workflows)
    bundle = build_v03_vertical_write_ready_operator_bundle(
        config=config,
        adapter_id=adapter_id,
        feature_id=slot.feature_id,
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
        raise V03ScenarioRuntimeCompositionError("FeatureTruth escaped the unique production Store runtime")
    if collector.result_source is not result_source or collector.callback_coordinator is not bundle.callback_coordinator:
        raise V03ScenarioRuntimeCompositionError("collector escaped the exact result/callback authority")
    if getattr(result_source.load_content, "__self__", None) is not result_source:
        raise V03ScenarioRuntimeCompositionError("collector content loader is not bound to exact result source")
    if "operation.resume" in bundle.backends:
        raise V03ScenarioRuntimeCompositionError("server-only operation.resume leaked into adapter backends")

    return V03ScenarioRuntimeComposition(
        scenario=slot.scenario,
        slot=slot,
        feature_id=slot.feature_id,
        target_ref=slot.target_ref,
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
