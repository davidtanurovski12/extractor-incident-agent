"""Create DevRev issues through the locally authenticated DevRev CLI profile."""

import json
import os
import re
import shutil
import subprocess
from difflib import SequenceMatcher

from devrev_config import (DEFAULT_APPLIES_TO_PARTS, DEFAULT_OWNER_ID,
                           applies_to_part)
from devrev_issue import build_issue_payload, build_issue_title


DEFAULT_ENV = "dev"
DEFAULT_ORG = "devrevxaws-demo"
DEFAULT_USER = "i-david.tanurovski@devrev.ai"
SIMILARITY_THRESHOLD = 0.82


def _applies_to_part(external_system: str) -> str:
    """Select the matching system part, with configured values overriding defaults."""
    return applies_to_part(external_system)


def _cli_context() -> tuple[str, list[str]]:
    cli = os.environ.get("DEVREV_CLI_PATH") or shutil.which("devrev")
    if not cli:
        raise RuntimeError("DevRev CLI not found; install it or set DEVREV_CLI_PATH")
    command = [cli, "--env", os.environ.get("DEVREV_CLI_ENV", DEFAULT_ENV),
               "--org", os.environ.get("DEVREV_ORG", DEFAULT_ORG),
               "--usr", os.environ.get("DEVREV_USER", DEFAULT_USER)]
    return cli, command


def _normalized_title(title: str) -> str:
    title = re.sub(r"^(?:agent(?:inspection|inspetion):|extractor incident:)\s*", "", title.strip(),
                   flags=re.IGNORECASE)
    title = re.sub(r"\bextractor\b", " ", title, flags=re.IGNORECASE)
    return " ".join(re.findall(r"[a-z0-9]+", title.casefold()))


def _extract_issues(value) -> list[dict]:
    if isinstance(value, list):
        issues = []
        for item in value:
            issues.extend(_extract_issues(item))
        return issues
    if not isinstance(value, dict):
        return []
    if isinstance(value.get("works"), list):
        return _extract_issues(value["works"])
    if isinstance(value.get("work"), dict):
        return _extract_issues(value["work"])
    if value.get("id") and value.get("title"):
        return [value]
    return []


def _parse_issue_list(stdout: str) -> list[dict]:
    try:
        return _extract_issues(json.loads(stdout))
    except json.JSONDecodeError:
        issues = []
        for line in stdout.splitlines():
            try:
                issues.extend(_extract_issues(json.loads(line)))
            except json.JSONDecodeError:
                continue
        return issues


def find_similar_issue(external_system: str, short_error: str) -> dict | None:
    """List issues for the service's part and find a title-level near duplicate."""
    target_title = build_issue_title(external_system, short_error)
    target = _normalized_title(target_title)
    applies_to_part = _applies_to_part(external_system)
    _, base_command = _cli_context()
    command = base_command + ["works", "list", "--issue", "--part",
                              applies_to_part, "--no-progress"]
    try:
        completed = subprocess.run(command, text=True, capture_output=True,
                                   timeout=120, check=False)
    except subprocess.TimeoutExpired:
        raise RuntimeError("DevRev issue duplicate lookup timed out") from None
    except OSError as exc:
        raise RuntimeError(f"Could not run DevRev CLI for duplicate lookup: {exc}") from None
    if completed.returncode:
        detail = completed.stderr.strip()[:1000] or f"exit status {completed.returncode}"
        raise RuntimeError(f"DevRev issue duplicate lookup failed: {detail}")

    candidates = []
    for issue in _parse_issue_list(completed.stdout):
        title = str(issue.get("title", ""))
        similarity = SequenceMatcher(None, target, _normalized_title(title)).ratio()
        if similarity >= SIMILARITY_THRESHOLD:
            candidates.append((similarity, issue))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def create_issue(external_system: str, short_error: str, body: str) -> dict:
    """Reuse a similar issue or create a newly titled issue through the CLI profile."""
    existing = find_similar_issue(external_system, short_error)
    if existing:
        return {
            "status": "existing",
            "id": existing["id"],
            "display_id": existing.get("display_id"),
            "title": existing["title"],
        }

    applies_to_part = _applies_to_part(external_system)
    configured_owners = os.environ.get("DEVREV_OWNED_BY", "")
    owners = [value.strip() for value in configured_owners.split(",") if value.strip()]
    if not owners:
        owners = [DEFAULT_OWNER_ID]
    payload = build_issue_payload(external_system, short_error, body, applies_to_part, owners)

    _, base_command = _cli_context()
    command = base_command + ["works", "create", "--no-progress"]

    try:
        completed = subprocess.run(
            command,
            input=json.dumps(payload) + "\n",
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("DevRev CLI issue creation timed out") from None
    except OSError as exc:
        raise RuntimeError(f"Could not run DevRev CLI: {exc}") from None

    if completed.returncode:
        detail = completed.stderr.strip()[:1000] or f"exit status {completed.returncode}"
        raise RuntimeError(f"DevRev CLI issue creation failed: {detail}")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError:
        raise RuntimeError("DevRev CLI returned invalid JSON") from None
    if isinstance(result, list) and len(result) == 1:
        result = result[0]
    work = result.get("work", {}) if isinstance(result, dict) else {}
    if not isinstance(work, dict) or not work.get("id"):
        raise RuntimeError("DevRev CLI response did not include work.id")
    return {
        "status": "created",
        "id": work["id"],
        "display_id": work.get("display_id"),
        "title": work.get("title", payload["title"]),
    }
