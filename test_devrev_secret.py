import json
import unittest
from unittest.mock import Mock

from botocore.exceptions import ClientError

from devrev_secret import load_token, store_token


def client_error(code):
    return ClientError({"Error": {"Code": code, "Message": "mock"}}, "SecretsManager")


class DevRevSecretTests(unittest.TestCase):
    def test_store_and_load_token_without_exposing_it(self):
        service = Mock()
        service.get_secret_value.return_value = {
            "SecretString": json.dumps({"jira_api_token": "existing-jira-value", "jira_project_key": "OPS"})
        }
        store_token(service, "incident-repro/jira", "secret-value")
        service.put_secret_value.assert_called_once_with(
            SecretId="incident-repro/jira",
            SecretString=json.dumps({
                "jira_api_token": "existing-jira-value",
                "jira_project_key": "OPS",
                "devrev_api_token": "secret-value",
            }),
        )
        service.get_secret_value.return_value = {
            "SecretString": json.dumps({
                "jira_api_token": "existing-jira-value", "devrev_api_token": "secret-value"
            })
        }
        self.assertEqual(load_token(service, "incident-repro/jira"), "secret-value")

    def test_creates_secret_when_name_does_not_exist(self):
        service = Mock()
        service.get_secret_value.side_effect = client_error("ResourceNotFoundException")
        service.put_secret_value.side_effect = client_error("ResourceNotFoundException")
        store_token(service, "incident-repro/jira", "secret-value")
        service.create_secret.assert_called_once_with(
            Name="incident-repro/jira",
            SecretString=json.dumps({"devrev_api_token": "secret-value"}),
            Description="DevRev API token for extractor incident issues",
        )

    def test_expired_aws_token_gives_refresh_guidance(self):
        service = Mock()
        service.get_secret_value.side_effect = client_error("ExpiredTokenException")
        with self.assertRaisesRegex(RuntimeError, "AWS credentials expired"):
            load_token(service, "incident-repro/jira")


if __name__ == "__main__":
    unittest.main()
