# Operator Store trusted provisioning

Issue: #241

## Why this exists

The accepted Operation Store design requires production semantic writes to remain disabled until the trusted Store state ref is positively proven protected.

Classic GitHub branch protection can express an explicit app push restriction for organization-owned repositories. GitHub documents that branch restriction actors are organization-only, so a user-owned repository cannot rely on the existing `restrictions.apps` proof shape.

Repository branch rulesets are available for public personal repositories and can target a branch name before that branch exists. AI-SDLC therefore supports two protection proof modes:

1. classic branch protection for repositories where an exact Operator App push restriction can be proven;
2. layered repository rulesets for personal repositories when trusted installation/control knows the numeric Operator GitHub App Integration id.

Neither mode is selected by a Feature branch, AI client request, Worker result, callback, or task payload.

## Layered ruleset design

The ruleset path intentionally uses two independent active rulesets for the exact state ref.

### Writer ruleset

Target: `refs/heads/ai-sdlc-operator-state`

Rules:

- restrict creation;
- restrict update with the exact parameter `update_allows_fetch_and_merge: false`.

Bypass list:

- exactly one `Integration` actor;
- actor id equals the trusted Operator GitHub App integration id;
- bypass mode is `always` so bypass use remains auditable.

No other User, Team, RepositoryRole, Integration, DeployKey, or administrator bypass is accepted by the verifier. The update rule is not accepted merely because its type is `update`: missing, malformed, additional/ambiguous, or permissive update parameters fail closed as `UNKNOWN`.

### Integrity ruleset

Target: the same exact state ref.

Rules:

- restrict deletion;
- block non-fast-forward updates.

Bypass list: empty.

The integrity ruleset is deliberately separate from the writer ruleset. Giving the Operator App a bypass on a combined ruleset would also let it bypass deletion/non-fast-forward rules, weakening the accepted Store invariant.

## Trusted proof

`scripts/operator_store_github_ruleset_protection.py` uses GitHub's active-rules-for-branch endpoint to identify every active rule applying to the exact branch name. GitHub supports this query even when the branch does not yet exist.

For each relevant repository ruleset, the verifier then fetches the authoritative ruleset detail and requires `bypass_actors`, repository provenance, and security-significant update-rule parameters to be positively visible. GitHub may omit fields when the caller lacks sufficient ruleset access; omission is `UNKNOWN`, never an implicit safe value.

A ruleset proof is `PROTECTED` only when all of the following hold:

- active creation and update restrictions apply;
- every active update rule has exactly `{"update_allows_fetch_and_merge": false}`;
- every active creation/update ruleset is bypassable by exactly the configured Operator Integration and nobody else;
- active deletion and non-fast-forward rules apply;
- for each integrity rule type, at least one applying ruleset has no bypass actors;
- every inspected ruleset has `source_type: Repository` and exact `owner/repo` source provenance;
- every inspected ruleset is an active repository branch ruleset;
- the verifier has enough trusted access to inspect all required fields.

Missing, inherited, unsupported, mismatched, or ambiguous provenance fails closed as `UNKNOWN`.

## Provisioning authority separation

`scripts/operator_store_github_ruleset_provision.py` is installation/control tooling, not Operator runtime functionality.

It requires two distinct authority surfaces:

- **repository administration credential**: creates/updates the two repository rulesets and reads their protected configuration;
- **Operator writer checkout credential**: performs the one initialization-only Git push and later bounded Store CAS pushes.

The provisioning function deliberately does not accept a Git token argument for the writer checkout. Git authentication is supplied externally by trusted installation configuration, so the repository-admin token is not silently reused as the durable Store writer credential.

Workers and AI clients receive neither credential.

## Safe Mode A bootstrap

The supported personal-repository bootstrap sequence is:

1. trusted installation/control creates or reconciles the writer and integrity rulesets for the future exact state ref;
2. trusted ruleset verification proves the future branch name is `PROTECTED`;
3. the separately authenticated Operator writer checkout checks whether the state ref already exists;
4. if absent, it creates one root commit containing only `state/operator/v1/.bootstrap`, with exact marker bytes, exact bootstrap commit message, and the workflow-owned trusted author/committer identity;
5. it pushes that commit to the protected state ref without force and re-reads the exact remote SHA;
6. if the ref already exists, provisioning **does not adopt it by presence alone**: it fetches and verifies that the tip is exactly the same one-commit capabilityless bootstrap root shape, with no parent, no second commit, no additional path, no semantic Store JSON, exact marker bytes, exact commit message, and exact trusted author/committer identity;
7. any pre-seeded semantic content, unknown/mismatched identity, extra path, or multi-commit history is refused. Migration/quarantine of pre-existing Store history is a separate future reviewed authority path, not an implicit provisioning behavior;
8. trusted control verifies the exact ref is still `PROTECTED` after bootstrap/re-verification;
9. only then may normal Operator Store semantic writes use the production runtime.

The `.bootstrap` marker is not JSON, so `RemoteGitStateRefBackend.read_snapshot()` does not materialize it as semantic Store state. There is no Operation event, reservation, claim, Decision, Notification, launch, cancellation, or Persist fact in the bootstrap commit.

Provisioning is idempotent only for the exact trusted bootstrap sentinel and the two named rulesets. It intentionally fails closed instead of silently re-provisioning or adopting an already-evolved semantic Store history.

## Deterministic validation

`.github/workflows/operator-store-ruleset-protection.yml` runs the strict ruleset/provenance regression independently of the live provisioning workflow. It proves, among other cases:

- exact `update_allows_fetch_and_merge: false` succeeds;
- `true`, missing, or ambiguous update parameters fail closed;
- an absent ref can be bootstrapped with workflow-owned Git identity;
- the exact trusted bootstrap sentinel can be safely re-verified;
- the same sentinel shape under an untrusted Git identity is rejected;
- pre-seeded semantic JSON is rejected;
- multi-commit pre-existing history is rejected;
- prior repository-provenance, exclusive-writer, no-bypass-integrity, and runtime regressions remain green.

These tests are deterministic implementation evidence only; they are not live provisioning evidence.

## Live-production boundary

Code and deterministic tests for this provisioning path do not authorize a Feature PR to provision the live repository. A live install requires independently reviewed code on the trusted default branch plus explicit trusted installation/control credentials and the configured Operator Integration id.

Until the live state ref exists and its protection verifies as `PROTECTED`, production Store-backed writes and v0.3 release dogfood that requires durable state remain blocked.
