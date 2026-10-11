"""Shared Secrets Manager helpers for Jira and DevRev agent configuration."""

import json

import boto3
from botocore.exceptions import ClientError


DEFAULT_SECRET_ID = "incident-repro/jira"
TOKEN_FIELD = "devrev_api_token"


def secret_client(profile: str | None = None, region: str | None = None):
    if profile is None and region is None:
        return boto3.client("secretsmanager")
    return boto3.Session(profile_name=profile, region_name=region).client("secretsmanager")


def load_token(service, secret_id: str) -> str:
    config = _read_config(service, secret_id)
    if not config.get(TOKEN_FIELD):
        raise ValueError(f"Secret {secret_id!r} is missing {TOKEN_FIELD}; run jira_secret.py configure-devrev-token")
    return str(config[TOKEN_FIELD])


def _read_config(service, secret_id: str) -> dict:
    try:
        response = service.get_secret_value(SecretId=secret_id)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        if code == "ResourceNotFoundException":
            return {}
        if code in ("ExpiredToken", "ExpiredTokenException"):
            raise RuntimeError("AWS credentials expired; refresh your AWS session and retry") from None
        raise RuntimeError(f"Unable to read DevRev secret ({code})") from None
    value = response.get("SecretString")
    if not value:
        return {}
    try:
        config = json.loads(value)
    except json.JSONDecodeError:
        raise ValueError("Shared secret must contain a JSON object") from None
    if not isinstance(config, dict):
        raise ValueError("Shared secret must contain a JSON object")
    return config


def store_token(service, secret_id: str, token: str) -> None:
    config = _read_config(service, secret_id)
    config[TOKEN_FIELD] = token
    secret_string = json.dumps(config)
    try:
        service.put_secret_value(SecretId=secret_id, SecretString=secret_string)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        if code != "ResourceNotFoundException":
            raise RuntimeError(f"Unable to save DevRev secret ({code})") from None
        try:
            service.create_secret(Name=secret_id, SecretString=secret_string,
                                  Description="DevRev API token for extractor incident issues")
        except ClientError as create_exc:
            create_code = create_exc.response.get("Error", {}).get("Code", "Unknown")
            raise RuntimeError(f"Unable to create DevRev secret ({create_code})") from None
