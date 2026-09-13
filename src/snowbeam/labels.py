"""Local names and notes, kept separate from Snowflake identifiers."""

ALIAS_LIMIT = 120
NOTES_LIMIT = 8000


def display_name(identifier: str, alias: str | None = None) -> str:
    return f"{alias} ({identifier})" if alias else identifier


def validate_label(value: str, *, notes: bool = False) -> str:
    limit = NOTES_LIMIT if notes else ALIAS_LIMIT
    field = "Notes" if notes else "Alias"
    if len(value) > limit:
        raise ValueError(f"{field} must be {limit} characters or fewer.")
    allowed = "\n\t" if notes else ""
    if any((ord(c) < 32 or 127 <= ord(c) <= 159) and c not in allowed for c in value):
        raise ValueError(f"{field} contains unsupported control characters.")
    return value if notes else value.strip()
