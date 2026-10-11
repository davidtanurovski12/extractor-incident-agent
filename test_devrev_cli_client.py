import json
import os
import unittest
from unittest.mock import patch

from devrev_cli_client import create_issue


class DevRevCLIClientTests(unittest.TestCase):
    def test_issue_uses_the_selected_dev_profile_and_org(self):
        completed = type("Completed", (), {
            "returncode": 0,
            "stdout": json.dumps({"work": {"id": "don:core:issue/1", "display_id": "ISS-1"}}),
            "stderr": "",
        })()
        with patch.dict(os.environ, {
            "DEVREV_CLI_PATH": "/usr/bin/devrev",
            "DEVREV_APPLIES_TO_PART": "don:core:part/1",
            "DEVREV_OWNED_BY": "DEVU-1",
        }, clear=True), patch("devrev_cli_client.subprocess.run", return_value=completed) as run:
            result = create_issue("Jira", "invalid previous IDs", "Finding and proposed fix")

        args, kwargs = run.call_args
        self.assertEqual(args[0], [
            "/usr/bin/devrev", "--env", "dev", "--org", "devrevxaws-demo",
            "--usr", "i-david.tanurovski@devrev.ai", "works", "create", "--no-progress",
        ])
        payload = json.loads(kwargs["input"])
        self.assertEqual(payload["title"], "Extractor incident: Jira - invalid previous IDs")
        self.assertEqual(result["display_id"], "ISS-1")

    def test_similar_existing_issue_is_reused_without_creating_another(self):
        listing = type("Completed", (), {
            "returncode": 0,
            "stdout": json.dumps({"works": [{
                "id": "don:core:issue/existing", "display_id": "ISS-OLD",
                "title": "AgentInspetion: Jira extractor invalid previous issue IDs",
            }]}),
            "stderr": "",
        })()
        with patch.dict(os.environ, {
            "DEVREV_CLI_PATH": "/usr/bin/devrev",
            "DEVREV_APPLIES_TO_PART": "don:core:part/1",
        }, clear=True), patch("devrev_cli_client.subprocess.run", return_value=listing) as run:
            result = create_issue("Jira", "invalid previous IDs", "New investigation report")

        self.assertEqual(run.call_count, 1)
        self.assertIn("list", run.call_args.args[0])
        self.assertEqual(result["status"], "existing")
        self.assertEqual(result["display_id"], "ISS-OLD")

    def test_new_issue_is_created_after_duplicate_lookup(self):
        listing = type("Completed", (), {"returncode": 0, "stdout": "[]", "stderr": ""})()
        created = type("Completed", (), {
            "returncode": 0,
            "stdout": json.dumps({"work": {"id": "don:core:issue/new", "display_id": "ISS-NEW"}}),
            "stderr": "",
        })()
        with patch.dict(os.environ, {
            "DEVREV_CLI_PATH": "/usr/bin/devrev",
            "DEVREV_APPLIES_TO_PART": "don:core:part/1",
        }, clear=True), patch("devrev_cli_client.subprocess.run",
                              side_effect=[listing, created]) as run:
            result = create_issue("Jira", "invalid previous IDs", "Evidence")

        self.assertEqual(run.call_count, 2)
        self.assertIn("list", run.call_args_list[0].args[0])
        self.assertIn("create", run.call_args_list[1].args[0])
        self.assertEqual(result["id"], "don:core:issue/new")

    def test_issue_uses_external_system_specific_part(self):
        completed = type("Completed", (), {
            "returncode": 0,
            "stdout": json.dumps({"work": {"id": "don:core:issue/2", "display_id": "ISS-2"}}),
            "stderr": "",
        })()
        mapping = json.dumps({"Jira": "jira-part-id", "Salesforce": "salesforce-part-id"})
        with patch.dict(os.environ, {
            "DEVREV_CLI_PATH": "/usr/bin/devrev",
            "DEVREV_APPLIES_TO_PARTS": mapping,
            "DEVREV_OWNED_BY": "DEVU-1",
        }, clear=True), patch("devrev_cli_client.subprocess.run", return_value=completed) as run:
            create_issue(" jira ", "test routing", "Verifies system-specific applies-to routing.")

        payload = json.loads(run.call_args.kwargs["input"])
        self.assertEqual(payload["applies_to_part"], "jira-part-id")

    def test_issue_routes_to_default_system_part_without_environment_mapping(self):
        completed = type("Completed", (), {
            "returncode": 0,
            "stdout": json.dumps({"work": {"id": "don:core:issue/3", "display_id": "ISS-3"}}),
            "stderr": "",
        })()
        with patch.dict(os.environ, {
            "DEVREV_CLI_PATH": "/usr/bin/devrev",
        }, clear=True), patch("devrev_cli_client.subprocess.run", return_value=completed) as run:
            create_issue("Salesforce", "test default routing", "Checks built-in system routing.")

        payload = json.loads(run.call_args.kwargs["input"])
        self.assertEqual(
            payload["applies_to_part"],
            "don:core:dvrv-us-1:devo/1W9hyutyQQ:product/4",
        )
        self.assertEqual(payload["owned_by"], ["DEVU-1"])

    def test_unknown_system_requires_a_configured_part(self):
        with patch.dict(os.environ, {
            "DEVREV_APPLIES_TO_PARTS": '{"Jira":"jira-part-id"}',
            "DEVREV_OWNED_BY": "DEVU-1",
        }, clear=True):
            with self.assertRaisesRegex(ValueError, "No DevRev applies-to part is configured for Acme"):
                create_issue("Acme", "test routing", "Finding")

    def test_cli_error_is_reported_without_creating_an_issue(self):
        completed = type("Completed", (), {
            "returncode": 1, "stdout": "", "stderr": "not authenticated"
        })()
        with patch.dict(os.environ, {
            "DEVREV_CLI_PATH": "/usr/bin/devrev",
            "DEVREV_APPLIES_TO_PART": "part/1",
            "DEVREV_OWNED_BY": "DEVU-1",
        }), patch("devrev_cli_client.subprocess.run", return_value=completed):
            with self.assertRaisesRegex(RuntimeError, "not authenticated"):
                create_issue("Jira", "failure", "Summary")


if __name__ == "__main__":
    unittest.main()
