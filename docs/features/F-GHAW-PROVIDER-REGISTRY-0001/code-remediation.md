# Code Review Remediation — F-GHAW-PROVIDER-REGISTRY-0001

Role: Implementation Developer

Remediation task: `F-GHAW-PROVIDER-REGISTRY-0001-CODE-REMEDIATION-1`

Target PR: `#196`

Validated remediation head: `d209746e14300ad085b9ad2dce4c51b70e7ebe93`

## Findings addressed

### CR-MAJOR-1 — credential alias false readiness

The shared Registry now rejects non-empty `credential_aliases` for `protocol: openai-compatible` profiles. This preserves the existing native Codex `OPENAI_API_KEY` / `CODEX_API_KEY` compatibility while preventing a compatible provider from reporting credential readiness based on an alias that its rendered `COPILOT_PROVIDER_API_KEY` path does not consume.

A deterministic negative fixture adds `credential_aliases` to the DeepSeek-compatible profile and requires full-Registry validation to fail on `credential_aliases`.

### CR-MINOR-1 — raw worker_source normalization

`worker_source` validation now checks the raw slash-separated segments before `PurePosixPath` normalization and rejects absolute/trailing-slash, empty, `.`, and `..` segments. Existing backslash rejection remains.

Deterministic negative fixtures cover repeated separators and embedded `./` path forms in addition to the existing traversal case.

## Validation

Remediation PR-head CI at `d209746e14300ad085b9ad2dce4c51b70e7ebe93`:

- `Validate AI-SDLC protocol` run `31307524581` — SUCCESS
- `Required PR Gate` run `31307524580` — SUCCESS
- `Validate Public Runtime Distribution` run `31307524592` — SUCCESS
- `Validate AI-SDLC gh-aw Worker Compile` run `31307524582` — SUCCESS for Copilot, Codex, Claude, Gemini, and DeepSeek

The Protocol run executes the shared Registry validator, synthetic extension proof, effective-model audit, runtime-preflight regression, command/security boundaries, cross-repository checks and the rest of the repository validation suite.

## Authority boundary

This remediation does not approve Code Review or PASS `code-gate`. Independent Code Review must re-evaluate the new head and the original findings.
