"""Run the investigation agent against live CloudWatch logs or an exported file."""

import argparse
import os

from agent import investigate
from agent_logging import configure_cloudwatch_activity


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", help="Run ID; inferred from a file export when omitted")
    parser.add_argument("--sync-unit-id", help="Sync unit ID; inferred from a file export when omitted")
    parser.add_argument("--service-name", help="Service identity used for S3 report deduplication")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--log-file", help="Exported JSONL log file")
    source.add_argument("--log-group", help="CloudWatch Logs group (queries live logs)")
    parser.add_argument("--lookback-hours", type=int, default=24)
    parser.add_argument("--activity-log-group", help="Existing CloudWatch group for this agent's activity logs")
    parser.add_argument("--prepare-missing-record", choices=("jira", "salesforce", "servicenow", "zendesk"),
                        help="Constrain automatic missing-record preparation to this external source")
    parser.add_argument("--create-devrev-issue", action="store_true",
                        help="Deprecated compatibility flag; issue creation is automatic")
    args = parser.parse_args()
    if not args.log_file and not args.log_group and not (args.service_name or os.environ.get("INCIDENT_SERVICE_NAME")):
        parser.error("provide --log-file, --log-group, or --service-name to select an extractor log group")
    if args.activity_log_group:
        configure_cloudwatch_activity(args.activity_log_group,
                                      os.environ.get("AWS_REGION") or "us-east-1",
                                      os.environ.get("AWS_PROFILE"))
    if args.log_file:
        os.environ["INCIDENT_LOG_FILE"] = args.log_file
        os.environ.pop("INCIDENT_LOG_GROUP", None)
        run_id, sync_unit_id = args.run_id, args.sync_unit_id
    else:
        os.environ.pop("INCIDENT_LOG_FILE", None)
        os.environ["INCIDENT_LOCAL_CLOUDWATCH"] = "1"
        os.environ.setdefault("DEV_CLOUDWATCH_SECRET_ID", "incident-repro/jira")
        os.environ.setdefault("DEV_SECRETS_REGION", "us-west-2")
        if args.log_group:
            os.environ["INCIDENT_LOG_GROUP"] = args.log_group
        else:
            os.environ.pop("INCIDENT_LOG_GROUP", None)
        os.environ["INCIDENT_LOOKBACK_HOURS"] = str(args.lookback_hours)
        run_id, sync_unit_id = args.run_id, args.sync_unit_id
    print(investigate(run_id, sync_unit_id, repro_source=args.prepare_missing_record,
                      service_name=args.service_name or os.environ.get("INCIDENT_SERVICE_NAME")))


if __name__ == "__main__":
    main()
