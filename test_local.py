import os
import unittest
from unittest.mock import patch

import local


class LocalCloudWatchSettingsTests(unittest.TestCase):
    def test_local_cloudwatch_defaults_to_west_region_secret_and_keeps_activity_identity(self):
        with patch.dict(os.environ, {"AWS_PROFILE": "default", "AWS_REGION": "us-east-1"}, clear=True), \
                patch("sys.argv", ["local.py", "--service-name", "Zendesk",
                                   "--activity-log-group", "/mock/agent-investigator"]), \
                patch.object(local, "configure_cloudwatch_activity") as activity, \
                patch.object(local, "investigate", return_value="done"):
            local.main()
            self.assertEqual(os.environ["DEV_CLOUDWATCH_SECRET_ID"], "incident-repro/jira")
            self.assertEqual(os.environ["DEV_SECRETS_REGION"], "us-west-2")
            self.assertEqual(os.environ["INCIDENT_LOCAL_CLOUDWATCH"], "1")
            self.assertEqual(os.environ["AWS_PROFILE"], "default")
        activity.assert_called_once_with("/mock/agent-investigator", "us-east-1", "default")


if __name__ == "__main__":
    unittest.main()
