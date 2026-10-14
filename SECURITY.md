# Security policy

Snowbeam is alpha software. Managed agent lifecycle (reviewed plan/apply, vault
writes, runtime handoff, rotation, and revocation) is implemented and covered by
isolated automated tests. Validate any change in a dedicated Snowflake test
account with your real `snow`, `op` or `bw`, and runtime before production use.
Cloud workload authentication and desktop integration still need broader
end-to-end validation.

## Report privately

Email [hello@reunionstudio.io](mailto:hello@reunionstudio.io) with the subject
`Snowbeam security report`. Describe the affected version, platform, prerequisites,
impact, and a sanitized reproduction. Do not post access-control vulnerabilities
or credential exposures in public issues.

Do not include passwords, tokens, private keys, vault exports, or unredacted
Snowflake configuration. If an actual credential has been exposed, revoke or
rotate it through its owner; sending a report does not revoke it.

During alpha development, fixes target the next alpha release. There is no
long-term maintenance commitment for earlier alpha versions.

## Boundaries

Snowflake authenticates users and enforces permissions. Supported vault adapters
resolve managed credentials. The chosen runtime runs the agent. Snowbeam does
not provide operating-system isolation or configure cloud IAM trust.

The metadata inventory is private to the filesystem user, not encrypted. It
contains identifiers, file paths, vault references, and operation evidence, not
managed secret values. Secrets can pass through the executing process and
restricted temporary files. Existing secrets in a Snowflake configuration remain
in that file and its backup. See the [identity and security guide](docs/security.md)
for the full behavior, partial-failure recovery, and validation boundary.
