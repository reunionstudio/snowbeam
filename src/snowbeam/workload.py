"""Bind existing runtime identities; cloud IAM remains with the runtime owner."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from .fleet import literal

WORKLOAD_FIELDS = {
    "workload_provider",
    "workload_subject",
    "workload_issuer",
    "workload_audience",
    "workload_token_file",
}


def validate_workload(spec: dict) -> dict:
    provider = spec.get("workload_provider", "").upper()
    subject = spec.get("workload_subject", "")
    if provider not in {"AWS", "AZURE", "GCP", "OIDC"} or not subject or "*" in subject:
        raise ValueError(
            "WIF needs a provider and one exact workload subject (AWS ARN, Azure "
            "object ID, GCP unique ID, or OIDC subject)."
        )
    if provider == "AWS" and not re.fullmatch(
        r"arn:(aws|aws-us-gov|aws-cn):iam::[0-9]{12}:(role|user)/[A-Za-z0-9+=,.@_/-]+", subject
    ):
        raise ValueError("Use the workload's exact IAM role or user ARN.")
    if provider == "AZURE" and not re.fullmatch(r"[0-9a-fA-F-]{36}", subject):
        raise ValueError("Use the Azure managed identity's object ID.")
    if provider == "GCP" and not re.fullmatch(r"[0-9]{10,30}", subject):
        raise ValueError("Use the GCP service account's numeric uniqueId.")
    issuer = spec.get("workload_issuer", "")
    if provider in {"AZURE", "OIDC"}:
        url = urlparse(issuer)
        if (
            url.scheme != "https"
            or not url.netloc
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError(
                "Use an HTTPS workload issuer URL without credentials or query parameters."
            )
    elif issuer:
        raise ValueError("Only Azure and OIDC workload identities take an issuer.")
    if provider == "OIDC":
        audience = spec.get("workload_audience", "")
        if not audience or audience == "snowflakecomputing.com" or "*" in audience:
            raise ValueError("Use an OIDC audience scoped to this Snowflake account.")
        path = spec.get("workload_token_file", "")
        if not path.startswith("/"):
            raise ValueError(
                "OIDC requires an absolute path to the runtime's projected, short-lived token file."
            )
    elif spec.get("workload_audience") or spec.get("workload_token_file"):
        raise ValueError("Audience and projected token file apply only to OIDC.")
    return {
        "workload_provider": provider,
        **{
            k: v for k, v in spec.items() if k in WORKLOAD_FIELDS and k != "workload_provider" and v
        },
    }


def workload_sql(spec: dict) -> str:
    binding = validate_workload(spec)
    provider = binding["workload_provider"]
    fields = ["TYPE = " + provider]
    fields.append(
        ("ARN" if provider == "AWS" else "SUBJECT") + " = " + literal(binding["workload_subject"])
    )
    if provider in {"AZURE", "OIDC"}:
        fields.append("ISSUER = " + literal(binding["workload_issuer"]))
    if provider == "OIDC":
        fields.append("OIDC_AUDIENCE_LIST = (" + literal(binding["workload_audience"]) + ")")
    return "WORKLOAD_IDENTITY = (" + " ".join(fields) + ")"


def matches_workload(spec: dict, methods: list[dict]) -> bool:
    binding = validate_workload(spec)
    expected = binding["workload_provider"]
    if len(methods) != 1 or methods[0].get("type") != expected:
        return False
    info = methods[0].get("additional_info") or {}
    if expected == "AWS":
        arn = binding["workload_subject"].split(":", 5)
        kind, role = arn[5].split("/", 1)
        return (
            info.get("awsPartition") == arn[1]
            and info.get("awsAccount") == arn[4]
            and info.get("iamRole") == role
            and info.get("type") == ("IAM_ROLE" if kind == "role" else "IAM_USER")
        )
    if info.get("subject") != binding["workload_subject"]:
        return False
    if expected in {"AZURE", "OIDC"} and info.get("issuer") != binding["workload_issuer"]:
        return False
    return expected != "OIDC" or info.get("audienceList") == [binding["workload_audience"]]
