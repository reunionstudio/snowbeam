# Connections, identities, and agent access

Snowbeam keeps the connection catalog and intended setup. Snowflake enforces
access. Your password manager holds PATs and private keys. A runtime runs the
agent. These are separate responsibilities.

Use Snowbeam on the manager's machine to inspect and provision. Send each agent
only its own runtime bundle. Do not distribute the consultant's full Snowflake
configuration, provisioning connection, or password-manager session.

## A consultant working across clients

Existing Snowflake profiles appear automatically. Label the profiles you want
to track, then inspect their current security metadata:

```sh
snowbeam identities track acme-jane --connection acme-work --kind person \
  --client Acme --owner Jane --runtime consultant-laptop
snowbeam identities track northwind-jane --connection northwind-work --kind person \
  --client Northwind --owner Jane --runtime consultant-laptop
snowbeam identities refresh acme-jane
snowbeam identities list --client Acme --json
snowbeam identities show acme-jane --json
```

Labels are local intent, not Snowflake grants. The account shown after a
successful connection comes from Snowflake. A shared vault reference on distinct
tracked identities is flagged, including when the inventory is filtered by client.

Inspection reads the user type and disabled state, directly granted roles and
privileges, effective authentication policy, user/account network policy,
network-rule contents, and PAT metadata. When applicable, it also reads public
key metadata and workload identity bindings. Template roles get a direct-grants
snapshot. `identities refresh NAME --history` additionally reads the latest user
login from `ACCOUNT_USAGE.LOGIN_HISTORY`; it needs privileges and can be delayed.

Missing permissions, unrecognized policy formats, failed reads, and checks older
than 24 hours stay visible. A failed read retains earlier evidence with its
original timestamp. `Inspected` means these scoped reads succeeded. `Matches
template` means the inspected values match the recorded setup; neither label
means the account passed a complete security audit.

The inspector does not expand the full inherited role graph, resolve every
security-integration override, inspect cloud IAM, or attribute all queries to a
particular agent. SAML/OAuth/security-integration network paths require separate
inspection. The UI displays these limits alongside the evidence.

## Reusable setup for individual agents

Give every agent a distinct Snowflake `SERVICE_AGENT` user and individual PAT,
key pair, or workload identity. Templates share a role and policy configuration;
they do not share credentials. Each template is bound to an exact organization,
account, and existing administrative connection.

The role, authentication policy, and network policy must already exist. An
administrator or an authorized AI can create them using Snowflake's tools after
reviewing the required access and runtime egress. Snowbeam does not infer grants
from a profile's `role` field, allocate a static IP, or create cloud IAM policies.
The administrator must have the Snowflake privileges required by each statement.

For example, this is a **blueprint to adapt and review**, not a command Snowbeam
runs automatically. Replace the database/schema/object names and the example
address with the actual runtime's approved egress. Add only required role grants:

```sql
CREATE ROLE REPORTING_AGENT;
GRANT USAGE ON DATABASE REPORTING TO ROLE REPORTING_AGENT;
GRANT USAGE ON SCHEMA REPORTING.PUBLIC TO ROLE REPORTING_AGENT;
GRANT SELECT ON VIEW REPORTING.PUBLIC.DAILY_SUMMARY TO ROLE REPORTING_AGENT;
GRANT USAGE ON WAREHOUSE AGENT_WH TO ROLE REPORTING_AGENT;

CREATE AUTHENTICATION POLICY SECURITY.POLICIES.AGENT_PAT_ONLY
  AUTHENTICATION_METHODS = ('PROGRAMMATIC_ACCESS_TOKEN')
  PAT_POLICY = (
    NETWORK_POLICY_EVALUATION = ENFORCED_REQUIRED
    MAX_EXPIRY_IN_DAYS = 30
  );
CREATE NETWORK POLICY REPORTING_RUNNERS
  ALLOWED_IP_LIST = ('198.51.100.10/32');
```

Record the approved setup. Use the 1Password account and vault identifiers from
your own installation. Nothing in these two commands changes Snowflake:

```sh
snowbeam templates add acme-reporting \
  --organization ACME --account PROD --admin-connection acme-admin \
  --role REPORTING_AGENT --warehouse AGENT_WH \
  --authentication-policy SECURITY.POLICIES.AGENT_PAT_ONLY \
  --network-policy REPORTING_RUNNERS --auth PAT --days-to-expiry 30 \
  --provider onepassword --vault VAULT_ID --vault-account ACCOUNT_ID

snowbeam identities add sam --template acme-reporting --user AGENT_SAM \
  --client Acme --owner Jane --purpose 'Daily reporting' --runtime runner-east
```

Repeat `identities add` for each agent. Names, owners, runtimes, and users remain
individual. The CLI and JSON output let an authorized AI or script manage many
entries without a separate Snowbeam server. Templates can be listed or edited
with `templates list` and `templates edit NAME`. Inspect the resulting file before
using an AI-generated template.

Review the live plan, then use its exact approval digest:

```sh
snowbeam identities plan sam --json
snowbeam identities apply sam --approve APPROVAL_DIGEST --json
```

Planning performs metadata reads. The plan identifies the organization, account,
admin identity, target user, role grants, policy contents, credential lifetime,
vault destination, and steps. Applying repeats the preflight and rejects a changed
plan. Each operation includes a server-side account/user guard in the same CLI
session as its statement. Built-in administrative workload roles, broad
allow-all network addresses, unknown PAT expiry limits, and authentication
policies permitting another method block provisioning.

Provisioning creates a disabled user, grants the existing role, attaches and
checks the policies, creates the individual credential, verifies the vault write,
adds the standard connection, and enables the user. Its local status remains
`awaiting_verification` until a runtime test succeeds. Default secondary roles
are empty. No existing user is adopted or overwritten. Changing a template does
not silently change a provisioned user; inspection reports drift, and changed
account/user/authentication targets require a separate entry or deliberate recovery.

## Vault adapters

Install and authorize the chosen provider's CLI: [1Password CLI](https://developer.1password.com/docs/cli/)
or [Bitwarden CLI](https://bitwarden.com/help/cli/). Snowbeam does not log you in,
unlock a vault, or store its master password. Use a vault identity with the
required access; the CLI's own authentication remains your responsibility.

1Password takes an explicit account and vault. Bitwarden templates use
`--provider bitwarden --vault-server https://vault.bitwarden.com --vault-user USER_UUID`
in place of the 1Password fields. Obtain the server and user UUID from `bw status`.
The active unlocked server/user must match exactly. New Bitwarden items go into
that user's personal vault; organization collections are not implemented.

Existing credentials can be referenced without copying their values:

```sh
snowbeam credentials bind acme-jane --provider onepassword \
  --vault VAULT_ID --vault-account ACCOUNT_ID --item IMMUTABLE_ITEM_ID \
  --credential-kind PAT --token-field token
```

For a Bitwarden login item's password, use `--token-field password`. For a key
pair, use `--credential-kind KEYPAIR --key-field FIELD_ID --passphrase-field FIELD_ID`.
1Password selectors accept field IDs or `section/field`; ambiguous names fail.
Use immutable item IDs. Managed agents use their lifecycle commands for changes
to credential references.

New PATs are minted by Snowflake with a role restriction and expiry. New keys are
RSA 3072-bit key pairs generated by OpenSSL, with an encrypted PKCS#8 private key
and a random passphrase. Snowbeam verifies each vault item with a separate read
before recording its reference. It stores fingerprints and item IDs, not secrets,
in the persistent inventory. Keys currently use Snowflake's two legacy public-key
slots; these keys have no automatic server expiry. `--days-to-expiry` applies to
PATs. Schedule key rotation explicitly.

Credentials pass transiently through Snowbeam and the provider CLI. Connection
execution uses a private temporary configuration and mode-0600 credential files,
with Snowflake CLI file logging disabled. Files are removed when the context
exits normally; a forced kill or host crash can leave a private temporary directory
for the operating system/operator to clean up. Python process memory is not
securely zeroized. Existing secrets in your original Snowflake config and backups
remain there.

## Test from the agent's runtime

Export the one-agent handoff from the manager:

```sh
snowbeam runtime export sam --out sam-runtime.json
```

Transfer it to the intended runtime through your normal deployment process. It
contains one profile, the expected organization/account/user/role, and either a
vault reference or workload binding. It contains no admin profile or credential
values. The runtime still needs its own authorization to read that vault item,
or its assigned cloud workload identity.

On that runtime, with Snowbeam and Snowflake CLI installed:

```sh
snowbeam runtime test sam-runtime.json --out sam-test.json
snowbeam runtime exec sam-runtime.json -- snow sql -c sam -q 'select current_user()'
```

The test connects as the agent, checks the exact account/user/role, and records
Snowflake's observed client IP and test time. The runtime name is an operator
label, not host attestation. `runtime test` and `runtime exec` do not read the
manager's connection catalog.

Return the report to the manager, review it, then explicitly accept it:

```sh
snowbeam identities accept-test sam sam-test.json
snowbeam identities accept-test sam sam-test.json --approve REPORT_APPROVAL_DIGEST
```

Only accept a report obtained from a trusted runtime/operator. Reports are
operator-supplied JSON, not signed Snowflake proof. Their digest detects changes
and binds them to the current handoff; it does not establish who produced them.
Reports older than 24 hours, from the future, or for another user, role, or
credential revision are rejected. Acceptance records this source explicitly.

If the manager itself is the declared runtime, `identities verify sam --runtime
runner-east` performs a direct Snowflake test. For an existing referenced profile,
`snowbeam exec acme-jane -- snow sql -c acme-work -q 'select current_user()'` resolves
its secret only for that child process.

The child environment omits inherited `SNOWFLAKE_*`, `SNOWSQL_*`, `OP_*`, and
`BW_*` credentials before adding the single connection. This is **not operating
system isolation**. An agent running as the same OS user may still read that
user's files and other credentials. Use separate OS users, containers, or a
restricted runner, and scope its vault/cloud access accordingly. Do not run an
untrusted agent under the consultant's login and rely on a connection name for
isolation.

## Rotation, revocation, and recovery

```sh
snowbeam identities plan sam --action rotate
snowbeam identities apply sam --action rotate --approve APPROVAL_DIGEST
# Export the new handoff, test on the runtime, and accept its report.
snowbeam identities plan sam --action retire-old
snowbeam identities apply sam --action retire-old --approve APPROVAL_DIGEST
```

Rotation creates a replacement while retaining the working credential. Runtime
verification promotes the replacement and keeps the previous reference. A
separate reviewed `retire-old` operation removes the old PAT or owned public-key
slot. Snowbeam never overwrites an occupied/unrecognized key slot. It retains
vault items for deliberate operator cleanup.

`--action revoke` disables the agent and removes only its managed credentials;
it does not drop the user, role, policy, local metadata, or vault items. Disabling
sign-in does not promise termination of existing sessions. Handle active-session
termination through Snowflake when necessary.

Inspect `snowbeam identities show sam --json` and `snowbeam operations --identity sam`.
Each apply operation records completed steps and failures. Snowflake changes and
vault/config writes cannot be one transaction. If a vault write fails after PAT
creation, Snowbeam attempts to remove that new PAT and records whether cleanup
was confirmed. A newly created user remains disabled when setup fails before
enabling it. Uncertain changes and partially provisioned users require recovery;
they are not silently retried or treated as working agents. Keep the local state
and journal: deleting them does not undo Snowflake or vault changes.

For a partially created managed user, review a `revoke` plan where available.
Otherwise inspect the recorded target and credential name in Snowflake, disable
the intended user if needed, and resolve incomplete steps with an authorized
administrator. Never clear the journal just to bypass a blocked retry. If a
rotation fails, verify the old credential before relying on it and reconcile
any pending credential before starting another rotation.

## Workload identity federation

Use `--auth WIF` and omit vault fields when the runtime already has a suitable
cloud identity. The authentication policy must allow only `WORKLOAD_IDENTITY`;
the template still requires the approved network policy. Provisioning binds and
verifies the existing workload identity without generating a long-lived secret.

For example, for a separately configured AWS IAM role:

```sh
snowbeam identities add cloud-sam --template acme-wif --user CLOUD_SAM \
  --client Acme --owner Jane --runtime aws-reports \
  --workload-provider AWS \
  --workload-subject arn:aws:iam::123456789012:role/sam
```

AWS uses an exact IAM ARN. Azure uses its managed identity object ID plus HTTPS
issuer. GCP uses the service account's numeric unique ID. OIDC uses an exact
subject, HTTPS issuer, account-scoped audience, and absolute path to the runtime's
projected token file. The CLI flags are `--workload-provider`, `--workload-subject`,
`--workload-issuer`, `--workload-audience`, and `--workload-token-file`.

Snowbeam does not provision cloud runtimes, IAM trust, token projection, or
password-manager service accounts. The installed Snowflake driver must support
the selected provider, and the runtime must satisfy Snowflake's prerequisites.
The provider renews short-lived credentials; Snowbeam's PAT/key rotation commands
do not apply to WIF. Revocation disables the user and removes its managed binding.
See Snowflake's [workload identity documentation](https://docs.snowflake.com/en/user-guide/workload-identity-federation).

## Shared configuration and AI tools

`connections.toml` is the standard connection catalog used by Snowflake CLI and
Cortex Code. During provisioning, Snowbeam consolidates legacy `[connections.*]`
tables into the adjacent file while preserving original config settings and
private backups. The companion `snowbeam.toml` holds templates, identity labels,
and vault references. `--fleet-config PATH` selects another companion file.

An AI can read these files and use the CLI's plan/apply commands with appropriate
authorization. The companion file is desired setup, not permission to grant
itself access. Vault references are not native Snowflake CLI credentials;
Snowbeam's explicit `exec`/runtime bridge resolves them. Transparent vault support
in Cortex Code has not been validated. Public configuration compatibility does
not imply that every consumer honors `SNOWFLAKE_HOME` identically.

## Validation boundary

Automated tests use synthetic Snowflake/provider responses and isolated files.
They cover account guards, policy drift, partial failures, runtime report
validation, PAT and key rotation, WIF binding, and secret-free persistent output.
OpenSSL generation is also tested locally. CLI/config compatibility has been
checked against Snowflake CLI 3.17.1. Live Snowflake provisioning, real 1Password/
Bitwarden writes, cloud WIF authentication, and an Omarchy desktop have not yet
been exercised end to end.
