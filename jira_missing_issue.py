"""Create and later remove one issue in a Jira Cloud project for an extractor repro.

Run `create`, perform an initial Jira extraction, then run `remove` and perform
an incremental extraction. The manifest binds removal to the issue created by
this script. Credentials are loaded from Secrets Manager, never command args.
"""

import argparse
import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from jira_secret import client, load_secret


def jira_request(config: dict, method: str, path: str, body: dict | None = None,
                 debug: bool = False) -> tuple[int, dict]:
    url = config["jira_base_url"].rstrip("/")
    if not url.startswith("https://"):
        raise ValueError("JIRA_BASE_URL must be an https:// URL")
    username = config["jira_email"]
    token = config["jira_api_token"]
    auth = base64.b64encode(f"{username}:{token}".encode()).decode()
    if debug:
        print(f"Request: {method} {url + path}", flush=True)
        print("Headers: Accept: application/json; Content-Type: application/json; "
              "Authorization: [REDACTED]", flush=True)
        if body is not None:
            print(f"Body: {json.dumps(body, sort_keys=True)}", flush=True)
    request = urllib.request.Request(
        url + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Authorization": f"Basic {auth}", "Accept": "application/json",
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            content = response.read()
            return response.status, json.loads(content) if content else {}
    except urllib.error.HTTPError as exc:
        response = exc.read(4096).decode("utf-8", errors="replace")
        for value in (token, username, auth):
            if value:
                response = response.replace(value, "[REDACTED]")
        response = re.sub(r"(?i)(authorization\s*[:=]\s*)(?:basic|bearer)\s+\S+",
                          r"\1[REDACTED]", response)
        raise RuntimeError(f"Jira {method} {path} returned HTTP {exc.code}\n"
                           f"Response (max 4096 bytes): {response}") from None


def create(config: dict, project: str, issue_type: str, manifest: Path,
           debug: bool = False) -> None:
    if manifest.exists():
        raise ValueError(f"Manifest already exists: {manifest}")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", project):
        raise ValueError("jira_project_key must be the Jira project KEY (e.g. DEMO), not its display name")
    jira_request(config, "GET", "/rest/api/3/myself", debug=debug)
    jira_request(config, "GET", f"/rest/api/3/project/{urllib.parse.quote(project)}", debug=debug)
    summary = f"Extractor missing-checkpoint repro {uuid.uuid4()}"
    _, issue = jira_request(config, "POST", "/rest/api/3/issue", {
        "fields": {"project": {"key": project}, "issuetype": {"name": issue_type},
                   "summary": summary}
    }, debug=debug)
    result = {"jira_url": config["jira_base_url"].rstrip("/"),
              "project_key": project, "issue_id": str(issue["id"]),
              "issue_key": issue["key"], "summary": summary}
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with manifest.open("x", encoding="utf-8") as output:
        json.dump(result, output, indent=2)
        output.write("\n")
    print(f"Created {issue['key']} (id={issue['id']}); manifest: {manifest}")
    print("Run the initial extractor sync now; verify its saved last_ids includes this ID.")


def remove(config: dict, manifest: Path) -> None:
    item = json.loads(manifest.read_text(encoding="utf-8"))
    if item["jira_url"] != config["jira_base_url"].rstrip("/"):
        raise ValueError("Manifest Jira URL does not match the configured Jira URL")
    issue_id = item["issue_id"]
    if not issue_id.isdecimal():
        raise ValueError("Manifest issue ID must be numeric")
    path = f"/rest/api/3/issue/{urllib.parse.quote(issue_id)}?fields=project,summary"
    _, issue = jira_request(config, "GET", path)
    if (issue.get("key") != item["issue_key"] or
            issue.get("fields", {}).get("project", {}).get("key") != item["project_key"] or
            issue.get("fields", {}).get("summary") != item["summary"]):
        raise ValueError("Issue does not match manifest; refusing to remove it")
    jira_request(config, "DELETE", f"/rest/api/3/issue/{urllib.parse.quote(issue_id)}")
    try:
        jira_request(config, "GET", path)
    except RuntimeError as exc:
        if "HTTP 404" not in str(exc):
            raise
    else:
        raise RuntimeError("Issue still resolves; expected HTTP 404 after removal")
    print(f"Removed {item['issue_key']} (id={issue_id}); Jira GET now returns HTTP 404.")
    print("Run the incremental extractor sync with the same test sync unit/state.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--secret-id", default="incident-repro/jira")
    parser.add_argument("--secret-profile", default="default", help="AWS profile of the secret's account")
    parser.add_argument("--secret-region", default="us-east-1")
    parser.add_argument("--debug", action="store_true", help="Print request without Authorization header")
    commands = parser.add_subparsers(dest="command", required=True)
    create_parser = commands.add_parser("create")
    create_parser.add_argument("--project-key", help="Override project key in secret")
    create_parser.add_argument("--issue-type", help="Override issue type in secret")
    create_parser.add_argument("--manifest", type=Path, required=True)
    remove_parser = commands.add_parser("remove")
    remove_parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    try:
        config = load_secret(client(args.secret_profile, args.secret_region), args.secret_id)
        if args.command == "create":
            create(config, args.project_key or config["jira_project_key"],
                   args.issue_type or config.get("jira_issue_type", "Task"), args.manifest,
                   debug=args.debug)
        else:
            remove(config, args.manifest)
    except (KeyError, ValueError, RuntimeError, OSError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
