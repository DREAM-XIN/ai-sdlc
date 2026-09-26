# Product naming and positioning decision proposal

Tracking issue: #222

Status: **proposal for independent product review**

Recommended disposition: **QUALIFY**

## Executive conclusion

Keep `AI-SDLC` as the project's protocol/domain descriptor and compatibility namespace through v0.3, but adopt a distinct product/project brand before broader public adoption.

Do **not** bundle a product-brand decision with an immediate repository, `.ai-sdlc`, Action, Feature-state, or protocol-schema rename. Those are compatibility migrations and require a separate approved plan.

This recommendation is a product/discoverability judgment only. It makes no trademark or legal conclusion.

## Why the collision is material

The collision is no longer limited to a generic phrase appearing in isolated repositories.

`ai-sdlc-framework/ai-sdlc` publicly presents itself as **AI-SDLC Framework** and uses the `ai-sdlc.io` domain. Its published surface includes spec-driven workflows, an autonomous orchestrator, quality gates, cross-harness review, conformance tooling, agent adapters, SDKs, governance and audit concepts.

Primary public evidence:

- https://github.com/ai-sdlc-framework/ai-sdlc
- https://ai-sdlc.io/docs/getting-started
- https://pypi.org/project/ai-sdlc-framework/

A second project, `ParkerRex/ai-sdlc`, also publicly uses the AI-SDLC name for an AI-assisted structured development workflow and occupies the `ai-sdlc` PyPI project name.

Primary public evidence:

- https://github.com/ParkerRex/ai-sdlc
- https://pypi.org/project/ai-sdlc/

The practical risk is therefore search/discoverability ambiguity, package/user-support confusion, and difficulty explaining which AI-SDLC a document, issue, command, benchmark, or integration refers to.

## Why KEEP is not recommended

A pure KEEP decision would require differentiation copy to carry most of the burden while another project already uses the same principal name across a repository, website, SDK/conformance ecosystem, and highly overlapping orchestration/governance vocabulary.

The current project's differentiator is stronger than its current name: it is evolving toward a **trusted SDLC control plane** whose core safety properties are durable lifecycle authority, exact candidate evidence, independent roles, external-effect fencing, trusted Persist, bounded authorization, and session-independent operation state.

Those properties should become the product identity rather than relying on a crowded generic descriptor.

## Why an immediate RENAME is not recommended

A full rename is much larger than changing the README heading.

`AI-SDLC` / `ai-sdlc` currently appears in compatibility-sensitive surfaces including:

- `.ai-sdlc/project.yaml`;
- canonical protocol ids such as `ai-sdlc.operator/v1`;
- Feature Manifest/Event and Operation Event identifiers;
- GitHub Action and workflow filenames and commands;
- repository URLs used by production callers pinned to immutable commits;
- state paths and runtime contracts;
- documentation, examples and installation instructions.

Renaming those surfaces during the current v0.3 release-critical work would increase migration risk without improving the safety proof that v0.3 is trying to establish.

## QUALIFY strategy

### 1. Separate product brand from protocol descriptor

Use a future distinct product name as the user-facing identity, with a subtitle such as:

`<Distinct Product Name> — a trusted AI-SDLC control plane`

`AI-SDLC` can remain a generic domain/protocol descriptor, analogous to how a product can implement a protocol without sharing the protocol's name.

### 2. Preserve v0.3 compatibility namespaces

Until a migration is separately approved, preserve:

- `.ai-sdlc` configuration paths;
- `ai-sdlc.*` schema and protocol identifiers;
- existing lifecycle/Event identities;
- current Action/workflow invocation contracts;
- repository slug and immutable production pins.

A distinct product display name does not require these identifiers to change at the same time.

### 3. Select the distinct name using explicit criteria

A candidate should be:

- distinctive in GitHub and general search;
- reasonably available across package/CLI namespaces;
- oriented around trust/control/evidence rather than code generation;
- provider-neutral and not GitHub-specific;
- short enough for operator UX, CLI and docs;
- pronounceable for an international engineering team;
- broad enough to cover Operator, control-plane and future policy products;
- compatible with retaining `AI-SDLC` as a protocol descriptor.

This proposal intentionally lists naming **directions**, not an unverified final brand:

- trust/control-plane metaphor;
- evidence/gate metaphor;
- orchestration-with-authority-boundaries metaphor;
- short invented brand word paired with an `AI-SDLC control plane` subtitle.

Name availability and legal clearance are separate work.

## Migration cost inventory

### Low-to-medium cost

- README/product display name;
- documentation headings;
- release messaging and diagrams.

### Medium cost

- GitHub App display name;
- workflow display names;
- badges, links and external documentation references.

### High compatibility cost

- repository slug;
- `.ai-sdlc` directory;
- Action paths and caller references;
- protocol/schema identifiers;
- state/Event contract identifiers;
- slash-command and workflow-file prefixes.

High-cost compatibility surfaces should change only through an explicit migration contract with aliases/deprecation windows where technically possible.

## Positioning language after qualification

The current v0.3 product vision already provides the strongest differentiator:

> Models propose. AI-SDLC validates. Workers execute. GitHub records.

The distinct product should be positioned around **trusted orchestration and lifecycle authority**, not as another coding agent or generic agent workflow framework.

A concise positioning pattern is:

> A trusted control plane for AI-operated software delivery. It coordinates replaceable AI workers while keeping lifecycle authority, independent review, evidence, authorization and external-effect safety deterministic and durable.

## Decision requested from independent Product review

Choose one:

- **QUALIFY / PASS** — approve the strategy above and open a separate distinct-name selection/migration-planning issue before broad adoption;
- **KEEP / REWORK** — retain AI-SDLC as product name, with an explicit rationale for accepting discoverability/support ambiguity;
- **RENAME / REWORK** — require a full compatibility migration before v0.3/broader release and define which protocol/runtime identifiers are in scope.

No repository or compatibility identifier should be changed by this proposal alone.
