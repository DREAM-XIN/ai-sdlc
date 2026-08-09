# Design Remediation Evidence — F-GHAW-ROLE-ROUTING-0001

Task: `F-GHAW-ROLE-ROUTING-0001-DESIGN-REMEDIATION-1`

Role: Architect

Status: PASS

## Remediated finding

DR-MAJOR-1 identified that credential **names** alone were insufficient to generate profile readiness generically because Copilot uses a trusted GitHub runtime token while other profiles use repository secrets. Without source metadata, implementation would require a Copilot/profile-name special case.

## Design change

Design v2 adds a bounded `credential_source` capability to the validated Provider Registry contract:

- `secret`
- `github-token`

All profiles receive explicit source metadata. Workflow readiness generation branches only on this capability, not profile/provider identity.

Additional constraints:

- `secret` supports primary credential plus approved aliases;
- `github-token` forbids aliases in v1;
- unknown source values fail closed;
- workflow expressions reduce source presence to booleans before Python;
- secret values remain excluded from resolver arguments/output;
- synthetic positive/negative tests cover both source types and invalid combinations.

## Review-note disposition

- RR-MINOR-1 is now fully addressed by explicit metadata-driven credential-source semantics.
- RR-MINOR-2 remains addressed through separate policy and trusted-manual selection modes.
- No provider maturity, autonomous-role, lifecycle/Gate, merge, or release authority is changed.
