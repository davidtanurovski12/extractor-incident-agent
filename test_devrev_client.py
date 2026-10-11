import json
import os
import unittest
from unittest.mock import MagicMock, patch

from devrev_client import create_issue


class DevRevClientTests(unittest.TestCase):
    def test_create_issue_sends_issue_summary_and_returns_work_ids(self):
        list_response = MagicMock()
        list_response.__enter__.return_value = list_response
        list_response.read.return_value = json.dumps({"works": []}).encode()
        create_response = MagicMock()
        create_response.__enter__.return_value = create_response
        create_response.read.return_value = json.dumps({
            "work": {"id": "don:core:issue/123", "display_id": "ISS-123"}
        }).encode()
        with patch.dict(os.environ, {
            "DEVREV_API_TOKEN": "secret-token",
            "DEVREV_APPLIES_TO_PARTS": json.dumps({"Jira": "don:core:prod/part-1"}),
            "DEVREV_OWNED_BY": "don:identity:user/1, don:identity:user/2",
        }), patch("devrev_client.urlopen", side_effect=[list_response, create_response]) as urlopen:
            result = create_issue("Jira", "invalid previous IDs", "Evidence and proposed fix")

        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.dev.devrev-eng.ai/works.create")
        self.assertEqual(request.get_header("Authorization"), "Bearer secret-token")
        self.assertEqual(json.loads(request.data), {
            "type": "issue",
            "title": "Extractor incident: Jira - invalid previous IDs",
            "body": "Evidence and proposed fix",
            "applies_to_part": "don:core:prod/part-1",
            "owned_by": ["don:identity:user/1", "don:identity:user/2"],
        })
        self.assertEqual(result["display_id"], "ISS-123")
        self.assertEqual(result["title"], "Extractor incident: Jira - invalid previous IDs")

    def test_requires_part_configuration_for_unknown_external_system(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "No DevRev applies-to part is configured for Acme"):
                create_issue("Acme", "Failure", "Summary")

    def test_reuses_similar_issue_in_the_service_part(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps({"works": [{
            "id": "don:core:issue/existing", "display_id": "ISS-OLD",
            "title": "Extractor incident: Jira - invalid previous issue IDs",
        }]}).encode()
        with patch.dict(os.environ, {
            "DEVREV_API_TOKEN": "secret-token",
            "DEVREV_APPLIES_TO_PARTS": json.dumps({"Jira": "don:core:prod/part-1"}),
        }, clear=True), patch("devrev_client.urlopen", return_value=response) as urlopen:
            result = create_issue("Jira", "invalid previous IDs", "New report")

        self.assertEqual(urlopen.call_count, 1)
        request = urlopen.call_args.args[0]
        self.assertTrue(request.full_url.startswith("https://api.dev.devrev-eng.ai/works.list?"))
        self.assertIn("applies_to_part=don%3Acore%3Aprod%2Fpart-1", request.full_url)
        self.assertEqual(result["status"], "existing")
        self.assertEqual(result["display_id"], "ISS-OLD")


if __name__ == "__main__":
    unittest.main()
