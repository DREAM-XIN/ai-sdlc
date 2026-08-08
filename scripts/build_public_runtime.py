#!/usr/bin/env python3
"""Build a deterministic public AI-SDLC lifecycle runtime bundle.

The private control repository remains the source of truth. This builder exports only
what the target-local plan/bootstrap/persist Action needs. Autonomous workers,
provider configuration, control workflows, repository state, and project docs are
not copied into the distribution.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ACTION = Path('.github/actions/control/action.yml')
REQUIREMENTS = Path('requirements-dev.txt')
DEFAULT_POLICY = Path('dispatch/default.yaml')
VERSION = Path('VERSION')

SCRIPT_SEEDS = {
    Path('scripts/commander.py'),
    Path('scripts/github_commander_transport.py'),
    Path('scripts/validate_feature_manifest.py'),
    Path('scripts/ingest_feature_event.py'),
    Path('scripts/verify_git_write_precondition.py'),
}

# Protocol schemas are data dependencies rather than Python imports.
STATIC_TREES = (Path('spec'),)


def local_script_for(module: str) -> Path | None:
    """Resolve a top-level import to a repository-local scripts module."""
    if not module:
        return None
    top = module.split('.')[0]
    candidate = Path('scripts') / f'{top}.py'
    return candidate if (ROOT / candidate).is_file() else None


def python_dependencies(seed: Path) -> set[Path]:
    pending = [seed]
    seen: set[Path] = set()
    while pending:
        rel = pending.pop()
        if rel in seen:
            continue
        source = ROOT / rel
        if not source.is_file():
            raise SystemExit(f'missing public-runtime script dependency: {rel}')
        seen.add(rel)
        tree = ast.parse(source.read_text(encoding='utf-8'), filename=str(rel))
        for node in ast.walk(tree):
            module: str | None = None
            if isinstance(node, ast.ImportFrom):
                module = node.module
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    candidate = local_script_for(alias.name)
                    if candidate and candidate not in seen:
                        pending.append(candidate)
                continue
            candidate = local_script_for(module or '')
            if candidate and candidate not in seen:
                pending.append(candidate)
    return seen


def copy_file(rel: Path, output: Path) -> None:
    source = ROOT / rel
    if not source.is_file():
        raise SystemExit(f'missing public-runtime file: {rel}')
    target = output / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def build(output: Path) -> dict:
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    scripts: set[Path] = set()
    for seed in SCRIPT_SEEDS:
        scripts.update(python_dependencies(seed))

    for rel in sorted({ACTION, REQUIREMENTS, DEFAULT_POLICY, VERSION, *scripts}, key=str):
        copy_file(rel, output)

    for tree in STATIC_TREES:
        source = ROOT / tree
        if not source.is_dir():
            raise SystemExit(f'missing public-runtime tree: {tree}')
        shutil.copytree(source, output / tree)

    readme = output / 'README.md'
    readme.write_text(
        '# AI-SDLC Runtime Distribution\n\n'
        'Generated from the private `DREAM-XIN/ai-sdlc` control repository. '\
        'This repository contains only the reviewed lifecycle runtime required by '\
        '`plan`, `bootstrap`, and `persist`. It intentionally excludes autonomous '\
        'workers, provider credentials/configuration, control-repository workflows, '\
        'Feature state, and project documentation.\n\n'
        'Consume `.github/actions/control` by immutable commit SHA.\n',
        encoding='utf-8',
    )

    files = sorted(path for path in output.rglob('*') if path.is_file())
    manifest = {
        'format': 1,
        'source_repository': 'DREAM-XIN/ai-sdlc',
        'files': [
            {
                'path': path.relative_to(output).as_posix(),
                'sha256': sha256(path),
            }
            for path in files
        ],
    }
    (output / 'runtime-manifest.json').write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + '\n', encoding='utf-8'
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    manifest = build(args.output.resolve())
    print(f"built public lifecycle runtime with {len(manifest['files'])} files")


if __name__ == '__main__':
    main()
