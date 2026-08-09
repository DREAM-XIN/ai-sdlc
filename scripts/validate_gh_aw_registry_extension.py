#!/usr/bin/env python3
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[1]
GENERIC_MODULES = (
    "scripts/gh_aw_provider_registry.py",
    "scripts/gh_aw_compiled_worker.py",
    "scripts/render_gh_aw_workers.py",
    "scripts/render_gh_aw_profile_surfaces.py",
    "scripts/resolve_gh_aw_engine.py",
    "scripts/gh_aw_runtime_preflight.py",
    "scripts/gh_aw_cross_repo_runtime.py",
    "scripts/gh_aw_profile_routing.py",
    "scripts/gh_aw_profile_readiness.py",
    "scripts/validate_gh_aw_effective_model_metadata.py",
)
IDENTITY_NAMES = frozenset({"provider", "profile", "profile_id", "engine_profile"})
IDENTITY_FIELDS = frozenset({"provider", "profile", "profile_id", "engine_profile"})


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_identity_expr(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id in IDENTITY_NAMES
    if isinstance(node, ast.Attribute):
        return node.attr in IDENTITY_FIELDS
    if isinstance(node, ast.Subscript):
        slice_node = node.slice
        return isinstance(slice_node, ast.Constant) and slice_node.value in IDENTITY_FIELDS
    return False


def contains_string_literal(node: ast.AST) -> bool:
    return any(
        isinstance(child, ast.Constant) and isinstance(child.value, str)
        for child in ast.walk(node)
    )


def pattern_contains_string(pattern: ast.pattern) -> bool:
    return any(
        isinstance(child, ast.MatchValue)
        and isinstance(child.value, ast.Constant)
        and isinstance(child.value.value, str)
        for child in ast.walk(pattern)
    )


def identity_branch_violations(source: str, filename: str) -> list[str]:
    tree = ast.parse(source, filename=filename)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            for left, right in zip(operands, operands[1:]):
                if (is_identity_expr(left) and contains_string_literal(right)) or (
                    is_identity_expr(right) and contains_string_literal(left)
                ):
                    violations.append(
                        f"{filename}:{node.lineno}: provider/profile identity compared to literal"
                    )
                    break
        elif isinstance(node, ast.Match) and is_identity_expr(node.subject):
            for case in node.cases:
                if pattern_contains_string(case.pattern):
                    violations.append(
                        f"{filename}:{node.lineno}: provider/profile identity matched to literal"
                    )
                    break
    return violations


def run(root: Path, *args: str) -> str:
    proc = subprocess.run(
        [sys.executable, *args],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )
    return proc.stdout.strip()


def build_fixture(root: Path, token: str) -> dict[str, str]:
    for relative in GENERIC_MODULES:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    for relative in (
        ".github/workflows/ai-sdlc-gh-aw-worker.md",
        ".github/workflows/ai-sdlc-gh-aw-preflight.yml",
        ".github/workflows/ai-sdlc-gh-aw-dispatch-profile.yml",
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)

    profile_id = f"fixture-{token}"
    provider_id = f"provider-{token}"
    model = f"fixture-model-{token}"
    host = f"api-{token}.example.invalid"
    credential = f"FIXTURE_{token.upper()}_API_KEY"
    source = f".github/workflows/ai-sdlc-gh-aw-worker-{profile_id}.md"
    lock = f"ai-sdlc-gh-aw-worker-{profile_id}.lock.yml"

    registry = {
        "version": "0.1.0",
        "profiles": {
            profile_id: {
                "engine": "copilot",
                "provider": provider_id,
                "protocol": "openai-compatible",
                "provider_type": "openai",
                "wire_api": "responses",
                "base_url": f"https://{host}/openai/v1",
                "network_host": host,
                "model": model,
                "worker_source": source,
                "worker_workflow": lock,
                "credential": credential,
                "credential_source": "secret",
                "maturity": "experimental",
            }
        },
    }
    registry_path = root / "runtimes/gh-aw/engine-profiles.yaml"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")
    (root / source).touch()

    runtime = {
        "version": "0.1.0",
        "id": "gh-aw",
        "engine_profile_registry": "runtimes/gh-aw/engine-profiles.yaml",
        "default_engine_profile": profile_id,
        "default_worker_workflow": lock,
    }
    (root / "runtimes/gh-aw/runtime.yaml").write_text(
        yaml.safe_dump(runtime, sort_keys=False), encoding="utf-8"
    )
    return {
        "profile": profile_id,
        "provider": provider_id,
        "model": model,
        "credential": credential,
        "source": source,
        "lock": lock,
    }


def main() -> int:
    before = {relative: digest(ROOT / relative) for relative in GENERIC_MODULES}
    aggregate = hashlib.sha256(
        "".join(before[key] for key in sorted(before)).encode()
    ).hexdigest()
    token = aggregate[:10]

    positive = '''\
COMPATIBILITY_BASELINE = {"deepseek": ("copilot", "experimental")}
if protocol == "openai-compatible":
    pass
if engine == "copilot":
    pass
'''
    negative = '''\
if provider == "deepseek":
    pass
if profile_id == "fixture-provider":
    pass
'''
    require(
        not identity_branch_violations(positive, "positive-fixture.py"),
        "AST guard rejected capability/test-only baseline fixture",
    )
    require(
        identity_branch_violations(negative, "negative-fixture.py"),
        "AST guard failed to reject provider/profile literal branches",
    )

    for relative in GENERIC_MODULES:
        source = (ROOT / relative).read_text(encoding="utf-8")
        violations = identity_branch_violations(source, relative)
        require(not violations, "; ".join(violations))

    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        fixture = build_fixture(workspace, token)
        for value in (fixture["profile"], fixture["provider"]):
            for relative in GENERIC_MODULES:
                require(
                    value not in (ROOT / relative).read_text(encoding="utf-8"),
                    f"synthetic identity leaked into generic production module: {relative}",
                )

        run(workspace, "scripts/render_gh_aw_workers.py", "--profile", fixture["profile"])
        run(
            workspace,
            "scripts/render_gh_aw_workers.py",
            "--profile",
            fixture["profile"],
            "--check",
        )

        metadata = {
            "schema_version": "v4",
            "compiler_version": "v0.83.4",
            "strict": True,
            "agent_id": "copilot",
            "agent_model": fixture["model"],
            "engine_versions": {"copilot": "fixture"},
        }
        lock_text = (
            "# gh-aw-metadata: "
            + json.dumps(metadata, separators=(",", ":"))
            + "\n"
            + f'GH_AW_INFO_MODEL: "{fixture["model"]}"\n'
            + f'GH_AW_ENGINE_MODEL: "{fixture["model"]}"\n'
        )
        (workspace / ".github/workflows" / fixture["lock"]).write_text(
            lock_text, encoding="utf-8"
        )

        resolved = json.loads(
            run(
                workspace,
                "scripts/resolve_gh_aw_engine.py",
                fixture["profile"],
                "--json",
            )
        )
        require(
            resolved["provider"] == fixture["provider"]
            and resolved["model"] == fixture["model"]
            and resolved["worker_workflow"] == fixture["lock"],
            "synthetic profile resolution drifted",
        )

        missing = json.loads(
            run(
                workspace,
                "scripts/gh_aw_runtime_preflight.py",
                fixture["profile"],
                "--credential-present",
                "false",
            )
        )
        require(
            missing["status"] == "MISSING_CREDENTIAL",
            "synthetic missing credential was accepted",
        )
        present = json.loads(
            run(
                workspace,
                "scripts/gh_aw_runtime_preflight.py",
                fixture["profile"],
                "--credential-present",
                "true",
            )
        )
        require(
            present["status"] == "READY_FOR_ENTITLEMENT_PROBE"
            and present["entitlement_verified"] is False,
            "synthetic static preflight semantics drifted",
        )

        worker = json.loads(
            run(
                workspace,
                "scripts/gh_aw_cross_repo_runtime.py",
                "worker",
                fixture["lock"],
            )
        )
        require(
            worker["status"] == "VALID" and worker["profile"] == fixture["profile"],
            "synthetic worker was not admitted by exact Registry allowlist",
        )
        run(workspace, "scripts/validate_gh_aw_effective_model_metadata.py")
        run(workspace, "scripts/render_gh_aw_profile_surfaces.py")
        run(workspace, "scripts/render_gh_aw_profile_surfaces.py", "--check")

    after = {relative: digest(ROOT / relative) for relative in GENERIC_MODULES}
    require(before == after, "synthetic extension proof modified generic production modules")
    print(
        "gh-aw synthetic Registry extension and provider/profile anti-special-case checks passed: "
        + token
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
