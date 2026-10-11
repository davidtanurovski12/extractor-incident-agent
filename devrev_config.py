"""Shared DevRev destination and owner configuration."""

import json
import os


DEFAULT_APPLIES_TO_PARTS = {
    "jira": "don:core:dvrv-us-1:devo/1W9hyutyQQ:product/3",
    "salesforce": "don:core:dvrv-us-1:devo/1W9hyutyQQ:product/4",
    "servicenow": "don:core:dvrv-us-1:devo/1W9hyutyQQ:product/5",
    "zendesk": "don:core:dvrv-us-1:devo/1W9hyutyQQ:product/6",
}
DEFAULT_OWNER_ID = "DEVU-1"


def applies_to_part(external_system: str) -> str:
    """Resolve an external system name to its configured DevRev part ID."""
    target = external_system.strip().casefold()
    configured = os.environ.get("DEVREV_APPLIES_TO_PARTS", "")
    if configured:
        try:
            parts = json.loads(configured)
        except json.JSONDecodeError:
            raise ValueError("DEVREV_APPLIES_TO_PARTS must be a JSON object mapping systems to part IDs") from None
        if not isinstance(parts, dict):
            raise ValueError("DEVREV_APPLIES_TO_PARTS must be a JSON object mapping systems to part IDs")
        for system, part_id in parts.items():
            if isinstance(system, str) and system.strip().casefold() == target:
                if not isinstance(part_id, str) or not part_id.strip():
                    raise ValueError(f"DEVREV_APPLIES_TO_PARTS has no valid part ID for {external_system}")
                return part_id.strip()
    default_part = DEFAULT_APPLIES_TO_PARTS.get(target)
    if default_part:
        return default_part
    legacy_part = os.environ.get("DEVREV_APPLIES_TO_PART", "")
    if legacy_part:
        return legacy_part.strip()
    raise ValueError(f"No DevRev applies-to part is configured for {external_system}")
