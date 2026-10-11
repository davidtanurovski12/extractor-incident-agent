"""Load DEV-prefixed CloudWatch configuration from Secrets Manager."""

from __future__ import annotations

import json
import os

import boto3
from botocore.exceptions import ClientError


DEFAULT_SECRET_ID = "incident-repro/dev-cloudwatch"
SERVICE_EXTRACTOR_GROUPS = {
    "zendesk": "airdrop-zendesk-extractor",
    "salesforce": "airdrop-salesforce-extractor",
    "jira": "airdrop-jira-extractor",
    "servicenow": "airdrop-servicenow-extractor",
}


def _read_config() -> dict:
    secret_id = os.environ.get("DEV_CLOUDWATCH_SECRET_ID", DEFAULT_SECRET_ID).strip()
    if not secret_id:
        return {}
    secrets_region = (os.environ.get("DEV_SECRETS_REGION") or os.environ.get("AWS_REGION")
                      or os.environ.get("INCIDENT_AWS_REGION") or "us-east-1")
    secrets_profile = os.environ.get("DEV_SECRETS_PROFILE") or os.environ.get("AWS_PROFILE")
    client = boto3.Session(profile_name=secrets_profile, region_name=secrets_region).client("secretsmanager")
    try:
        response = client.get_secret_value(SecretId=secret_id)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        if code == "ResourceNotFoundException":
            return {}
        raise RuntimeError(f"Unable to load CloudWatch configuration secret ({code})") from None
    value = response.get("SecretString")
    if not value:
        return {}
    try:
        config = json.loads(value)
    except json.JSONDecodeError:
        raise ValueError("CloudWatch configuration secret must contain a JSON object") from None
    if not isinstance(config, dict):
        raise ValueError("CloudWatch configuration secret must contain a JSON object")
    return config


def _service_log_group(config: dict, service_name: str | None,
                       *, require_mapping: bool = False) -> str | None:
    groups = config.get("DEV_CLOUDWATCH_LOG_GROUPS")
    if require_mapping and (not service_name or not isinstance(groups, dict)):
        raise ValueError("This scan requires a service_name and DEV_CLOUDWATCH_LOG_GROUPS mapping")
    if groups is not None:
        if not isinstance(groups, dict):
            raise ValueError("DEV_CLOUDWATCH_LOG_GROUPS must be an object mapping service names to log groups")
        if service_name:
            for name, group in groups.items():
                if isinstance(name, str) and name.casefold() == service_name.casefold():
                    if not isinstance(group, str) or not group.strip():
                        raise ValueError(f"No CloudWatch log group configured for {service_name}")
                    return group.strip()
            raise ValueError(f"No CloudWatch log group configured for service {service_name!r}")
    return None if service_name else config.get("DEV_CLOUDWATCH_LOG_GROUP")


def validate_service_log_groups(service_names: list[str]) -> None:
    """Fail before a scheduled scan if any service lacks an explicit log group."""
    config = _read_config()
    groups = config.get("DEV_CLOUDWATCH_LOG_GROUPS")
    if not isinstance(groups, dict):
        raise ValueError("Scheduled service scans require DEV_CLOUDWATCH_LOG_GROUPS in Secrets Manager")
    configured = {name.casefold(): value for name, value in groups.items() if isinstance(name, str)}
    missing = [name for name in service_names
               if not isinstance(configured.get(name.casefold()), str)
               or not configured[name.casefold()].strip()]
    if missing:
        raise ValueError("Missing CloudWatch log group mappings for: " + ", ".join(missing))


def cloudwatch_connection(service_name: str | None = None, *,
                          prefer_service_mapping: bool = False) -> tuple[object, str, int]:
    """Return a configured Logs client, log group, and lookback window.

    Secrets Manager fields use a DEV prefix:
    dev_aws_access_key_id, dev_aws_secret_access_key, dev_aws_session_token,
    DEV_AWS_PROFILE, DEV_AWS_REGION, DEV_CLOUDWATCH_LOG_GROUP,
    DEV_CLOUDWATCH_LOG_GROUPS, and DEV_CLOUDWATCH_LOOKBACK_HOURS. Ambient
    role/profile credentials remain the fallback when access keys are absent.
    """
    config = _read_config()
    service_group = _service_log_group(config, service_name,
                                       require_mapping=prefer_service_mapping)
    log_group = (service_group if prefer_service_mapping else
                 os.environ.get("INCIDENT_LOG_GROUP") or service_group)
    region = (os.environ.get("INCIDENT_AWS_REGION") or config.get("DEV_AWS_REGION")
              or os.environ.get("AWS_REGION") or "us-east-1")
    lookback_hours = int(os.environ.get("INCIDENT_LOOKBACK_HOURS")
                         or config.get("DEV_CLOUDWATCH_LOOKBACK_HOURS") or "24")
    if not 1 <= lookback_hours <= 168:
        raise ValueError("DEV_CLOUDWATCH_LOOKBACK_HOURS must be between 1 and 168")

    access_key = config.get("dev_aws_access_key_id") or config.get("DEV_AWS_ACCESS_KEY_ID")
    secret_key = config.get("dev_aws_secret_access_key") or config.get("DEV_AWS_SECRET_ACCESS_KEY")
    session_token = config.get("dev_aws_session_token") or config.get("DEV_AWS_SESSION_TOKEN")
    if bool(access_key) != bool(secret_key):
        raise ValueError("CloudWatch secret must provide both dev_aws_access_key_id and dev_aws_secret_access_key")
    explicit_profile = os.environ.get("INCIDENT_AWS_PROFILE")
    local_run = os.environ.get("INCIDENT_LOCAL_CLOUDWATCH") == "1"
    profile = (explicit_profile or config.get("DEV_AWS_PROFILE")
               or ("dev" if local_run else os.environ.get("AWS_PROFILE")))
    if access_key and (local_run or not explicit_profile):
        # This session is intentionally scoped to the CloudWatch Logs client below.
        # Never export these credentials into process-wide AWS_* variables.
        session = boto3.Session(aws_access_key_id=access_key,
                                aws_secret_access_key=secret_key,
                                aws_session_token=session_token,
                                region_name=region)
    else:
        session = boto3.Session(profile_name=profile, region_name=region)
    client = session.client("logs")
    if not log_group:
        service = service_name.casefold() if service_name else ""
        if service not in SERVICE_EXTRACTOR_GROUPS:
            raise RuntimeError("Provide a supported service_name, INCIDENT_LOG_GROUP, or a CloudWatch log group mapping")
        base = SERVICE_EXTRACTOR_GROUPS[service]
        candidates = (base, f"/aws/lambda/{base}", f"/mock/{base}")
        for candidate in candidates:
            groups = client.describe_log_groups(logGroupNamePrefix=candidate, limit=1)["logGroups"]
            if any(item["logGroupName"] == candidate for item in groups):
                log_group = candidate
                break
        if not log_group:
            raise RuntimeError(
                f"No extractor log group found for {service_name} in {region}; "
                "supply --log-group or configure DEV_CLOUDWATCH_LOG_GROUPS"
            )
    return client, str(log_group), lookback_hours
