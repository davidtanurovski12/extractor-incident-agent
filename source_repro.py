"""Prepare a missing-record API condition on a controlled external test instance.

Each source creates one marker-tagged record, then removes only that record
after a separate extractor run has checkpointed it. No extractor is run here.
"""

import argparse
import base64
import getpass
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from botocore.exceptions import ClientError

from jira_secret import client, load_secret


SOURCES = {
    "salesforce": ("instance_url", "access_token"),
    "servicenow": ("instance_url", "username", "password"),
    "zendesk": ("instance_url", "email", "api_token"),
}
SECRETS = {"access_token", "password", "api_token"}


def api(config: dict, source: str, method: str, path: str, body: dict | None = None) -> dict:
    url = config["instance_url"].rstrip("/")
    if not url.startswith("https://"):
        raise ValueError("instance_url must start with https://")
    if source == "salesforce":
        auth = "Bearer " + config["access_token"]
    else:
        user = config["username"] if source == "servicenow" else config["email"] + "/token"
        token = config["password"] if source == "servicenow" else config["api_token"]
        auth = "Basic " + base64.b64encode(f"{user}:{token}".encode()).decode()
    request = urllib.request.Request(
        url + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": auth, "Accept": "application/json",
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            content = response.read()
            return json.loads(content) if content else {}
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{source} {method} {path} returned HTTP {exc.code}") from None


def record_path(source: str, record_id: str) -> str:
    ident = urllib.parse.quote(record_id, safe="")
    if source == "salesforce":
        return f"/services/data/v60.0/sobjects/Account/{ident}"
    if source == "servicenow":
        return f"/api/now/table/incident/{ident}"
    return f"/api/v2/tickets/{ident}.json"


def record_marker(source: str, response: dict) -> str | None:
    if source == "salesforce":
        return response.get("Name")
    if source == "servicenow":
        return response.get("result", {}).get("short_description")
    return response.get("ticket", {}).get("subject")


def create(config: dict, source: str, manifest: Path) -> dict:
    if manifest.exists():
        raise ValueError(f"Manifest already exists: {manifest}")
    marker = f"Extractor missing-record repro {uuid.uuid4()}"
    if source == "salesforce":
        response = api(config, source, "POST", "/services/data/v60.0/sobjects/Account", {"Name": marker})
        record_id = str(response["id"])
    elif source == "servicenow":
        response = api(config, source, "POST", "/api/now/table/incident", {"short_description": marker})
        record_id = str(response["result"]["sys_id"])
    else:
        response = api(config, source, "POST", "/api/v2/tickets.json",
                       {"ticket": {"subject": marker, "comment": {"body": marker}}})
        record_id = str(response["ticket"]["id"])
    saved = {"source": source, "instance_url": config["instance_url"].rstrip("/"),
             "record_id": record_id, "marker": marker, "state": "created"}
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(saved, indent=2) + "\n", encoding="utf-8")
    print(f"Created {source} record {record_id}; manifest: {manifest}")
    print("Run an initial extractor sync and verify this ID is in its checkpoint before removal.")
    return saved


def remove(config: dict, source: str, manifest: Path) -> dict:
    saved = json.loads(manifest.read_text(encoding="utf-8"))
    if (saved["source"] != source or saved["instance_url"] != config["instance_url"].rstrip("/")
            or saved.get("state") != "created"):
        raise ValueError("Manifest does not match the configured source or has already been used")
    record_id = saved["record_id"]
    path = record_path(source, record_id)
    current = api(config, source, "GET", path)
    if record_marker(source, current) != saved["marker"]:
        raise ValueError("Remote record marker differs from manifest; refusing to remove")
    api(config, source, "DELETE", path)
    for _ in range(3):
        try:
            api(config, source, "GET", path)
        except RuntimeError as exc:
            if "HTTP 404" in str(exc):
                saved["state"] = "removed"
                manifest.write_text(json.dumps(saved, indent=2) + "\n", encoding="utf-8")
                print(f"Removed {source} record {record_id}; its detail API now returns HTTP 404.")
                return saved
            raise
        time.sleep(1)
    raise RuntimeError("DELETE completed but the detail API still returns a record")


def configure(service, source: str, secret_id: str) -> None:
    try:
        existing = load_secret(service, secret_id)
    except ValueError as exc:
        if "does not exist" not in str(exc):
            raise
        existing = {}
    values = dict(existing)
    print("Enter test-instance settings; blank keeps the current value.")
    for field in SOURCES[source]:
        if field in SECRETS:
            value = getpass.getpass(f"{field} (hidden): ")
        else:
            value = input(f"{field}: ").strip()
        if value:
            values[field] = value
    if any(not values.get(field) for field in SOURCES[source]):
        raise ValueError("All configured source fields are required")
    if not values["instance_url"].startswith("https://"):
        raise ValueError("instance_url must start with https://")
    values["instance_url"] = values["instance_url"].rstrip("/")
    if existing:
        service.put_secret_value(SecretId=secret_id, SecretString=json.dumps(values))
    else:
        service.create_secret(Name=secret_id, SecretString=json.dumps(values))
    print(f"Saved {source} settings to {secret_id}; token/password not displayed.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", choices=SOURCES)
    parser.add_argument("action", choices=("configure", "status", "create", "remove"))
    parser.add_argument("--secret-id", help="Defaults to incident-repro/<source>")
    parser.add_argument("--profile", default="default", help="AWS profile holding the secret")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--manifest", type=Path, help="Required for create and remove")
    args = parser.parse_args()
    secret_id = args.secret_id or f"incident-repro/{args.source}"
    try:
        service = client(args.profile, args.region)
        if args.action == "configure":
            configure(service, args.source, secret_id)
            return
        config = load_secret(service, secret_id)
        if args.action == "status":
            print(f"{secret_id}: configured keys: {', '.join(sorted(config))}")
        elif args.manifest is None:
            parser.error("--manifest is required for create and remove")
        elif args.action == "create":
            create(config, args.source, args.manifest)
        else:
            remove(config, args.source, args.manifest)
    except (ValueError, RuntimeError, KeyError, OSError, ClientError) as exc:
        # Avoid printing third-party exceptions that may include request headers.
        if isinstance(exc, ClientError):
            parser.exit(1, "AWS secret operation failed\n")
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
