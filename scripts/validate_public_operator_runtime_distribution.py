#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import build_public_operator_runtime as builder

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts/build_public_operator_runtime.py"

REQUIRED_PATHS = {
    "requirements-operator.txt",
    "scripts/operator_api.py",
    "scripts/operator_mcp.py",
    "scripts/operator_openai_responses.py",
    "scripts/operator_openai_responses_host.py",
    "scripts/operator_openai_responses_journal.py",
    "scripts/operator_openai_responses_production.py",
    "scripts/operator_store.py",
    "scripts/operator_store_backends.py",
    "scripts/operator_store_git.py",
    "scripts/operator_store_model.py",
    "scripts/operator_store_protection.py",
    "scripts/operator_store_remote_git.py",
    "scripts/operator_store_runtime.py",
    "spec/operator/request-envelope.schema.json",
    "spec/operator/response-envelope.schema.json",
}
FORBIDDEN_PREFIXES = (
    "tests/",
    "state/",
    "docs/",
    "profiles/",
    "gates/",
    "runtimes/",
    ".github/",
)
FORBIDDEN_SCRIPT_FRAGMENTS = (
    "/validate_",
    "_conformance.py",
)
FORBIDDEN_TEXT = (
    "AI_SDLC_RUNTIME_APP_PRIVATE_KEY",
    "AI_SDLC_CONTROL_DISPATCH_TOKEN",
    "OPENAI_API_KEY=",
    "ANTHROPIC_API_KEY=",
    "GOOGLE_API_KEY=",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _declares_function(path: Path, function_name: str) -> bool:
    if not path.is_file():
        return False
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, ValueError):
        return False
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name
        for node in tree.body
    )


def expected_runtime_contract() -> tuple[set[Path], set[Path], bool]:
    roots = builder.runtime_roots()
    final_included = builder.FINAL_VERTICAL_ROOT in roots
    require(
        final_included
        == builder._declares_function(builder.FINAL_VERTICAL_ROOT, builder.FINAL_VERTICAL_FACTORY),
        "public runtime final-Vertical root selection drifted from source baseline",
    )
    scripts: set[Path] = set()
    for seed in roots:
        scripts.update(builder.python_dependencies(seed, roots=roots))
    return roots, scripts, final_included


def run_import_probe(output: Path, *, final_included: bool) -> None:
    probe = rf'''
import operator_openai_responses as responses
import operator_openai_responses_host as host
import operator_openai_responses_journal as journal
import operator_openai_responses_production as production
import operator_mcp

assert responses.ADAPTER_ID == "ai-sdlc.openai.responses"
assert len(responses.TOOLS) == 10
assert set(responses.WRITE_CAPABILITIES) == {{
    "operation.start", "operation.cancel", "decision.respond", "notification.ack"
}}
assert "operation.resume" not in responses.TOOL_CAPABILITIES.values()
assert callable(host.build_official_openai_client)
assert callable(journal.StoreResponsesCallJournal)
assert callable(production.build_openai_responses_production_bundle)
status = production.production_dependency_status()
assert set(status) == {{
    "full_vertical_production_factory", "stale_recorded_callback_convergence"
}}
assert all(type(value) is bool for value in status.values())
assert status["full_vertical_production_factory"] is {final_included!r}
assert operator_mcp.ADAPTER_ID == "ai-sdlc.mcp.stdio"
print("public Operator runtime import probe passed")
'''
    env = dict(os.environ)
    env["PYTHONPATH"] = str(output / "scripts")
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=output,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    require(result.returncode == 0, f"public Operator runtime import probe failed: {result.stdout}")


def main() -> None:
    expected_roots, expected_scripts, final_included = expected_runtime_contract()

    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "operator-runtime"
        subprocess.run([sys.executable, str(BUILDER), "--output", str(output)], check=True)

        manifest_path = output / "runtime-manifest.json"
        require(manifest_path.is_file(), "Operator runtime manifest missing")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        require(manifest.get("format") == 1, "unexpected Operator runtime manifest format")
        require(manifest.get("kind") == "ai-sdlc-operator-runtime", "Operator runtime kind drifted")
        require(
            manifest.get("source_repository") == "DREAM-XIN/ai-sdlc",
            "Operator runtime source identity drifted",
        )
        require(
            set(manifest.get("entrypoints", []))
            == {path.as_posix() for path in builder.PUBLIC_ENTRYPOINTS},
            "Operator runtime entrypoint set drifted",
        )
        require(
            set(manifest.get("runtime_roots", []))
            == {path.as_posix() for path in expected_roots},
            "Operator runtime production root set drifted from source baseline",
        )
        require(
            manifest.get("final_vertical_factory_included") is final_included,
            "Operator runtime final-Vertical manifest flag drifted from source baseline",
        )

        paths = {item["path"] for item in manifest.get("files", [])}
        missing = REQUIRED_PATHS - paths
        require(not missing, f"missing required public Operator runtime files: {sorted(missing)}")
        require("requirements-dev.txt" not in paths, "lifecycle/dev dependency file leaked into Operator bundle")

        packaged_scripts = {Path(path) for path in paths if path.startswith("scripts/") and path.endswith(".py")}
        require(
            packaged_scripts == expected_scripts,
            "packaged Operator Python closure drifted from builder dependency graph",
        )

        final_runtime = output / builder.FINAL_VERTICAL_ROOT
        if final_included:
            require(final_runtime.is_file(), "reviewed full-Vertical production root was not packaged")
            require(
                _declares_function(final_runtime, builder.FINAL_VERTICAL_FACTORY),
                "packaged final-Vertical root lacks the authoritative full production factory",
            )
            require(
                "scripts/operator_v03_vertical_production_runtime.py" in paths,
                "final production composition module is missing from public runtime closure",
            )
            require(
                "scripts/operator_vertical_feature_persist_gateway.py" in paths,
                "final durable Vertical Persist gateway is missing from public runtime closure",
            )
            require(
                "scripts/operator_vertical_reconcile_classified.py" in paths,
                "final classified Vertical recovery is missing from public runtime closure",
            )
        elif final_runtime.is_file():
            require(
                not _declares_function(final_runtime, builder.FINAL_VERTICAL_FACTORY),
                "unreviewed full-Vertical production factory entered the public bundle",
            )

        for path in paths:
            require(not path.startswith(FORBIDDEN_PREFIXES), f"private/control-only path leaked: {path}")
            require(
                not any(fragment in f"/{path}" for fragment in FORBIDDEN_SCRIPT_FRAGMENTS),
                f"test/validator module leaked into Operator runtime: {path}",
            )

        requirements = (output / "requirements-operator.txt").read_text(encoding="utf-8")
        require("mcp==2.0.0" in requirements, "accepted MCP dependency missing")
        require("openai>=2,<3" in requirements, "official OpenAI SDK compatibility range missing")
        require("jsonschema" in requirements, "canonical/Responses schema dependency missing")

        for path in output.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".md", ".txt", ".json", ".yaml", ".yml"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for token in FORBIDDEN_TEXT:
                require(token not in text, f"credential assignment leaked into Operator bundle: {token} in {path.relative_to(output)}")

        readme = (output / "README.md").read_text(encoding="utf-8")
        require("must not be described as Supported" in readme, "Supported-status blocker is not stated honestly")
        require("never falls back" in readme, "fail-closed production binding boundary missing")
        require("all hard runtime prerequisites" in readme, "hard production prerequisite boundary missing")
        if final_included:
            require(
                "baseline declares the reviewed full-Vertical production factory" in readme,
                "packaged final production composition is not described",
            )
        else:
            require(
                "baseline does not yet declare the reviewed full-Vertical production factory" in readme,
                "missing final production composition is not described",
            )

        run_import_probe(output, final_included=final_included)

        first = manifest_path.read_text(encoding="utf-8")
        subprocess.run([sys.executable, str(BUILDER), "--output", str(output)], check=True)
        second = (output / "runtime-manifest.json").read_text(encoding="utf-8")
        require(first == second, "public Operator runtime rebuild is not deterministic")

    print("Public Operator runtime distribution boundary checks passed")
    print("- lifecycle runtime remains a separate minimal bundle")
    print("- runtime roots and exact Python closure are derived from the current reviewed source baseline")
    print(f"- final full-Vertical factory included: {str(final_included).lower()}")
    print("- reviewed production composition is auto-packaged only after its exact factory exists on baseline")
    print("- durable journal and fail-closed Responses production binding remain explicit runtime roots")
    print("- test/conformance/control state and credential assignments are excluded")
    print("- Responses Supported status remains blocked until all hard runtime prerequisites and Lane B pass")


if __name__ == "__main__":
    main()
