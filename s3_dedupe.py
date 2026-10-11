"""S3-backed idempotency records for completed incident investigations."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import ClientError


LEASE_MINUTES = 30


def _extractor_name() -> str:
    value = os.environ.get("INCIDENT_EXTRACTOR_NAME", "airdrop-jira-extractor")
    return re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-").lower() or "unknown"


def _service_name(service_name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", service_name).strip("-").lower() or "unknown"


def _key(service_name: str, run_id: str, sync_unit_id: str) -> str:
    identity = json.dumps([service_name, run_id, sync_unit_id], separators=(",", ":"))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    prefix = os.environ.get("INCIDENT_DEDUPE_PREFIX", "processed-investigations").strip("/")
    root = f"{prefix}/{_extractor_name()}" if prefix else _extractor_name()
    return f"{root}/{_service_name(service_name)}/{digest}.json"


def _client():
    region = os.environ.get("INCIDENT_DEDUPE_REGION") or os.environ.get("AWS_REGION")
    return boto3.client("s3", region_name=region)


def _record(service_name: str, run_id: str, sync_unit_id: str, status: str) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "service_name": service_name,
        "run_id": run_id,
        "sync_unit_id": sync_unit_id,
        "status": status,
        "updated_at": now.isoformat(),
        "lease_expires_at": (now + timedelta(minutes=LEASE_MINUTES)).isoformat()
        if status == "processing" else None,
    }


def _put(client, bucket: str, key: str, record: dict, **conditions) -> str | None:
    response = client.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(record, separators=(",", ":")).encode("utf-8"),
        ContentType="application/json",
        **conditions,
    )
    return response.get("ETag")


def claim(service_name: str, run_id: str, sync_unit_id: str) -> dict:
    """Claim a new or expired/failed identity; otherwise report it as already claimed."""
    bucket = os.environ.get("INCIDENT_DEDUPE_BUCKET")
    if not bucket:
        return {"enabled": False, "claimed": True}
    client = _client()
    key = _key(service_name, run_id, sync_unit_id)
    record = _record(service_name, run_id, sync_unit_id, "processing")
    try:
        etag = _put(client, bucket, key, record, IfNoneMatch="*")
        return {"enabled": True, "claimed": True, "bucket": bucket, "key": key, "etag": etag}
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code not in {"PreconditionFailed", "ConditionalRequestConflict", "412", "409"}:
            raise

    try:
        existing = client.get_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        # A competing claim may have disappeared between the conditional write and read.
        if exc.response.get("Error", {}).get("Code") in {"NoSuchKey", "404"}:
            return claim(service_name, run_id, sync_unit_id)
        raise

    previous = json.loads(existing["Body"].read().decode("utf-8"))
    if previous.get("status") == "completed":
        return {"enabled": True, "claimed": False, "status": "completed"}

    now = datetime.now(timezone.utc)
    lease_expiry = previous.get("lease_expires_at")
    expired = False
    if lease_expiry:
        try:
            expired = datetime.fromisoformat(lease_expiry) <= now
        except ValueError:
            expired = True
    retryable = previous.get("status") == "failed" or expired
    if not retryable:
        return {"enabled": True, "claimed": False, "status": previous.get("status", "processing")}

    etag = existing.get("ETag")
    if not etag:
        return {"enabled": True, "claimed": False, "status": "processing"}
    try:
        etag = _put(client, bucket, key, record, IfMatch=etag)
        return {"enabled": True, "claimed": True, "bucket": bucket, "key": key, "etag": etag}
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {
            "PreconditionFailed", "ConditionalRequestConflict", "412", "409"
        }:
            return {"enabled": True, "claimed": False, "status": "processing"}
        raise


def complete(claim_info: dict, service_name: str, run_id: str, sync_unit_id: str,
             report: str | None = None) -> None:
    if not claim_info.get("enabled"):
        return
    record = _record(service_name, run_id, sync_unit_id, "completed")
    if report is not None:
        record["report"] = report[:12000]
    _put(_client(), claim_info["bucket"], claim_info["key"],
         record,
         **({"IfMatch": claim_info["etag"]} if claim_info.get("etag") else {}))


def previous_investigations(service_name: str, limit: int = 5) -> list[dict]:
    """Load a small set of completed investigation reports for this extractor."""
    bucket = os.environ.get("INCIDENT_DEDUPE_BUCKET")
    if not bucket:
        return []
    prefix = os.environ.get("INCIDENT_DEDUPE_PREFIX", "processed-investigations").strip("/")
    root = f"{prefix}/{_extractor_name()}" if prefix else _extractor_name()
    scoped_prefix = f"{root}/{_service_name(service_name)}/"
    client = _client()
    response = client.list_objects_v2(Bucket=bucket, Prefix=scoped_prefix, MaxKeys=1000)
    items = []
    for entry in response.get("Contents", []):
        try:
            obj = client.get_object(Bucket=bucket, Key=entry["Key"])
            record = json.loads(obj["Body"].read().decode("utf-8"))
        except (KeyError, ValueError, UnicodeDecodeError):
            continue
        if record.get("status") == "completed" and record.get("report"):
            items.append(record)
    items.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return [{key: item.get(key) for key in ("run_id", "sync_unit_id", "updated_at", "report")}
            for item in items[:limit]]


def fail(claim_info: dict, service_name: str, run_id: str, sync_unit_id: str) -> None:
    """Make a failed investigation immediately retryable without deleting its audit record."""
    if not claim_info.get("enabled"):
        return
    _put(_client(), claim_info["bucket"], claim_info["key"],
         _record(service_name, run_id, sync_unit_id, "failed"),
         **({"IfMatch": claim_info["etag"]} if claim_info.get("etag") else {}))
