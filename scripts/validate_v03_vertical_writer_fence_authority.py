#!/usr/bin/env python3
"""Auditable trusted-main proof that legacy/raw Vertical writers are fenced."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from operator_effect_rollout import REQUIRED_FENCED_CAPABILITIES
from operator_store_model import digest_json

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_PATH = ROOT / "evidence/v03-vertical-writer-fence-proof.json"

RAW_SURFACES = {
    "plan_semantic_reservation": {"scripts/operator_store.py"},
    "plan_dispatch_claim": {
        "scripts/operator_store.py",
        "scripts/operator_effect_lineage_fences.py",
        "scripts/operator_vertical_executor.py",
    },
    "plan_authorize_launch": {
        "scripts/operator_store.py",
        "scripts/operator_effect_lineage_fences.py",
        "scripts/operator_vertical_executor.py",
    },
    "plan_vertical_semantic_reservation": {
        "scripts/operator_vertical_store.py",
        "scripts/operator_vertical_executor.py",
    },
    "plan_external_create_attempt": {
        "scripts/operator_external_create_attempt.py",
        "scripts/operator_effect_lineage_fences.py",
    },
}
AUDITED_FILES = (
    "scripts/operator_vertical_runtime.py",
    "scripts/operator_vertical_executor.py",
    "scripts/operator_effect_rollout.py",
    "scripts/operator_effect_lineage_fences.py",
    "scripts/operator_effect_lineage_integration.py",
    "scripts/operator_external_create_attempt.py",
    "scripts/operator_external_create_gateway.py",
    "scripts/operator_vertical_store.py",
    "scripts/operator_store.py",
    "scripts/validate_operator_effect_lineage_v2.py",
    "scripts/validate_operator_external_create_fence.py",
    "scripts/validate_operator_external_create_fence_supplemental.py",
)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    require(result.returncode == 0, "unable to resolve audited trusted-main commit")
    sha = result.stdout.strip().lower()
    require(
        len(sha) == 40 and all(ch in "0123456789abcdef" for ch in sha),
        "invalid audited commit SHA",
    )
    return sha


def production_scripts():
    for path in sorted((ROOT / "scripts").glob("*.py")):
        name = path.name
        if name.startswith("validate_") or name.startswith("render_") or name.startswith("run_v03_"):
            continue
        if name.startswith("v03_"):
            continue
        yield path


def validate_raw_surface_allowlist():
    observed = {name: set() for name in RAW_SURFACES}
    for path in production_scripts():
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        for name, allowed in RAW_SURFACES.items():
            if name in text:
                require(
                    rel in allowed,
                    f"unexpected production raw-writer reference: {name} in {rel}",
                )
                observed[name].add(rel)
    for name, allowed in RAW_SURFACES.items():
        require(
            observed[name] == allowed,
            f"raw-writer surface changed for {name}: {observed[name]}",
        )

    workflow_hits = []
    for path in sorted((ROOT / ".github/workflows").glob("*.y*ml")):
        text = path.read_text(encoding="utf-8")
        for name in RAW_SURFACES:
            if name in text:
                workflow_hits.append(f"{path.relative_to(ROOT).as_posix()}:{name}")
        if "legacy_compatibility_mode" in text:
            workflow_hits.append(
                f"{path.relative_to(ROOT).as_posix()}:legacy_compatibility_mode"
            )
    require(not workflow_hits, f"workflow exposes raw/legacy writer surface: {workflow_hits}")

    legacy_prod = []
    for path in production_scripts():
        if "legacy_compatibility_mode=True" in path.read_text(encoding="utf-8"):
            legacy_prod.append(path.relative_to(ROOT).as_posix())
    require(not legacy_prod, f"production script enables legacy compatibility: {legacy_prod}")
    return {name: sorted(paths) for name, paths in observed.items()}


def _call_name(node):
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def _lineage_guard_elses(function):
    guarded = set()
    for node in ast.walk(function):
        if not isinstance(node, ast.If):
            continue
        try:
            test = ast.unparse(node.test)
        except Exception:
            continue
        if test != "self.config.effect_lineage_required":
            continue
        for statement in node.orelse:
            guarded.update(id(child) for child in ast.walk(statement))
    return guarded


def validate_executor_branching():
    path = ROOT / "scripts/operator_vertical_executor.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    dispatch = next(
        node
        for class_node in tree.body
        if isinstance(class_node, ast.ClassDef)
        and class_node.name == "TrustedVerticalExecutor"
        for node in class_node.body
        if isinstance(node, ast.FunctionDef) and node.name == "_dispatch"
    )
    guarded_else_nodes = _lineage_guard_elses(dispatch)
    raw_calls = {
        "plan_vertical_semantic_reservation",
        "plan_dispatch_claim",
        "plan_authorize_launch",
    }
    safe_calls = {
        "plan_lineage_gated_reservation",
        "plan_lineage_dispatch_claim",
        "plan_lineage_authorize_launch",
    }
    seen_raw = set()
    seen_safe = set()
    for node in ast.walk(dispatch):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name in raw_calls:
            seen_raw.add(name)
            require(
                id(node) in guarded_else_nodes,
                f"{name} escaped explicit non-lineage fallback",
            )
        if name in safe_calls:
            seen_safe.add(name)
    require(seen_raw == raw_calls, f"expected raw fallback set changed: {seen_raw}")
    require(seen_safe == safe_calls, f"lineage-aware production writer set changed: {seen_safe}")


def validate_external_create_writer_chain():
    attempt = (ROOT / "scripts/operator_external_create_attempt.py").read_text(encoding="utf-8")
    fences = (ROOT / "scripts/operator_effect_lineage_fences.py").read_text(encoding="utf-8")
    gateway = (ROOT / "scripts/operator_external_create_gateway.py").read_text(encoding="utf-8")
    runtime = (ROOT / "scripts/operator_vertical_runtime.py").read_text(encoding="utf-8")
    rollout = (ROOT / "scripts/operator_effect_rollout.py").read_text(encoding="utf-8")

    require(
        "def plan_external_create_attempt(" in attempt
        and 'StoreMutation("create_immutable", path, value)' in attempt,
        "durable raw external-create attempt writer changed",
    )
    require(
        "def plan_lineage_external_create_attempt(" in fences
        and "assert_lineage_member(snapshot, semantic_effect_key)" in fences
        and "plan_external_create_attempt(" in fences,
        "raw external-create attempt is not wrapped by the lineage-aware writer fence",
    )
    require(
        "class StoreBackedOneShotExternalCreateGateway" in gateway
        and "plan_lineage_external_create_attempt(" in gateway
        and "receipt = _normalize_receipt(self.delegate.launch(dispatch=dispatch))" in gateway,
        "one-shot external-create gateway composition changed",
    )
    require(
        "StoreBackedOneShotExternalCreateGateway(" in runtime,
        "production Vertical runtime does not install the one-shot external-create gateway",
    )
    require(
        '"raw-external-create-attempt"' in rollout,
        "raw external-create attempt capability is absent from the frozen fenced capability set",
    )


def validate_production_composition():
    runtime = (ROOT / "scripts/operator_vertical_runtime.py").read_text(encoding="utf-8")
    executor = (ROOT / "scripts/operator_vertical_executor.py").read_text(encoding="utf-8")
    require(
        "plan_guard=EffectLineageWriteFence(rollout)" in runtime,
        "production Vertical Store runtime does not install EffectLineageWriteFence",
    )
    require(
        'raise ValueError("test-only Effect Lineage rollout cannot enable production vertical runtime")'
        in runtime,
        "production runtime no longer rejects test-only rollout authority",
    )
    require(
        "effect_lineage_required=rollout.effect_lineage_required" in runtime
        and "writer_fence_receipt_digest=rollout.writer_fence_receipt_digest" in runtime,
        "production executor is not bound to verified rollout/fence material",
    )
    require(
        "legacy_compatibility_mode=True" not in runtime,
        "production runtime enables legacy compatibility",
    )
    require(
        "non-lineage vertical execution is allowed only in explicit test-only legacy compatibility mode"
        in executor,
        "executor no longer fail-closes non-lineage production configuration",
    )
    validate_external_create_writer_chain()


def run_adversarial_fence_validator():
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts/validate_operator_effect_lineage_v2.py")],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    require(
        completed.returncode == 0,
        "accepted Effect Lineage adversarial fence validator failed:\n"
        + completed.stdout[-4000:],
    )
    return hashlib.sha256(completed.stdout.encode()).hexdigest()


def run_external_create_fence_validators():
    digests = {}
    for rel in (
        "scripts/validate_operator_external_create_fence.py",
        "scripts/validate_operator_external_create_fence_supplemental.py",
    ):
        completed = subprocess.run(
            [sys.executable, str(ROOT / rel)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        require(
            completed.returncode == 0,
            f"accepted external-create fence validator failed: {rel}\n"
            + completed.stdout[-4000:],
        )
        digests[rel] = hashlib.sha256(completed.stdout.encode()).hexdigest()
    return digests


def main():
    head = git_head()
    raw_surfaces = validate_raw_surface_allowlist()
    validate_executor_branching()
    validate_production_composition()
    adversarial_stdout_digest = run_adversarial_fence_validator()
    external_create_validator_digests = run_external_create_fence_validators()
    file_digests = {path: sha256_file(ROOT / path) for path in AUDITED_FILES}
    material = {
        "schema_version": "ai-sdlc.vertical-writer-fence-proof/v1",
        "installation_commit_sha": head,
        "fenced_capabilities": sorted(REQUIRED_FENCED_CAPABILITIES),
        "raw_writer_surface_files": raw_surfaces,
        "workflow_raw_writer_entrypoints": [],
        "production_legacy_compatibility_entrypoints": [],
        "effect_lineage_write_fence_installed": True,
        "external_create_one_shot_fence_installed": True,
        "adversarial_fence_validator": {
            "path": "scripts/validate_operator_effect_lineage_v2.py",
            "stdout_digest": adversarial_stdout_digest,
        },
        "external_create_fence_validators": external_create_validator_digests,
        "audited_file_digests": file_digests,
    }
    evidence = {**material, "proof_digest": digest_json(material)}
    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_PATH.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(evidence, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
