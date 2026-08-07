# Dogfooding Findings — Feature #8

Meta issue: #24

## What worked
- Separating Requirement, Design, Plan, Implementation, Review, Verification and Acceptance created clear traceability.
- Deterministic Gate/Evidence rules prevented worker self-report from being treated as completion.
- Bounded work units exposed scope and dependency ownership before coding.
- ChatGPT Web/manual execution remains viable because durable state lives outside the conversation.

## Friction discovered

### 1. Too many bookkeeping Issues
Creating a distinct GitHub Issue for every gate evidence record is too noisy. A production reference implementation should prefer:
- one Feature issue;
- sub-issues only for real work units or independently actionable reviews;
- repository artifacts for canonical Requirement/Design/Review/Test documents;
- GitHub Project fields/comments/checks for state and evidence indexes.

Gate evidence itself should not require a dedicated Issue.

### 2. Independent review needs stronger runtime separation
This dogfood run produced separate review artifacts, but it was executed within the same overall assistant session. That tests protocol mechanics, not true reviewer independence. Production guidance should require a separate conversation/runtime/model for independent review when risk warrants it.

### 3. Bootstrap PR is becoming too broad
Feature #8 currently lives inside the large bootstrap PR #7. Future dogfood work should branch from merged protocol foundations and use one feature PR per independently reviewable change.

### 4. Execution state is a useful missing primitive
The original six primitives were insufficient to cleanly represent manual transport progress. `TaskExecution` should be considered a seventh protocol primitive, or Execution should become a clearly defined sub-resource of Task in a future protocol review.

### 5. Schema validation and semantic validation are distinct
JSON Schema is effective for shape and state-specific required fields, while transition history and stable identity are better enforced by deterministic semantic validators. The protocol should explicitly distinguish structural validation from semantic validation.

## Actions
- Feed bookkeeping simplification into #1 protocol hardening.
- Require separate review worker guidance in reviewer role/risk profiles.
- After bootstrap merge, move to feature-scoped branches/PRs.
- Review whether `TaskExecution` is promoted to a first-class protocol primitive.
- Add formal semantic-validator guidance to the specification.
