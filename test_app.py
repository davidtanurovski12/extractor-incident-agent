import unittest
from unittest.mock import call, patch

import app as agentcore_app


class AgentCoreEntrypointTests(unittest.TestCase):
    def test_hourly_scan_checks_all_services_and_continues_after_a_failure(self):
        def investigate(run_id, sync_unit_id, *, service_name, allow_empty_errors=False,
                        prefer_service_mapping=False):
            if service_name == "Salesforce":
                raise RuntimeError("CloudWatch unavailable")
            return f"{service_name} report"

        with patch.object(agentcore_app, "validate_service_log_groups") as validate, \
                patch.object(agentcore_app, "investigate", side_effect=investigate) as run:
            result = agentcore_app.handle_payload({"scan_all_services": True})

        expected = ["Zendesk", "Salesforce", "Jira", "ServiceNow"]
        validate.assert_called_once_with(expected)
        self.assertEqual(run.call_args_list, [
            call(None, None, service_name=name, allow_empty_errors=True,
                 prefer_service_mapping=True)
            for name in expected
        ])
        self.assertEqual([item["status"] for item in result["reports"]],
                         ["completed", "failed", "completed", "completed"])

    def test_normal_invocation_still_processes_a_single_service(self):
        with patch.object(agentcore_app, "investigate", return_value="single report") as run:
            result = agentcore_app.handle_payload({
                "run_id": "run-1", "sync_unit_id": "unit-1", "service_name": "Jira",
            })

        run.assert_called_once_with("run-1", "unit-1", service_name="Jira")
        self.assertEqual(result, {"report": "single report"})


if __name__ == "__main__":
    unittest.main()
