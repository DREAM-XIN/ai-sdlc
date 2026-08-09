#!/usr/bin/env python3
from pathlib import Path

from gh_aw_compiled_worker import load_compiled_worker
from gh_aw_provider_registry import load_registry

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github/workflows"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    registry = load_registry()
    compatible = registry.openai_compatible_profiles()
    require(compatible, "at least one OpenAI-compatible profile must be registered")

    audited = []
    for profile in compatible:
        model = profile.model
        require(model is not None, f"{profile.profile_id}: compatible profile model missing")
        source = (ROOT / profile.worker_source).read_text(encoding="utf-8")
        compiled = load_compiled_worker(profile, WORKFLOW_DIR)

        require(
            f'  model: "{model}"\n' in source,
            f"{profile.profile_id}: source must pin engine.model for gh-aw audit metadata",
        )
        require(
            f"    COPILOT_MODEL: {model}\n" in source,
            f"{profile.profile_id}: source must pin COPILOT_MODEL for provider routing",
        )
        require(
            f"    COPILOT_PROVIDER_BASE_URL: {profile.base_url}\n" in source,
            f"{profile.profile_id}: source provider base URL drifted from Registry",
        )
        require(
            f"    COPILOT_PROVIDER_TYPE: {profile.provider_type}\n" in source,
            f"{profile.profile_id}: source provider type drifted from Registry",
        )
        require(
            f"    COPILOT_PROVIDER_WIRE_API: {profile.wire_api}\n" in source,
            f"{profile.profile_id}: source wire API drifted from Registry",
        )
        require(
            f'GH_AW_INFO_MODEL: "{model}"' in compiled.text,
            f"{profile.profile_id}: compiled run metadata must report the Registry model",
        )
        require(
            f'GH_AW_ENGINE_MODEL: "{model}"' in compiled.text,
            f"{profile.profile_id}: compiled telemetry must report the Registry model",
        )
        require(
            compiled.metadata.get("agent_model") == model,
            f"{profile.profile_id}: compiled metadata agent_model drifted from Registry",
        )
        audited.append(profile.profile_id)

    print(
        "gh-aw OpenAI-compatible effective model routing and audit metadata checks passed: "
        + ", ".join(audited)
    )


if __name__ == "__main__":
    main()
