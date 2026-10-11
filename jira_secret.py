"""Configure and inspect the Jira repro secret without printing its values."""

import argparse
import getpass
import json
from urllib.parse import urlparse

import boto3
from botocore.exceptions import ClientError

from devrev_secret import store_token as store_devrev_token


DEFAULT_SECRET = "incident-repro/jira"
FIELDS = (
    ("jira_base_url", "Jira Cloud URL (https://...)"),
    ("jira_email", "Jira account email"),
    ("jira_project_key", "Dedicated Jira project key"),
    ("jira_issue_type", "Issue type name (default Task)"),
    ("dev_org", "DevRev test org ID (optional)"),
    ("dev_user", "DevRev test user ID (optional)"),
    ("keyring_id", "DevRev Jira keyring ID (optional)"),
    ("external_system_id", "Jira hostname for extractor (optional)"),
    ("external_sync_unit_id", "Numeric Jira project ID (optional)"),
)


def client(profile: str | None = None, region: str | None = None):
    if profile is None and region is None:
        return boto3.client("secretsmanager")
    return boto3.Session(profile_name=profile, region_name=region).client("secretsmanager")


def load_secret(service, secret_id: str) -> dict:
    try:
        metadata = service.describe_secret(SecretId=secret_id)
    except service.exceptions.ResourceNotFoundException:
        raise ValueError(
            "Jira secret does not exist in the selected AWS account") from None
    if not metadata.get("VersionIdsToStages"):
        raise ValueError(
            "Jira secret has no value; run jira_secret.py configure first")
    value = service.get_secret_value(SecretId=secret_id).get("SecretString")
    if not value:
        raise ValueError(
            "Jira secret has no value; run jira_secret.py configure first")
    data = json.loads(value)
    if not isinstance(data, dict):
        raise ValueError("Jira secret must contain a JSON object")
    return data


def configure_devrev_token(service, secret_id: str, token: str) -> None:
    """Save the DevRev token in the shared Jira/DevRev secret."""
    token = token.strip()
    if not token:
        raise ValueError("DevRev API token must not be empty")
    store_devrev_token(service, secret_id, token)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("configure", "configure-devrev-token", "status"))
    parser.add_argument("--secret-id", default=DEFAULT_SECRET)
    args = parser.parse_args()
    try:
        service = client()
        if args.command == "configure":
            try:
                previous = load_secret(service, args.secret_id)
            except ValueError as exc:
                if "has no value" not in str(exc):
                    raise
                previous = {}
            updated = dict(previous)
            print(
                "Enter Jira test-org settings. Leave a field blank to keep its existing value.")
            for key, label in FIELDS:
                value = input(f"{label}: ").strip()
                if value:
                    updated[key] = value
            token = getpass.getpass(
                "Jira API token (hidden; blank keeps existing): ")
            if token:
                updated["jira_api_token"] = token
            for key in ("jira_base_url", "jira_email", "jira_project_key", "jira_api_token"):
                if not updated.get(key):
                    raise ValueError(f"Missing required value: {key}")
            parsed = urlparse(updated["jira_base_url"])
            if (parsed.scheme != "https" or not parsed.hostname or parsed.path not in ("", "/")
                    or parsed.username or parsed.password or parsed.query):
                raise ValueError("Jira URL must be a bare https:// host")
            updated["jira_base_url"] = updated["jira_base_url"].rstrip("/")
            updated.setdefault("jira_issue_type", "Task")
            service.put_secret_value(
                SecretId=args.secret_id, SecretString=json.dumps(updated))
            print(f"Saved Jira configuration to {args.secret_id}.")
        elif args.command == "configure-devrev-token":
            token = getpass.getpass("DevRev API token (hidden): ")
            configure_devrev_token(service, args.secret_id, token)
            print(f"Saved DevRev API token in shared secret {args.secret_id}.")
        elif args.command == "status":
            data = load_secret(service, args.secret_id)
            print(f"{args.secret_id}: configured keys: {
                  ', '.join(sorted(data))}")
    except ValueError as exc:
        parser.exit(1, f"Jira secret operation failed: {exc}\n")
    except RuntimeError as exc:
        parser.exit(1, f"Jira secret operation failed: {exc}\n")
    except (ClientError, json.JSONDecodeError) as exc:
        parser.exit(1, f"Jira secret operation failed: {type(exc).__name__}\n")


if __name__ == "__main__":
    main()
