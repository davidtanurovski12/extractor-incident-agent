# Extractor Incident Investigator

A Strands-based agent that investigates failed extractor syncs by correlating
CloudWatch errors with source code, then reports evidence and proposed fixes.
It supports Jira, Salesforce, ServiceNow, and Zendesk, with local JSONL replay
and an AgentCore entrypoint (`app.py`).

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -q
AWS_REGION=us-east-1 .venv/bin/python local.py \
  --service-name Zendesk \
  --activity-log-group /mock/agent-investigator
```

Live log access uses the configured AWS profiles or a Secrets Manager credential
bundle; see `dev_cloudwatch.py` and `local.py` for the current local defaults.
Provide access to CloudWatch Logs and Bedrock before running an investigation.
The agent only fetches a source snapshot when running through its AgentCore
entrypoint or when `INCIDENT_RUNTIME_SOURCE=airsync-metis` is set.

AgentCore deployment configuration and an end-to-end hosted demo are not yet
included. This repository contains source and tests only; no log exports,
credentials, or live incident data are required to run the unit tests.
