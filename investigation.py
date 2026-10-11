"""Read-only, bounded evidence tools for investigating a Salesforce sync unit."""

import json
import os
import re
import time
from pathlib import Path

from dev_cloudwatch import cloudwatch_connection


MAX_EVENTS = 200
MAX_ERROR_SELECTION_EVENTS = 10_000
MAX_FILE_BYTES = 1_000_000
MAX_LOG_EXPORT_BYTES = 50_000_000
MAX_SEARCH_FILES = 3000
SKIP_DIRS = {".git", "_build", "vendor", "node_modules", ".cache", "testdata"}
SOURCE_SUFFIXES = {".go", ".py", ".md", ".yaml", ".yml", ".json"}


def _extractor_repo_setting(service_name: str | None) -> tuple[str | None, str]:
    configured_map = os.environ.get("EXTRACTOR_REPO_PATHS", "")
    if configured_map:
        try:
            paths = json.loads(configured_map)
        except json.JSONDecodeError:
            raise ValueError("EXTRACTOR_REPO_PATHS must be a JSON object mapping services to repository paths") from None
        if not isinstance(paths, dict):
            raise ValueError("EXTRACTOR_REPO_PATHS must be a JSON object mapping services to repository paths")
        if service_name:
            for name, path in paths.items():
                if isinstance(name, str) and name.casefold() == service_name.casefold():
                    if not isinstance(path, str) or not path.strip():
                        raise ValueError(f"No valid extractor checkout configured for {service_name}")
                    return path, f'EXTRACTOR_REPO_PATHS[{service_name!r}]'

    if service_name:
        normalized = re.sub(r"[^a-z0-9]+", "_", service_name.casefold()).strip("_")
        aliases = [normalized]
        stripped = normalized.removeprefix("airdrop_").removesuffix("_extractor")
        for alias in (normalized.removeprefix("airdrop_"), normalized.removesuffix("_extractor"), stripped):
            if alias and alias not in aliases:
                aliases.append(alias)
        for alias in aliases:
            variable = f"{alias.upper()}_EXTRACTOR_REPO_PATH"
            configured = os.environ.get(variable)
            if configured:
                return configured, variable
        if (normalized not in {"jira", "airdrop_jira", "jira_extractor", "airdrop_jira_extractor"}
                and os.environ.get("INCIDENT_RUNTIME_SOURCE") != "airsync-metis"):
            raise RuntimeError(
                f"No extractor checkout configured for service {service_name!r}; "
                f"set {normalized.upper()}_EXTRACTOR_REPO_PATH or add it to EXTRACTOR_REPO_PATHS"
            )

    configured = os.environ.get("EXTRACTOR_REPO_PATH")
    return configured, "EXTRACTOR_REPO_PATH"


def repo_root(repo: str = "extractor", service_name: str | None = None) -> Path:
    if repo == "extractor":
        configured, setting = _extractor_repo_setting(service_name)
        if not configured and os.environ.get("INCIDENT_RUNTIME_SOURCE") == "airsync-metis":
            from runtime_source import source_root
            return source_root()
    elif repo == "mock_api":
        setting = "MOCK_API_REPO_PATH"
        configured = os.environ.get(setting)
    else:
        raise ValueError("repo must be extractor or mock_api")
    if not configured:
        raise RuntimeError(f"Set {setting} to the local {repo.replace('_', ' ')} repository")
    root = Path(configured).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"{setting} must point to an existing directory")
    return root


def _source_file(path: str, repo: str = "extractor", service_name: str | None = None) -> Path:
    root = repo_root(repo, service_name)
    candidate = (root / path).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file() or candidate.is_symlink():
        raise ValueError("Path must be a regular file inside the extractor repository")
    if any(part in SKIP_DIRS or part.startswith(".") for part in candidate.relative_to(root).parts):
        raise ValueError("Hidden and generated paths are excluded")
    if candidate.suffix not in SOURCE_SUFFIXES or candidate.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("File type or size is not supported")
    return candidate


def read_repo(path: str, start_line: int = 1, end_line: int = 80, repo: str = "extractor",
              service_name: str | None = None) -> dict:
    """Read at most 120 numbered lines from a repository source file."""
    if start_line < 1 or end_line < start_line or end_line - start_line >= 120:
        raise ValueError("Specify a valid range of at most 120 lines")
    candidate = _source_file(path, repo, service_name)
    lines = candidate.read_text(encoding="utf-8", errors="replace").splitlines()
    return {"repo": repo, "path": str(candidate.relative_to(repo_root(repo, service_name))), "total_lines": len(lines),
            "lines": [f"{n}: {lines[n - 1]}" for n in range(start_line, min(end_line, len(lines)) + 1)]}


def search_repo(query: str, limit: int = 30, repo: str = "all",
                service_name: str | None = None) -> dict:
    """Find literal text in repository sources; returns path and numbered snippets."""
    if not 2 <= len(query) <= 160 or not 1 <= limit <= 50:
        raise ValueError("Query must be 2–160 characters and limit must be 1–50")
    if repo not in ("all", "extractor", "mock_api"):
        raise ValueError("repo must be all, extractor, or mock_api")
    repositories = ("extractor", "mock_api") if repo == "all" else (repo,)
    if repo == "all":
        repo_root("extractor", service_name)
    hits = []
    scanned = 0
    for repository in repositories:
        try:
            root = repo_root(repository, service_name)
        except (RuntimeError, ValueError):
            continue
        for base, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
            for name in sorted(files):
                if Path(name).suffix not in SOURCE_SUFFIXES:
                    continue
                scanned += 1
                if scanned > MAX_SEARCH_FILES:
                    return {"matches": hits, "truncated": True}
                relative = str((Path(base) / name).relative_to(root))
                try:
                    source = _source_file(relative, repository, service_name)
                    for number, line in enumerate(source.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                        if query.lower() in line.lower():
                            hits.append({"repo": repository, "path": relative, "line": number, "text": line.strip()[:240]})
                            if len(hits) >= limit:
                                return {"matches": hits, "truncated": True}
                except (ValueError, OSError):
                    continue
    return {"matches": hits, "truncated": False}


def _normalized_file_events(configured: str) -> list[dict]:
    path = Path(configured)
    content = path.read_text(encoding="utf-8")
    if len(content.encode("utf-8")) > MAX_LOG_EXPORT_BYTES:
        raise ValueError(f"Log export exceeds the {MAX_LOG_EXPORT_BYTES}-byte limit")

    try:
        document = json.loads(content)
    except json.JSONDecodeError:
        document = None

    if isinstance(document, list):
        records = document
    elif isinstance(document, dict):
        records = document.get("logs", document.get("data", []))
        if not isinstance(records, list):
            records = [document]
    elif document is None:
        records = []
        for index, line in enumerate(content.splitlines(), 1):
            if len(line) > MAX_FILE_BYTES:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                records.append(record)
    else:
        records = []

    events = []
    for index, record in enumerate(records, 1):
        if not isinstance(record, dict):
            continue
        fields = record.get("fields")
        fields = fields if isinstance(fields, dict) else {}
        raw_line = record.get("line", record.get("message", ""))
        if not isinstance(raw_line, str):
            raw_line = json.dumps(raw_line, ensure_ascii=False)
        structured = {}
        try:
            parsed_line = json.loads(raw_line)
            if isinstance(parsed_line, dict):
                structured = parsed_line
        except json.JSONDecodeError:
            pass

        run_id = fields.get("run_id", fields.get("run_id_extracted"))
        sync_unit_id = fields.get("sync_unit_id", fields.get("sync_unit_id_extracted"))
        if run_id is None:
            run_id = record.get("run_id", structured.get("run_id"))
        if sync_unit_id is None:
            sync_unit_id = record.get("sync_unit_id", structured.get("sync_unit_id"))

        event = {
            "run_id": str(run_id) if run_id is not None else "",
            "sync_unit_id": str(sync_unit_id) if sync_unit_id is not None else "",
            "timestamp": str(record.get("date", record.get("timestamp", fields.get("timestamp", "")))),
            "level": record.get("level", fields.get("level", fields.get("detected_level", structured.get("level", "")))),
            "event_type": record.get("event_type", fields.get("event_type", fields.get("event_type_extracted", ""))),
            "message": structured.get("msg", fields.get("msg", raw_line)),
            "raw_line": raw_line,
            "fields": fields,
            "source_line": index,
            "source_ref": {"file": str(path), "entry": index},
        }
        events.append(event)
    return events


def _event_error_score(event: dict) -> int:
    text = " ".join(str(event.get(key, ""))
                    for key in ("message", "msg", "raw_line", "event_type", "error")).lower()
    fields = event.get("fields", {})
    text += " " + str(fields.get("event_type", "")).lower()
    level = str(event.get("level", fields.get("level", ""))).lower()
    score = 10 if level in ("error", "fatal", "critical") else 0
    if "error:" in text or "exception" in text:
        score += 5
    if "extraction_error" in text or "extraction error" in text:
        score += 4
    if re.search(r"\bhttp(?: status)? [45]\d\d\b|status code [45]\d\d", text):
        score += 3
    if any(marker in text for marker in ("not found", "failed", "failure", "timed out", "timeout", "refused")):
        score += 1
    return score


def select_log_pair(configured: str, run_id: str | None = None,
                    sync_unit_id: str | None = None) -> tuple[str, str, dict]:
    """Select the strongest error pair from an export (legacy single-pair interface)."""
    return select_log_pairs(configured, run_id, sync_unit_id)[0]


def select_log_pairs(configured: str, run_id: str | None = None,
                     sync_unit_id: str | None = None) -> list[tuple[str, str, dict]]:
    """Return every distinct run/unit pair with error evidence in an export."""
    events = _normalized_file_events(configured)
    pairs: dict[tuple[str, str], dict] = {}
    for event in events:
        run = event["run_id"]
        unit = event["sync_unit_id"]
        if not run or not unit:
            continue
        if run_id and run != run_id or sync_unit_id and unit != sync_unit_id:
            continue
        score = _event_error_score(event)
        if score <= 0:
            continue
        pair = pairs.setdefault((run, unit), {"score": 0, "count": 0, "latest": ""})
        pair["score"] += score
        pair["count"] += 1
        pair["latest"] = max(pair["latest"], event["timestamp"])

    if not pairs:
        raise ValueError("No error run_id/sync_unit_id pair found in the supplied log export")
    ordered = sorted(pairs, key=lambda pair: (pairs[pair]["score"], pairs[pair]["latest"],
                                               pairs[pair]["count"]), reverse=True)
    return [(run, unit, pairs[(run, unit)]) for run, unit in ordered]


def select_cloudwatch_log_pair(run_id: str | None = None,
                               sync_unit_id: str | None = None,
                               service_name: str | None = None) -> tuple[str, str, dict]:
    """Select the strongest error pair (legacy single-pair interface)."""
    return select_cloudwatch_log_pairs(run_id, sync_unit_id, service_name=service_name)[0]


def select_cloudwatch_log_pairs(run_id: str | None = None,
                                sync_unit_id: str | None = None, *,
                                service_name: str | None = None,
                                allow_empty: bool = False,
                                prefer_service_mapping: bool = False) -> list[tuple[str, str, dict]]:
    """Select every distinct run/unit pair with error evidence in recent CloudWatch logs."""
    for value in (run_id, sync_unit_id):
        if value and not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", value):
            raise ValueError("CloudWatch identifiers must contain only letters, digits, '-' or '_'")
    client, group, hours = cloudwatch_connection(
        service_name, prefer_service_mapping=prefer_service_mapping)
    query = ("fields @timestamp, @message, @logStream, @ptr "
             "| filter @message like /(?i)(error|exception|fatal|critical|failed|failure|timeout|timed out|refused|not found|HTTP [45][0-9]{2}|status code [45][0-9]{2})/ "
             f"| sort @timestamp desc | limit {MAX_ERROR_SELECTION_EVENTS}")
    now = int(time.time())
    query_id = client.start_query(
        logGroupName=group, startTime=now - hours * 3600,
        endTime=now, queryString=query,
    )["queryId"]
    for _ in range(45):
        result = client.get_query_results(queryId=query_id)
        if result["status"] == "Complete":
            break
        if result["status"] in ("Failed", "Cancelled", "Timeout", "Unknown"):
            raise RuntimeError(f"CloudWatch query {query_id} ended: {result['status']}")
        time.sleep(1)
    else:
        client.stop_query(queryId=query_id)
        raise TimeoutError(f"CloudWatch query {query_id} did not complete in 45 seconds")

    matched = result.get("statistics", {}).get("recordsMatched")
    if matched is not None and matched > MAX_ERROR_SELECTION_EVENTS:
        raise RuntimeError(
            f"CloudWatch error selection exceeded {MAX_ERROR_SELECTION_EVENTS} records; "
            "narrow INCIDENT_LOOKBACK_HOURS before retrying"
        )

    pairs: dict[tuple[str, str], dict] = {}
    for row in result["results"]:
        fields = {field["field"]: field["value"] for field in row}
        try:
            event = json.loads(fields.get("@message", ""))
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        nested = event.get("fields") if isinstance(event.get("fields"), dict) else {}
        selected_run = event.get("run_id") or event.get("run_id_extracted") or nested.get("run_id") or nested.get("run_id_extracted")
        selected_unit = (event.get("sync_unit_id") or event.get("sync_unit_id_extracted")
                         or nested.get("sync_unit_id") or nested.get("sync_unit_id_extracted"))
        if not selected_run or not selected_unit:
            continue
        selected_run, selected_unit = str(selected_run), str(selected_unit)
        if run_id and selected_run != run_id or sync_unit_id and selected_unit != sync_unit_id:
            continue
        event["timestamp"] = fields.get("@timestamp", event.get("ts", ""))
        score = _event_error_score(event)
        if score <= 0:
            continue
        pair = pairs.setdefault((selected_run, selected_unit), {"score": 0, "count": 0, "latest": ""})
        pair["score"] += score
        pair["count"] += 1
        pair["latest"] = max(pair["latest"], str(event["timestamp"]))
    if not pairs:
        if allow_empty:
            return []
        raise ValueError("No error log event with a run_id/sync_unit_id pair found in the CloudWatch lookback")
    ordered = sorted(pairs, key=lambda pair: (pairs[pair]["score"], pairs[pair]["latest"],
                                               pairs[pair]["count"]), reverse=True)
    return [(run, unit, dict(pairs[(run, unit)], query_id=query_id))
            for run, unit in ordered]


def search_sync_logs(run_id: str, sync_unit_id: str, limit: int = 100,
                     service_name: str | None = None,
                     prefer_service_mapping: bool = False) -> dict:
    """Read matching JSONL/Grafana events or query CloudWatch for one run and sync unit.

    File exports may use top-level JSONL fields or Grafana's JSON-array `fields`
    schema. Searches remain scoped to both identifiers.
    """
    if not run_id or not sync_unit_id or len(run_id) > 256 or len(sync_unit_id) > 256:
        raise ValueError("Both run_id and sync_unit_id are required (max 256 characters)")
    if not 1 <= limit <= MAX_EVENTS:
        raise ValueError(f"Limit must be between 1 and {MAX_EVENTS}")
    configured = os.environ.get("INCIDENT_LOG_FILE")
    if not configured:
        return _search_cloudwatch(run_id, sync_unit_id, limit, service_name,
                                  prefer_service_mapping)
    events = [event for event in _normalized_file_events(configured)
              if event["run_id"] == run_id and event["sync_unit_id"] == sync_unit_id]
    events.sort(key=lambda event: (event["timestamp"], event["source_line"]))
    return {"run_id": run_id, "sync_unit_id": sync_unit_id, "total_matches": len(events),
            "truncated": len(events) > limit, "events": events[:limit]}


def _search_cloudwatch(run_id: str, sync_unit_id: str, limit: int,
                       service_name: str | None = None,
                       prefer_service_mapping: bool = False) -> dict:
    if not all(re.fullmatch(r"[A-Za-z0-9_-]{1,256}", value) for value in (run_id, sync_unit_id)):
        raise ValueError("CloudWatch identifiers must contain only letters, digits, '-' or '_'")
    client, group, hours = cloudwatch_connection(
        service_name, prefer_service_mapping=prefer_service_mapping)
    query = ("fields @timestamp, @message, @logStream, @ptr "
             f'| filter run_id = "{run_id}" and sync_unit_id = "{sync_unit_id}" '
             f"| sort @timestamp desc | limit {limit + 1}")
    now = int(time.time())
    query_id = client.start_query(logGroupName=group, startTime=now - hours * 3600,
                                  endTime=now, queryString=query)["queryId"]
    for _ in range(45):
        result = client.get_query_results(queryId=query_id)
        if result["status"] == "Complete":
            break
        if result["status"] in ("Failed", "Cancelled", "Timeout", "Unknown"):
            raise RuntimeError(f"CloudWatch query {query_id} ended: {result['status']}")
        time.sleep(1)
    else:
        client.stop_query(queryId=query_id)
        raise TimeoutError(f"CloudWatch query {query_id} did not complete in 45 seconds")
    events = []
    for row in result["results"]:
        fields = {field["field"]: field["value"] for field in row}
        try:
            event = json.loads(fields.get("@message", ""))
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        event["timestamp"] = fields.get("@timestamp", event.get("ts", ""))
        event["source_ref"] = {"log_group": group, "log_stream": fields.get("@logStream"),
                               "event_pointer": fields.get("@ptr")}
        events.append(event)
    events.reverse()
    return {"run_id": run_id, "sync_unit_id": sync_unit_id,
            "total_matches": len(events), "truncated": len(events) > limit,
            "events": events[-limit:], "query_id": query_id}
