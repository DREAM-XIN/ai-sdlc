#!/usr/bin/env python3
"""Build the deterministic public AI-SDLC Operator runtime bundle.

This bundle is separate from the minimal lifecycle runtime. It exports supported
Operator protocol/runtime modules and their local import closure, but never test
fixtures, validators, private state, Feature artifacts, workflows, or credentials.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = Path("requirements-operator.txt")
VERSION = Path("VERSION")
FINAL_VERTICAL_ROOT = Path("scripts/operator_v03_write_runtime.py")
FINAL_VERTICAL_FACTORY = "build_v03_vertical_write_ready_operator_bundle"

PUBLIC_ENTRYPOINTS = {
    Path("scripts/operator_mcp.py"),
    Path("scripts/operator_openai_responses_host.py"),
}

BASE_RUNTIME_ROOTS = PUBLIC_ENTRYPOINTS | {
    Path("scripts/operator_openai_responses_journal.py"),
    Path("scripts/operator_openai_responses_production.py"),
}

STATIC_TREES = (Path("spec/operator"),)
FORBIDDEN_SCRIPT_MARKERS = ("validate_", "_conformance")


def _declares_function(path: Path, function_name: str) -> bool:
    source = ROOT / path
    if not source.is_file():
        return False
    try:
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, ValueError):
        return False
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name
        for node in tree.body
    )


def runtime_roots() -> set[Path]:
    """Include final full-Vertical composition only when it is truly on baseline."""

    roots = set(BASE_RUNTIME_ROOTS)
    if _declares_function(FINAL_VERTICAL_ROOT, FINAL_VERTICAL_FACTORY):
        roots.add(FINAL_VERTICAL_ROOT)
    return roots


def local_script_for(module: str) -> Path | None:
    if not module:
        return None
    top = module.split(".")[0]
    candidate = Path("scripts") / f"{top}.py"
    return candidate if (ROOT / candidate).is_file() else None


def python_dependencies(seed: Path, *, roots: set[Path]) -> set[Path]:
    pending = [seed]
    seen: set[Path] = set()
    while pending:
        rel = pending.pop()
        if rel in seen:
            continue
        source = ROOT / rel
        if not source.is_file():
            raise SystemExit(f"missing public Operator runtime dependency: {rel}")
        name = source.name
        if rel not in roots and any(marker in name for marker in FORBIDDEN_SCRIPT_MARKERS):
            raise SystemExit(f"test/conformance module entered production Operator closure: {rel}")
        seen.add(rel)
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(rel))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                candidate = local_script_for(node.module or "")
                if candidate and candidate not in seen:
                    pending.append(candidate)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    candidate = local_script_for(alias.name)
                    if candidate and candidate not in seen:
                        pending.append(candidate)
    return seen


def copy_file(rel: Path, output: Path) -> None:
    source = ROOT / rel
    if not source.is_file():
        raise SystemExit(f"missing public Operator runtime file: {rel}")
    target = output / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build(output: Path) -> dict:
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    roots = runtime_roots()
    scripts: set[Path] = set()
    for seed in roots:
        scripts.update(python_dependencies(seed, roots=roots))

    for rel in sorted({REQUIREMENTS, VERSION, *scripts}, key=str):
        copy_file(rel, output)

    for tree in STATIC_TREES:
        source = ROOT / tree
        if not source.is_dir():
            raise SystemExit(f"missing public Operator runtime tree: {tree}")
        shutil.copytree(source, output / tree)

    final_vertical_included = FINAL_VERTICAL_ROOT in roots
    readme = output / "README.md"
    readme.write_text(
        "# AI-SDLC Operator Runtime Distribution\n\n"
        "Generated from the reviewed private `DREAM-XIN/ai-sdlc` control repository. "
        "This bundle is distinct from the minimal lifecycle runtime and contains only "
        "production Operator protocol/runtime code plus canonical Operator schemas.\n\n"
        "Current bundle entrypoints include the accepted read-only MCP stdio adapter and "
        "the OpenAI Responses protocol/host foundation. The protected-Store-backed durable "
        "Responses call journal and fail-closed production binding are explicit runtime roots. "
        "The production binding never falls back to the semantic-only/test composition. "
        + (
            "The baseline declares the reviewed full-Vertical production factory, so its root "
            "and static production import closure are included in this bundle. "
            if final_vertical_included
            else "The baseline does not yet declare the reviewed full-Vertical production factory, "
            "so no unavailable authority is copied into this bundle. "
        )
        + "The write-capable Responses adapter must not be described as Supported until all hard "
        "runtime prerequisites are present and mandatory production conformance passes.\n\n"
        "Provider credentials, protected Store credentials/configuration, Feature state, test "
        "fixtures, validators, lifecycle artifacts, and control workflows are not included. "
        "Supply credentials and trusted runtime configuration externally.\n",
        encoding="utf-8",
    )

    files = sorted(path for path in output.rglob("*") if path.is_file())
    manifest = {
        "format": 1,
        "kind": "ai-sdlc-operator-runtime",
        "source_repository": "DREAM-XIN/ai-sdlc",
        "entrypoints": [path.as_posix() for path in sorted(PUBLIC_ENTRYPOINTS, key=str)],
        "runtime_roots": [path.as_posix() for path in sorted(roots, key=str)],
        "final_vertical_factory_included": final_vertical_included,
        "files": [
            {"path": path.relative_to(output).as_posix(), "sha256": sha256(path)}
            for path in files
        ],
    }
    (output / "runtime-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = build(args.output.resolve())
    print(f"built public Operator runtime with {len(manifest['files'])} files")


if __name__ == "__main__":
    main()
