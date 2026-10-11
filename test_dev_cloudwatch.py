import json
import os
import unittest
from unittest.mock import Mock, patch

from dev_cloudwatch import cloudwatch_connection, validate_service_log_groups


class DevCloudWatchTests(unittest.TestCase):
    def test_reads_dev_prefixed_config_and_credentials_from_secret(self):
        log_client = Mock()
        secret_client = Mock()
        secret_client.get_secret_value.return_value = {
            "SecretString": json.dumps({
                "DEV_CLOUDWATCH_LOG_GROUP": "/aws/lambda/dev-extractor",
                "DEV_CLOUDWATCH_LOOKBACK_HOURS": "36",
                "DEV_AWS_REGION": "us-west-2",
                "dev_aws_access_key_id": "access-key",
                "dev_aws_secret_access_key": "secret-key",
                "dev_aws_session_token": "session-token",
            })
        }
        secret_session = Mock()
        secret_session.client.return_value = secret_client
        logs_session = Mock()
        logs_session.client.return_value = log_client

        with patch.dict("os.environ", {
            "DEV_CLOUDWATCH_SECRET_ID": "incident-repro/dev-cloudwatch",
        }, clear=True), patch("dev_cloudwatch.boto3.Session",
                              side_effect=[secret_session, logs_session]) as session:
            client, log_group, hours = cloudwatch_connection()
            self.assertNotIn("AWS_ACCESS_KEY_ID", os.environ)
            self.assertNotIn("AWS_SECRET_ACCESS_KEY", os.environ)
            self.assertNotIn("AWS_SESSION_TOKEN", os.environ)

        self.assertIs(client, log_client)
        self.assertEqual(log_group, "/aws/lambda/dev-extractor")
        self.assertEqual(hours, 36)
        self.assertEqual(session.call_args_list[1].kwargs, {
            "aws_access_key_id": "access-key",
            "aws_secret_access_key": "secret-key",
            "aws_session_token": "session-token",
            "region_name": "us-west-2",
        })
        secret_client.get_secret_value.assert_called_once_with(
            SecretId="incident-repro/dev-cloudwatch")

    def test_uses_ambient_credentials_and_env_fallback_when_secret_disabled(self):
        logs_client = Mock()
        session_instance = Mock()
        session_instance.client.return_value = logs_client
        with patch.dict("os.environ", {
            "DEV_CLOUDWATCH_SECRET_ID": "",
            "INCIDENT_LOG_GROUP": "/aws/lambda/from-env",
            "INCIDENT_AWS_PROFILE": "dev-logs",
            "INCIDENT_AWS_REGION": "us-east-1",
            "INCIDENT_LOOKBACK_HOURS": "12",
        }, clear=True), patch("dev_cloudwatch.boto3.Session",
                              return_value=session_instance) as session:
            client, log_group, hours = cloudwatch_connection()

        session.assert_called_once_with(profile_name="dev-logs", region_name="us-east-1")
        self.assertIs(client, logs_client)
        self.assertEqual((log_group, hours), ("/aws/lambda/from-env", 12))

    def test_rejects_incomplete_dev_aws_key_pair(self):
        secret_client = Mock()
        secret_client.get_secret_value.return_value = {
            "SecretString": json.dumps({
                "DEV_CLOUDWATCH_LOG_GROUP": "/aws/lambda/dev-extractor",
                "dev_aws_access_key_id": "access-key",
            })
        }
        secret_session = Mock()
        secret_session.client.return_value = secret_client
        with patch.dict("os.environ", {}, clear=True), \
                patch("dev_cloudwatch.boto3.Session", return_value=secret_session):
            with self.assertRaisesRegex(ValueError, "both dev_aws_access_key_id"):
                cloudwatch_connection()

    def test_legacy_uppercase_credentials_remain_supported(self):
        secret_client = Mock()
        secret_client.get_secret_value.return_value = {
            "SecretString": json.dumps({
                "DEV_CLOUDWATCH_LOG_GROUP": "/aws/lambda/dev-extractor",
                "DEV_AWS_ACCESS_KEY_ID": "legacy-access",
                "DEV_AWS_SECRET_ACCESS_KEY": "legacy-secret",
                "DEV_AWS_SESSION_TOKEN": "legacy-token",
            })
        }
        secret_session = Mock()
        secret_session.client.return_value = secret_client
        with patch.dict("os.environ", {}, clear=True), \
                patch("dev_cloudwatch.boto3.Session", side_effect=[secret_session, Mock()]) as session:
            cloudwatch_connection()
        self.assertEqual(session.call_args_list[1].kwargs["aws_access_key_id"], "legacy-access")
        self.assertEqual(session.call_args_list[1].kwargs["aws_session_token"], "legacy-token")

    def test_routes_service_names_to_distinct_log_groups(self):
        mapping = {
            "Zendesk": "/aws/lambda/airdrop-zendesk-extractor",
            "Salesforce": "/aws/lambda/airdrop-salesforce-extractor",
            "Jira": "/aws/lambda/airdrop-jira-extractor",
            "ServiceNow": "/aws/lambda/airdrop-servicenow-extractor",
        }
        secret_client = Mock()
        secret_client.get_secret_value.return_value = {
            "SecretString": json.dumps({"DEV_AWS_REGION": "us-east-1",
                                        "DEV_CLOUDWATCH_LOG_GROUPS": mapping})
        }
        secret_session = Mock()
        secret_session.client.return_value = secret_client
        logs_session = Mock()
        logs_session.client.return_value = Mock()
        with patch.dict("os.environ", {"INCIDENT_LOG_GROUP": "/aws/lambda/legacy"}, clear=True), \
                patch("dev_cloudwatch.boto3.Session",
                      side_effect=[secret_session, logs_session, secret_session,
                                   logs_session, secret_session]):
            _, group, _ = cloudwatch_connection("Salesforce", prefer_service_mapping=True)
            _, next_group, _ = cloudwatch_connection("ServiceNow", prefer_service_mapping=True)
            validate_service_log_groups(list(mapping))

        self.assertEqual(group, mapping["Salesforce"])
        self.assertEqual(next_group, mapping["ServiceNow"])

    def test_discovers_service_group_and_falls_back_to_mock(self):
        logs_client = Mock()
        logs_client.describe_log_groups.side_effect = [
            {"logGroups": []},
            {"logGroups": []},
            {"logGroups": [{"logGroupName": "/mock/airdrop-zendesk-extractor"}]},
        ]
        session = Mock()
        session.client.return_value = logs_client
        with patch.dict("os.environ", {"DEV_CLOUDWATCH_SECRET_ID": ""}, clear=True), \
                patch("dev_cloudwatch.boto3.Session", return_value=session):
            _, group, _ = cloudwatch_connection("Zendesk")

        self.assertEqual(group, "/mock/airdrop-zendesk-extractor")
        self.assertEqual(logs_client.describe_log_groups.call_count, 3)

    def test_resolves_each_service_to_its_named_extractor_group(self):
        from dev_cloudwatch import SERVICE_EXTRACTOR_GROUPS

        for service, expected in SERVICE_EXTRACTOR_GROUPS.items():
            with self.subTest(service=service):
                logs_client = Mock()
                logs_client.describe_log_groups.return_value = {
                    "logGroups": [{"logGroupName": expected}]
                }
                session = Mock()
                session.client.return_value = logs_client
                with patch.dict("os.environ", {"DEV_CLOUDWATCH_SECRET_ID": ""}, clear=True), \
                        patch("dev_cloudwatch.boto3.Session", return_value=session):
                    _, group, _ = cloudwatch_connection(service.title())
                self.assertEqual(group, expected)
                logs_client.describe_log_groups.assert_called_once_with(
                    logGroupNamePrefix=expected, limit=1)

    def test_does_not_use_unrelated_group_when_service_group_missing(self):
        logs_client = Mock()
        logs_client.describe_log_groups.return_value = {"logGroups": []}
        session = Mock()
        session.client.return_value = logs_client
        with patch.dict("os.environ", {"DEV_CLOUDWATCH_SECRET_ID": ""}, clear=True), \
                patch("dev_cloudwatch.boto3.Session", return_value=session):
            with self.assertRaisesRegex(RuntimeError, "No extractor log group found for Zendesk"):
                cloudwatch_connection("Zendesk")

    def test_local_live_reads_default_to_dev_profile_without_secret(self):
        logs_client = Mock()
        logs_client.describe_log_groups.return_value = {
            "logGroups": [{"logGroupName": "airdrop-zendesk-extractor"}]
        }
        session = Mock()
        session.client.return_value = logs_client
        with patch.dict("os.environ", {
            "DEV_CLOUDWATCH_SECRET_ID": "",
            "INCIDENT_LOCAL_CLOUDWATCH": "1",
            "AWS_PROFILE": "default",
        }, clear=True), patch("dev_cloudwatch.boto3.Session", return_value=session) as sessions:
            _, group, _ = cloudwatch_connection("Zendesk")
        self.assertEqual(group, "airdrop-zendesk-extractor")
        sessions.assert_called_once_with(profile_name="dev", region_name="us-east-1")

    def test_local_secret_credentials_override_source_profile(self):
        logs_session = Mock()
        logs_session.client.return_value = Mock()
        with patch.dict("os.environ", {
            "INCIDENT_AWS_PROFILE": "dev",
            "INCIDENT_LOCAL_CLOUDWATCH": "1",
        }, clear=True), patch("dev_cloudwatch._read_config", return_value={
            "dev_aws_access_key_id": "from-secret",
            "dev_aws_secret_access_key": "from-secret",
            "DEV_CLOUDWATCH_LOG_GROUPS": {"Zendesk": "airdrop-zendesk-extractor"},
        }), patch("dev_cloudwatch.boto3.Session", return_value=logs_session) as session:
            _, group, _ = cloudwatch_connection("Zendesk")
        self.assertEqual(group, "airdrop-zendesk-extractor")
        self.assertEqual(session.call_args.kwargs["aws_access_key_id"], "from-secret")
        self.assertNotIn("profile_name", session.call_args.kwargs)

    def test_scheduled_validation_rejects_missing_service_log_group(self):
        secret_client = Mock()
        secret_client.get_secret_value.return_value = {
            "SecretString": json.dumps({"DEV_CLOUDWATCH_LOG_GROUPS": {
                "Jira": "/aws/lambda/airdrop-jira-extractor",
            }})
        }
        session = Mock()
        session.client.return_value = secret_client
        with patch.dict("os.environ", {}, clear=True), \
                patch("dev_cloudwatch.boto3.Session", return_value=session):
            with self.assertRaisesRegex(ValueError, "Salesforce"):
                validate_service_log_groups(["Jira", "Salesforce"])


if __name__ == "__main__":
    unittest.main()
