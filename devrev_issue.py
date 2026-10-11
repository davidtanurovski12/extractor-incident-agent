"""Pure DevRev issue validation and request-payload construction."""


MAX_TITLE_LENGTH = 256
MAX_BODY_LENGTH = 65_536
ISSUE_TITLE_PREFIX = "Extractor incident:"


def build_issue_title(external_system: str, short_error: str) -> str:
    external_system = external_system.strip()
    short_error = short_error.strip()
    title = f"{ISSUE_TITLE_PREFIX} {external_system} - {short_error}"
    if not external_system or not short_error or len(title) > MAX_TITLE_LENGTH:
        raise ValueError("external_system and short_error must produce a title of 1–256 characters")
    return title


def build_issue_payload(external_system: str, short_error: str, body: str,
                        applies_to_part: str, owners: list[str]) -> dict:
    """Validate issue fields and build the DevRev works.create request body."""
    external_system = external_system.strip()
    applies_to_part = applies_to_part.strip()
    cleaned_owners = [owner.strip() for owner in owners if owner.strip()]
    title = build_issue_title(external_system, short_error)
    if not body.strip() or len(body) > MAX_BODY_LENGTH:
        raise ValueError(f"body must be 1–{MAX_BODY_LENGTH} characters")
    if not applies_to_part:
        raise ValueError("applies_to_part must not be empty")
    if not cleaned_owners:
        raise ValueError("at least one DevRev issue owner is required")

    return {
        "type": "issue",
        "title": title,
        "body": body,
        "applies_to_part": applies_to_part,
        "owned_by": cleaned_owners,
    }
