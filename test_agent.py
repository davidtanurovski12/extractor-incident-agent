import json
import unittest
from unittest.mock import patch

import agent


class AgentWorkflowTests(unittest.TestCase):
    def test_investigation_is_report_only_and_keeps_fixture_preparation_guarded(self):
        captured = {}

        class FakeAgent:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            def __call__(self, prompt):
                captured["prompt"] = prompt
                return "investigation result"

        with patch.object(agent, "Agent", FakeAgent):
            result = agent.investigate("run-123", "unit-456")

        self.assertEqual(result, "investigation result")
        self.assertNotIn("model", captured)
        self.assertIn("missing-record lookup", captured["system_prompt"])
        normalized_prompt = " ".join(captured["system_prompt"].split())
        self.assertIn("preparation alone is not a completed reproduction", normalized_prompt)
        self.assertIn("pagination behavior and error handling", normalized_prompt)
        self.assertIn("component-test harness", normalized_prompt)
        self.assertIn("error classification/demotion", normalized_prompt)
        self.assertIn("prepare_missing_record", {tool._tool_name for tool in captured["tools"]})
        self.assertNotIn("run_extractor", {tool._tool_name for tool in captured["tools"]})
        self.assertIn("local extractor execution was not run", normalized_prompt)
        self.assertIn("create_devrev_issue", {tool._tool_name for tool in captured["tools"]})
        self.assertIn("issue creation is automatic", captured["system_prompt"])
        self.assertIn("run-123", captured["prompt"])
        self.assertIn("unit-456", captured["prompt"])

    def test_requested_source_constrains_preparation(self):
        captured = {}

        class FakeAgent:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            def __call__(self, prompt):
                return "done"

        with patch.object(agent, "Agent", FakeAgent):
            agent.investigate("run-123", "unit-456", repro_source="jira")

        self.assertIn("caller selected 'jira'", captured["system_prompt"])

    def test_devrev_issue_creation_is_automatic(self):
        captured = {}

        class FakeAgent:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            def __call__(self, prompt):
                return "done"

        with patch.object(agent, "Agent", FakeAgent):
            agent.investigate("run-123", "unit-456", open_issue=True)

        self.assertIn("create_devrev_issue", {tool._tool_name for tool in captured["tools"]})
        self.assertIn("DevRev issue creation is automatic", captured["system_prompt"])

    def test_investigate_auto_selects_missing_ids_from_error_logs(self):
        captured = {}

        class FakeAgent:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            def __call__(self, prompt):
                captured["prompt"] = prompt
                return "investigated"

        details = {"score": 14, "count": 2}
        with patch.object(agent, "Agent", FakeAgent), \
                patch.object(agent, "select_cloudwatch_log_pairs",
                             return_value=[("selected-run", "selected-unit", details)]) as select:
            with self.assertLogs("incident_agent", level="INFO") as output:
                report = agent.investigate(None, None)

        select.assert_called_once_with(None, None, service_name="",
                                       allow_empty=False, prefer_service_mapping=False)
        self.assertIn("run_id='selected-run'", captured["prompt"])
        self.assertIn("Auto-selected run_id=selected-run sync_unit_id=selected-unit", report)
        events = [json.loads(record.message) for record in output.records]
        self.assertEqual(events[0]["event"], "agent.invocation.started")
        selected = next(event for event in events if event["event"] == "logs.pair_selected")
        self.assertEqual(selected["run_id"], "selected-run")
        self.assertEqual(selected["event_count"], 2)
        self.assertEqual({event["invocation_id"] for event in events},
                         {events[0]["invocation_id"]})
        self.assertTrue(any(event["event"] == "agent.investigation.completed"
                            for event in events))

    def test_rejects_unsupported_reproduction_source(self):
        with self.assertRaises(ValueError):
            agent.investigate("run-123", "unit-456", repro_source="unknown")

    def test_completed_s3_identity_skips_report_generation(self):
        with patch.dict("os.environ", {"INCIDENT_DEDUPE_BUCKET": "dedupe-test"}), \
                patch.object(agent.s3_dedupe, "claim", return_value={
                    "enabled": True, "claimed": False, "status": "completed"
                }) as claim, \
                patch.object(agent.s3_dedupe, "previous_investigations", return_value=[]), \
                patch.object(agent, "Agent", side_effect=AssertionError("report must be skipped")):
            result = agent.investigate("run-123", "unit-456", service_name="Salesforce")

        claim.assert_called_once_with("Salesforce", "run-123", "unit-456")
        self.assertIn("No report generated", result)
        self.assertIn("status=completed", result)

    def test_successful_report_completes_s3_claim(self):
        claim_info = {"enabled": True, "claimed": True}

        class FakeAgent:
            def __init__(self, **kwargs):
                pass

            def __call__(self, prompt):
                return "investigation result"

        with patch.dict("os.environ", {"INCIDENT_DEDUPE_BUCKET": "dedupe-test"}), \
                patch.object(agent.s3_dedupe, "claim", return_value=claim_info), \
                patch.object(agent.s3_dedupe, "previous_investigations", return_value=[]), \
                patch.object(agent.s3_dedupe, "complete") as complete, \
                patch.object(agent, "Agent", FakeAgent):
            result = agent.investigate("run-123", "unit-456", service_name="Jira")

        self.assertEqual(result, "investigation result")
        complete.assert_called_once_with(claim_info, "Jira", "run-123", "unit-456",
                                         "investigation result")

    def test_previous_investigations_are_added_as_historical_context(self):
        captured = {}

        class FakeAgent:
            def __init__(self, **kwargs):
                pass

            def __call__(self, prompt):
                captured["prompt"] = prompt
                return "new report"

        previous = [{"run_id": "old-run", "report": "earlier finding"}]
        with patch.dict("os.environ", {"INCIDENT_DEDUPE_BUCKET": "dedupe-test"}), \
                patch.object(agent.s3_dedupe, "claim", return_value={"enabled": True, "claimed": True}), \
                patch.object(agent.s3_dedupe, "previous_investigations", return_value=previous), \
                patch.object(agent.s3_dedupe, "complete"), \
                patch.object(agent, "Agent", FakeAgent):
            agent.investigate("new-run", "unit-456", service_name="Salesforce")

        self.assertIn("earlier finding", captured["prompt"])
        self.assertIn("historical context", captured["prompt"])

    def test_s3_deduplication_requires_service_name(self):
        with patch.dict("os.environ", {"INCIDENT_DEDUPE_BUCKET": "dedupe-test"}, clear=True):
            with self.assertRaisesRegex(ValueError, "service_name is required"):
                agent.investigate("run-123", "unit-456")

    def test_report_failure_is_logged_without_exception_message(self):
        class FailingAgent:
            def __init__(self, **kwargs):
                pass

            def __call__(self, prompt):
                raise RuntimeError("sensitive raw log text")

        with patch.object(agent, "Agent", FailingAgent):
            with self.assertLogs("incident_agent", level="ERROR") as output:
                with self.assertRaisesRegex(RuntimeError, "sensitive raw log text"):
                    agent.investigate("run-123", "unit-456")

        events = [json.loads(record.message) for record in output.records]
        failed = next(event for event in events if event["event"] == "agent.investigation.failed")
        self.assertEqual(failed["error_type"], "RuntimeError")
        self.assertNotIn("sensitive raw log text", output.output)

    def test_missing_ids_process_every_selected_error_pair(self):
        captured = []
        selected = [
            ("run-a", "unit-a", {"score": 20, "count": 2}),
            ("run-b", "unit-b", {"score": 10, "count": 1}),
        ]

        class FakeAgent:
            def __init__(self, **kwargs):
                pass

            def __call__(self, prompt):
                captured.append(prompt)
                return f"report-{len(captured)}"

        with patch.object(agent, "select_cloudwatch_log_pairs", return_value=selected) as select, \
                patch.object(agent, "Agent", FakeAgent):
            result = agent.investigate(None, None)

        select.assert_called_once_with(None, None, service_name="",
                                       allow_empty=False, prefer_service_mapping=False)
        self.assertEqual(len(captured), 2)
        self.assertIn("Processed 2 error run/sync pairs", result)
        self.assertIn("## run-a / unit-a", result)
        self.assertIn("## run-b / unit-b", result)

    def test_batch_continues_after_one_pair_fails(self):
        calls = []
        selected = [
            ("run-a", "unit-a", {"score": 20, "count": 2}),
            ("run-b", "unit-b", {"score": 10, "count": 1}),
        ]

        class FakeAgent:
            def __init__(self, **kwargs):
                pass

            def __call__(self, prompt):
                calls.append(prompt)
                if "run-a" in prompt:
                    raise RuntimeError("model unavailable")
                return "second report"

        with patch.object(agent, "select_cloudwatch_log_pairs", return_value=selected), \
                patch.object(agent, "Agent", FakeAgent):
            result = agent.investigate(None, None)

        self.assertEqual(len(calls), 2)
        self.assertIn("Processing failed (RuntimeError)", result)
        self.assertIn("second report", result)


if __name__ == "__main__":
    unittest.main()
