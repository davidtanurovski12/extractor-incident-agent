"""Fetch scoped extractor events from CloudWatch and save them as JSONL."""

import argparse
import json
import os
from pathlib import Path

from investigation import MAX_EVENTS, search_sync_logs


def write_jsonl(events: list[dict], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output:
        for event in events:
            output.write(json.dumps(event, separators=(",", ":")) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-group", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--sync-unit-id", required=True)
    parser.add_argument("--profile", default="dev", help="AWS profile with CloudWatch access")
    parser.add_argument("--region", default="us-east-1", help="Region containing the log group")
    parser.add_argument("--lookback-hours", type=int, default=24)
    parser.add_argument("--limit", type=int, default=MAX_EVENTS)
    parser.add_argument("--output", type=Path, default=Path("incident-logs.jsonl"))
    args = parser.parse_args()

    if not 1 <= args.lookback_hours <= 168:
        parser.error("--lookback-hours must be between 1 and 168")
    if not 1 <= args.limit <= MAX_EVENTS:
        parser.error(f"--limit must be between 1 and {MAX_EVENTS}")

    os.environ["INCIDENT_LOG_GROUP"] = args.log_group
    os.environ["INCIDENT_AWS_PROFILE"] = args.profile
    os.environ["INCIDENT_AWS_REGION"] = args.region
    os.environ["INCIDENT_LOOKBACK_HOURS"] = str(args.lookback_hours)
    os.environ.pop("INCIDENT_LOG_FILE", None)

    result = search_sync_logs(args.run_id, args.sync_unit_id, args.limit)
    for event in result["events"]:
        event.setdefault("run_id", args.run_id)
        event.setdefault("sync_unit_id", args.sync_unit_id)
    write_jsonl(result["events"], args.output)

    print(f"Fetched {len(result['events'])} matching events to {args.output}")
    if result["truncated"]:
        print(f"Results were capped at {args.limit}; increase the limit (max {MAX_EVENTS}) or narrow the time window.")
    if result.get("query_id"):
        print(f"CloudWatch query ID: {result['query_id']}")


if __name__ == "__main__":
    main()
