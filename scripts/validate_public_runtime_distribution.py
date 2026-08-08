#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / 'scripts/build_public_runtime.py'

FORBIDDEN_PREFIXES = (
    'runtimes/',
    'profiles/',
    'gates/',
    'state/',
    'templates/',
    'docs/',
    '.github/workflows/',
)
FORBIDDEN_NAMES = {
    '.env',
    'engine-profiles.yaml',
    'ai-sdlc-command.yml',
}
FORBIDDEN_TEXT = (
    'AI_SDLC_RUNTIME_APP_PRIVATE_KEY',
    'AI_SDLC_CONTROL_DISPATCH_TOKEN',
    'OPENAI_API_KEY',
    'ANTHROPIC_API_KEY',
    'GOOGLE_API_KEY',
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / 'runtime'
        subprocess.run([sys.executable, str(BUILDER), '--output', str(output)], check=True)

        manifest_path = output / 'runtime-manifest.json'
        require(manifest_path.is_file(), 'runtime manifest missing')
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        require(manifest.get('format') == 1, 'unexpected runtime manifest format')
        require(manifest.get('source_repository') == 'DREAM-XIN/ai-sdlc', 'source identity drifted')

        paths = {item['path'] for item in manifest.get('files', [])}
        for required in (
            '.github/actions/control/action.yml',
            'requirements-dev.txt',
            'dispatch/default.yaml',
            'scripts/commander.py',
            'scripts/ingest_feature_event.py',
            'scripts/github_commander_transport.py',
            'scripts/validate_feature_manifest.py',
            'scripts/verify_git_write_precondition.py',
        ):
            require(required in paths, f'missing required public runtime file: {required}')

        for path in paths:
            require(not path.startswith(FORBIDDEN_PREFIXES), f'private/control-only path leaked: {path}')
            require(Path(path).name not in FORBIDDEN_NAMES, f'forbidden runtime file leaked: {path}')

        for path in output.rglob('*'):
            if not path.is_file() or path.suffix not in {'.py', '.yml', '.yaml', '.md', '.txt', '.json'}:
                continue
            text = path.read_text(encoding='utf-8', errors='ignore')
            for token in FORBIDDEN_TEXT:
                require(token not in text, f'credential/control token name leaked into public runtime: {token} in {path.relative_to(output)}')

        action = (output / '.github/actions/control/action.yml').read_text(encoding='utf-8')
        require('plan)' in action and 'bootstrap)' in action and 'persist)' in action, 'lifecycle action lost operation coverage')
        require('dispatch-gh-aw' not in action, 'autonomous command leaked into lifecycle Action')

        result = subprocess.run(
            [sys.executable, str(output / 'scripts/commander.py'), '--help'],
            cwd=output,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        require(result.returncode == 0, f'public runtime Commander cannot start: {result.stdout}')

    print('Public lifecycle runtime distribution boundary checks passed')


if __name__ == '__main__':
    main()
