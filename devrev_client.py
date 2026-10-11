"""Small authenticated client for creating DevRev issue work items."""

import json
import os
import re
from difflib import SequenceMatcher
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from devrev_config import DEFAULT_OWNER_ID, applies_to_part
from devrev_issue import build_issue_payload, build_issue_title
from devrev_secret import DEFAULT_SECRET_ID, load_token, secret_client


DEFAULT_API_BASE_URL = "https://api.dev.devrev-eng.ai"
LIST_PAGE_SIZE = 100
MAX_LIST_PAGES = 100
SIMILARITY_THRESHOLD = 0.82


def _api_url() -> str:
    base_url = os.environ.get("DEVREV_API_BASE_URL", DEFAULT_API_BASE_URL).rstrip("/")
    parsed = urlsplit(base_url)
    if (parsed.scheme != "https" or not parsed.netloc or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment):
        raise ValueError("DEVREV_API_BASE_URL must be an HTTPS origin without a path")
    return base_url + "/works.create"


def _list_url() -> str:
    return _api_url().removesuffix("/works.create") + "/works.list"


def _normalized_title(title: str) -> str:
    title = re.sub(r"^(?:agent(?:inspection|inspetion):|extractor incident:)\s*", "", title.strip(),
                   flags=re.IGNORECASE)
    title = re.sub(r"\bextractor\b", " ", title, flags=re.IGNORECASE)
    return " ".join(re.findall(r"[a-z0-9]+", title.casefold()))


def _load_works(response: dict) -> list[dict]:
    if not isinstance(response, dict):
        raise RuntimeError("DevRev works.list response was not an object")
    works = response.get("works")
    if not isinstance(works, list):
        raise RuntimeError("DevRev works.list response did not include a works array")
    return [work for work in works if isinstance(work, dict) and work.get("id") and work.get("title")]


def find_similar_issue(external_system: str, short_error: str, token: str,
                       part_id: str | None = None) -> dict | None:
    """Search all issues in the service part and return the closest similar title."""
    target = _normalized_title(build_issue_title(external_system, short_error))
    part_id = part_id or applies_to_part(external_system)
    cursor = None
    candidates = []
    pages = 0
    while True:
        params = [("type", "issue"), ("applies_to_part", part_id), ("limit", LIST_PAGE_SIZE)]
        if cursor:
            params.append(("cursor", cursor))
        url = _list_url() + "?" + urlencode(params)
        request = Request(url, headers={"Authorization": f"Bearer {token}"}, method="GET")
        try:
            with urlopen(request, timeout=20) as response:
                page = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            raise RuntimeError(f"DevRev issue lookup failed (HTTP {exc.code}): {detail}") from None
        except URLError as exc:
            raise RuntimeError(f"DevRev issue lookup request failed: {exc.reason}") from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise RuntimeError("DevRev works.list returned invalid JSON") from None

        for work in _load_works(page):
            similarity = SequenceMatcher(None, target, _normalized_title(str(work["title"]))).ratio()
            if similarity >= SIMILARITY_THRESHOLD:
                candidates.append((similarity, work))
        cursor = page.get("next_cursor") if isinstance(page, dict) else None
        if not cursor:
            break
        pages += 1
        if pages >= MAX_LIST_PAGES:
            raise RuntimeError("DevRev issue lookup exceeded its pagination limit")
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def create_issue(external_system: str, short_error: str, body: str) -> dict:
    """Reuse a similar DevRev issue or create a new issue and return its identifiers."""
    part_id = applies_to_part(external_system)
    configured_owners = os.environ.get("DEVREV_OWNED_BY", "")
    owners = [value.strip() for value in configured_owners.split(",") if value.strip()]
    if not owners:
        owners = [DEFAULT_OWNER_ID]
    token = os.environ.get("DEVREV_API_TOKEN", "").strip()
    if not token:
        secret_id = os.environ.get("DEVREV_SECRET_ID", DEFAULT_SECRET_ID)
        token = load_token(secret_client(), secret_id)

    existing = find_similar_issue(external_system, short_error, token, part_id)
    if existing:
        return {
            "status": "existing",
            "id": existing["id"],
            "display_id": existing.get("display_id"),
            "title": existing["title"],
        }

    payload = build_issue_payload(external_system, short_error, body, part_id, owners)
    request = Request(
        _api_url(),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"DevRev issue creation failed (HTTP {exc.code}): {detail}") from None
    except URLError as exc:
        raise RuntimeError(f"DevRev issue creation request failed: {exc.reason}") from None
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("DevRev issue creation returned an invalid JSON response") from exc

    work = result.get("work", {}) if isinstance(result, dict) else {}
    if not isinstance(work, dict) or not work.get("id"):
        raise RuntimeError("DevRev issue creation response did not include work.id")
    return {
        "status": "created",
        "id": work["id"],
        "display_id": work.get("display_id"),
        "title": work.get("title", payload["title"]),
    }
