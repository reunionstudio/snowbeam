"""Readable, credential-free intent shared by people and agent tooling."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import tomlkit

from .config import Config, ConfigError, read_document

LABEL_FIELDS = {"client", "owner", "purpose", "runtime"}
TEMPLATE_FIELDS = {
    "host",
    "organization",
    "account",
    "admin_connection",
    "role",
    "warehouse",
    "authentication_policy",
    "network_policy",
    "auth",
    "days_to_expiry",
    "provider",
    "vault",
    "vault_account",
    "vault_server",
    "vault_user",
}
WORKLOAD_FIELDS = {
    "workload_provider",
    "workload_subject",
    "workload_issuer",
    "workload_audience",
    "workload_token_file",
}
IDENTITY_FIELDS = LABEL_FIELDS | WORKLOAD_FIELDS | {"connection", "template", "user", "kind"}
REFERENCE_FIELDS = {
    "provider",
    "vault",
    "vault_account",
    "vault_server",
    "vault_user",
    "item",
    "credential_kind",
    "token_field",
    "key_field",
    "passphrase_field",
}
PRIVILEGED_ROLES = {
    "ACCOUNTADMIN",
    "SECURITYADMIN",
    "ORGADMIN",
    "GLOBALORGADMIN",
    "SYSADMIN",
    "USERADMIN",
    "PUBLIC",
}


def name(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,95}", value):
        raise ValueError("Use a name beginning with a letter, followed by letters, digits, _ or -.")
    return value


def identifier(value: str, *, qualified: bool = False) -> str:
    """Provisioning accepts ordinary Snowflake identifiers; metadata can quote any name."""
    parts = value.split(".") if isinstance(value, str) else []
    count = 3 if qualified else 1
    if len(parts) != count or any(
        not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]{0,254}", p) for p in parts
    ):
        raise ValueError(
            "Use a fully qualified DATABASE.SCHEMA.POLICY name."
            if qualified
            else "Use an ordinary Snowflake identifier."
        )
    return value.upper()


def quote(value: str) -> str:
    if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value):
        raise ValueError("Invalid Snowflake identifier.")
    return '"' + value.replace('"', '""') + '"'


def qualified_quote(value: str) -> str:
    return ".".join(quote(part) for part in identifier(value, qualified=True).split("."))


def literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def validate_fields(data: dict, allowed: set[str]) -> dict:
    if not isinstance(data, dict) or set(data) - allowed:
        raise ValueError(
            "Unknown Snowbeam fields. Store credentials in a vault, never in snowbeam.toml."
        )
    for value in data.values():
        if not isinstance(value, (str, int)) or isinstance(value, bool):
            raise ValueError("Snowbeam fields must be text or integers.")
        if isinstance(value, str) and (len(value) > 2048 or any(ord(c) < 32 for c in value)):
            raise ValueError("Snowbeam fields cannot contain control characters or long payloads.")
    return dict(data)


def template_values(data: dict) -> dict:
    result = validate_fields(data, TEMPLATE_FIELDS)
    required = {
        "organization",
        "account",
        "admin_connection",
        "role",
        "authentication_policy",
        "network_policy",
    }
    if not all(result.get(key) for key in required):
        raise ValueError(
            "A template needs organization, account, admin connection, role, "
            "authentication policy, network policy, and vault provider."
        )
    for field in ("organization", "account", "role", "network_policy"):
        result[field] = identifier(result[field])
    result["authentication_policy"] = identifier(result["authentication_policy"], qualified=True)
    if result["role"] in PRIVILEGED_ROLES:
        raise ValueError(
            "Choose a dedicated workload role, not a built-in administrative or PUBLIC role."
        )
    if result.get("warehouse"):
        result["warehouse"] = identifier(result["warehouse"])
    result.setdefault("auth", "PAT")
    if result["auth"] not in {"PAT", "KEYPAIR", "WIF"}:
        raise ValueError("Provisioning supports PAT, KEYPAIR, or WIF authentication.")
    result.setdefault("days_to_expiry", 30)
    if not isinstance(result["days_to_expiry"], int) or not 1 <= result["days_to_expiry"] <= 365:
        raise ValueError("PAT lifetime must be between 1 and 365 days.")
    if result["auth"] != "WIF":
        vault_values(result)
    elif any(
        result.get(k) for k in ("provider", "vault", "vault_account", "vault_server", "vault_user")
    ):
        raise ValueError("WIF uses runtime identity; omit password-manager fields.")
    if result.get("host"):
        validate_host(result["host"])
    return result


def validate_host(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("A host override must be a Snowflake hostname.")
    host = value.lower()
    if not re.fullmatch(r"[a-z0-9.-]+", host) or not host.endswith(
        (".snowflakecomputing.com", ".snowflakecomputing.cn")
    ):
        raise ValueError("A host override must be a Snowflake hostname.")
    return host


def vault_values(data: dict) -> None:
    if data.get("credential_kind") not in {None, "PAT", "KEYPAIR"}:
        raise ValueError("A vault credential kind must be PAT or KEYPAIR.")
    if data.get("provider") == "onepassword":
        if not data.get("vault") or not data.get("vault_account"):
            raise ValueError("1Password requires an explicit vault and account.")
    elif data.get("provider") == "bitwarden":
        from urllib.parse import urlparse

        server = urlparse(str(data.get("vault_server", "")))
        if server.scheme != "https" or not server.netloc or server.username or server.password:
            raise ValueError("Bitwarden requires its HTTPS server URL.")
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", str(data.get("vault_user", ""))):
            raise ValueError("Bitwarden requires the signed-in user's UUID from bw status.")
        if data.get("vault"):
            raise ValueError(
                "Bitwarden writes to the specified user's personal vault; organization "
                "collections are not supported."
            )
    else:
        raise ValueError("Supported vault providers: onepassword, bitwarden.")


class FleetConfig:
    """Use the same locked, atomic, private backup protocol as connection editing."""

    def __init__(self, path: Path):
        self.path = path.expanduser().absolute()

    def read(self) -> dict:
        return self.validate(read_document(self.path))

    @staticmethod
    def validate(doc) -> dict:
        if (
            set(doc) - {"version", "templates", "identities", "credentials"}
            or doc.get("version", 1) != 1
        ):
            raise ConfigError("Unsupported Snowbeam fleet configuration.")
        result = {"version": 1, "templates": {}, "identities": {}, "credentials": {}}
        for section in ("templates", "identities", "credentials"):
            entries = doc.get(section, {})
            if not isinstance(entries, dict):
                raise ConfigError("Expected named tables in Snowbeam fleet configuration.")
            for key, value in entries.items():
                name(key)
                allowed = (
                    TEMPLATE_FIELDS
                    if section == "templates"
                    else IDENTITY_FIELDS
                    if section == "identities"
                    else REFERENCE_FIELDS
                )
                result[section][key] = validate_fields(value, allowed)
        for key, value in result["templates"].items():
            result["templates"][key] = template_values(value)
        seen = set()
        for value in result["identities"].values():
            if not value.get("connection") or value.get("kind") not in {"person", "agent"}:
                raise ValueError(
                    "Each tracked identity requires a connection and kind (person or agent)."
                )
            if value["connection"] in seen:
                raise ValueError("A connection can belong to only one tracked identity.")
            seen.add(value["connection"])
            if value.get("template") and value["template"] not in result["templates"]:
                raise ValueError("An identity references a missing template.")
            if value.get("template"):
                if not value.get("owner") or not value.get("runtime"):
                    raise ValueError("A managed agent needs an owner and runtime.")
                if result["templates"][value["template"]]["auth"] == "WIF":
                    from .workload import validate_workload

                    validate_workload(value)
                identifier(value.get("user", ""))
                if (
                    value["connection"]
                    == result["templates"][value["template"]]["admin_connection"]
                ):
                    raise ValueError(
                        "An agent connection must differ from its provisioning connection."
                    )
        principals, workloads = set(), set()
        for value in result["identities"].values():
            if not value.get("template"):
                continue
            template = result["templates"][value["template"]]
            account = (template["organization"], template["account"])
            principal = (*account, identifier(value["user"]))
            if principal in principals:
                raise ValueError("This Snowflake agent identity is already tracked.")
            principals.add(principal)
            if template["auth"] == "WIF":
                binding = validate_workload(value)
                workload = (
                    *account,
                    *(
                        binding.get(k)
                        for k in ("workload_provider", "workload_subject", "workload_issuer")
                    ),
                )
                if workload in workloads:
                    raise ValueError("This workload identity is already assigned to another agent.")
                workloads.add(workload)
        for key, value in result["credentials"].items():
            if key not in result["identities"]:
                raise ValueError("A vault reference must belong to a tracked identity.")
            vault_values(value)
            if not re.fullmatch(r"[A-Za-z0-9-]{20,64}", value.get("item", "")):
                raise ValueError("Credential references must use an immutable vault item ID.")
        return result

    def save(self, section: str, key: str, values: dict, *, create: bool = False) -> None:
        name(key)
        # Validate the entire future document before touching the file.
        with Config(self.path)._edit(self.path) as doc:
            if section not in {"templates", "identities", "credentials"}:
                raise ValueError("Unknown Snowbeam section.")
            if section not in doc:
                doc[section] = tomlkit.table()
            if create and key in doc[section]:
                raise ValueError("That Snowbeam name already exists.")
            current = {} if section == "credentials" else dict(doc[section].get(key, {}))
            current.update(values)
            allowed = (
                TEMPLATE_FIELDS
                if section == "templates"
                else IDENTITY_FIELDS
                if section == "identities"
                else REFERENCE_FIELDS
            )
            validate_fields(current, allowed)
            if section == "templates":
                current = template_values(current)
            if section == "credentials":
                vault_values(current)
            doc[section][key] = current
            self.validate(doc)

    def spec(self, key: str, document: dict | None = None) -> dict:
        doc = self.read() if document is None else document
        if key not in doc["identities"]:
            raise ValueError("Unknown tracked identity.")
        value = doc["identities"][key]
        template = doc["templates"].get(value.get("template"), {})
        return {**template, **value, "id": key, "credential": doc["credentials"].get(key)}

    def track(self, key: str, connection: str, *, kind: str = "person", **labels) -> None:
        doc = self.read()
        if any(v["connection"] == connection for v in doc["identities"].values()):
            raise ValueError("This connection is already tracked.")
        if kind not in {"person", "agent"}:
            raise ValueError("Identity kind must be person or agent.")
        self.save(
            "identities", key, {"connection": connection, "kind": kind, **labels}, create=True
        )

    def add_agent(self, key: str, template: str, user: str, connection: str, **labels) -> None:
        doc = self.read()
        if template not in doc["templates"]:
            raise ValueError("Unknown provisioning template.")
        chosen = doc["templates"][template]
        user = identifier(user)
        if chosen["auth"] == "WIF":
            from .workload import validate_workload

            labels.update(validate_workload(labels))
        name(connection)
        if connection == chosen["admin_connection"] or any(
            v["connection"] == connection for v in doc["identities"].values()
        ):
            raise ValueError("Choose an unused agent connection name.")
        for key_ in doc["identities"]:
            other = self.spec(key_, doc)
            if (
                chosen["auth"] == "WIF"
                and other.get("organization") == chosen["organization"]
                and other.get("account") == chosen["account"]
                and all(
                    other.get(k) == labels.get(k)
                    for k in ("workload_provider", "workload_subject", "workload_issuer")
                )
            ):
                raise ValueError(
                    "This workload identity is already assigned to another agent in this account."
                )
            if (other.get("organization"), other.get("account"), other.get("user")) == (
                chosen["organization"],
                chosen["account"],
                user,
            ):
                raise ValueError("This Snowflake agent identity is already tracked.")
        self.save(
            "identities",
            key,
            {
                "connection": connection,
                "kind": "agent",
                "template": template,
                "user": user,
                **labels,
            },
            create=True,
        )
