# Protocol versioning

AI-SDLC protocol documents use semantic versions: `MAJOR.MINOR.PATCH`.

## Compatibility rules

### Patch
Backward-compatible clarification or validation fix that does not require valid existing documents to change.

Examples:
- documentation clarification;
- validator bug fix that aligns with already documented behavior;
- new non-normative example.

### Minor
Backward-compatible protocol extension. Existing valid documents remain valid and new fields/capabilities are optional unless a new profile explicitly opts into them.

Examples:
- optional field;
- new artifact or evidence type;
- new runtime capability;
- new workflow profile.

### Major
Breaking change. Existing valid documents may require migration.

Examples:
- removing or renaming a required field;
- changing state or transition semantics incompatibly;
- making an optional field required for existing profiles;
- changing an identifier's meaning.

## Structural and semantic validation

AI-SDLC deliberately separates two validators:

1. **Structural validation** uses JSON Schema for shape, required fields, enums and local conditional constraints.
2. **Semantic validation** checks cross-object and state-machine invariants that JSON Schema cannot express clearly, such as referenced IDs existing, transitions being legal, or a workflow marked DONE having all required gates passed.

A protocol implementation claiming conformance must run both layers for objects that have semantic rules.

## Version negotiation

A consumer must reject unsupported major versions. Consumers should accept newer compatible minor/patch versions only when their implementation explicitly declares that compatibility range.

Reference implementations initially support `0.1.x` only. Pre-1.0 minor versions may still evolve quickly; breaking changes must be called out explicitly in release notes and migration guidance even when the project remains pre-1.0.
