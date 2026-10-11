"""AgentCore Runtime entry point; supply INCIDENT_LOG_FILE in the runtime environment."""

import logging
import os

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from agent import investigate
from agent_logging import emit
from dev_cloudwatch import validate_service_log_groups


SCHEDULED_SERVICES = ("Zendesk", "Salesforce", "Jira", "ServiceNow")


app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload: dict) -> dict:
    os.environ.setdefault("INCIDENT_RUNTIME_SOURCE", "airsync-metis")
    return handle_payload(payload)


def handle_payload(payload: dict) -> dict:
    if payload.get("scan_all_services") is True:
        validate_service_log_groups(list(SCHEDULED_SERVICES))
        reports = []
        for service_name in SCHEDULED_SERVICES:
            emit("schedule.service_scan.started", service_name=service_name)
            try:
                report = investigate(None, None, service_name=service_name,
                                     allow_empty_errors=True,
                                     prefer_service_mapping=True)
                status = "completed"
            except Exception as exc:
                emit("schedule.service_scan.failed", level=logging.ERROR,
                     service_name=service_name, error_type=type(exc).__name__)
                report = f"Service scan failed ({type(exc).__name__})."
                status = "failed"
            reports.append({"service_name": service_name, "status": status,
                            "report": report})
        return {"reports": reports}

    return {"report": investigate(payload.get("run_id"), payload.get("sync_unit_id"),
                                   service_name=payload.get("service_name"))}


if __name__ == "__main__":
    app.run()
