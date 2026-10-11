"""Minimal JSON-line logging helpers for local and AgentCore execution."""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from uuid import uuid4

import boto3


logger = logging.getLogger("incident_agent")
configured_level = getattr(logging, os.environ.get("INCIDENT_LOG_LEVEL", "INFO").upper(), logging.INFO)
logger.setLevel(configured_level if isinstance(configured_level, int) else logging.INFO)
logger.propagate = False
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)

_invocation_id: ContextVar[str | None] = ContextVar("incident_invocation_id", default=None)


class CloudWatchActivityHandler(logging.Handler):
    """Send agent activity events to an existing CloudWatch Logs group."""

    def __init__(self, group: str, region: str, profile: str | None = None):
        super().__init__()
        self.client = boto3.Session(profile_name=profile, region_name=region).client("logs")
        self.group = group
        self.stream = f"local-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid4().hex[:8]}"
        self.client.create_log_stream(logGroupName=group, logStreamName=self.stream)

    def emit(self, record: logging.LogRecord) -> None:
        self.client.put_log_events(
            logGroupName=self.group,
            logStreamName=self.stream,
            logEvents=[{"timestamp": int(record.created * 1000), "message": record.getMessage()}],
        )


class DeferredCloudWatchActivityHandler(logging.Handler):
    """Avoid writing local activity logs when no extractor evidence was found."""

    def __init__(self, group: str, region: str, profile: str | None = None):
        super().__init__()
        self.group, self.region, self.profile = group, region, profile
        self.pending: list[logging.LogRecord] = []
        self.active: CloudWatchActivityHandler | None = None

    def emit(self, record: logging.LogRecord) -> None:
        if self.active:
            self.active.emit(record)
            return
        self.pending.append(record)
        # A source query with an error pair, or an explicit-ID query with matching
        # events, establishes that there is an investigation to log.
        try:
            event = json.loads(record.getMessage())
        except (ValueError, TypeError):
            return
        has_evidence = (event.get("event") == "logs.error_pairs_selected" and event.get("pair_count", 0) > 0
                        or event.get("event") == "logs.search.completed" and event.get("match_count", 0) > 0)
        if has_evidence:
            self.active = CloudWatchActivityHandler(self.group, self.region, self.profile)
            for item in self.pending:
                self.active.emit(item)
            self.pending.clear()


def configure_cloudwatch_activity(group: str, region: str, profile: str | None = None) -> None:
    """Send local activity to CloudWatch only when source evidence exists."""
    logger.addHandler(DeferredCloudWatchActivityHandler(group, region, profile))


def invocation_scope(function):
    """Bind a correlation ID for one invocation, including its nested tool calls."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        invocation_id = uuid4().hex
        started_at = time.monotonic()
        token = _invocation_id.set(invocation_id)
        try:
            result = function(*args, _invocation_id=invocation_id, **kwargs)
        except Exception as exc:
            emit("agent.invocation.failed", level=logging.ERROR,
                 error_type=type(exc).__name__,
                 duration_ms=round((time.monotonic() - started_at) * 1000))
            raise
        else:
            emit("agent.invocation.returned", status="returned",
                 duration_ms=round((time.monotonic() - started_at) * 1000))
            return result
        finally:
            _invocation_id.reset(token)
    return wrapped


def emit(event: str, *, level: int = logging.INFO, **fields) -> None:
    """Emit one JSON object per line; callers must pass metadata, never log content."""
    invocation_id = _invocation_id.get()
    if invocation_id and "invocation_id" not in fields:
        fields["invocation_id"] = invocation_id
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": logging.getLevelName(level),
        "event": event,
        **fields,
    }
    logger.log(level, json.dumps(entry, ensure_ascii=False, separators=(",", ":")))
