import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from investigation import (read_repo, repo_root, search_repo, search_sync_logs,
                           select_cloudwatch_log_pair, select_cloudwatch_log_pairs,
                           select_log_pair, select_log_pairs)


class InvestigationTests(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.TemporaryDirectory()
        self.addCleanup(self.repo.cleanup)
        self.environment = patch.dict(os.environ, {"EXTRACTOR_REPO_PATH": self.repo.name})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        (Path(self.repo.name) / "internal").mkdir()
        (Path(self.repo.name) / "internal" / "extractor.go").write_text(
            "package internal\ntype salesforceExtractor struct {}\n// sample\n"
        )

    def test_logs_are_scoped_sorted_and_cited(self):
        records = [
            {"run_id": "run", "sync_unit_id": "unit", "timestamp": "2026-01-02T00:00:00Z", "message": "failure"},
            {"run_id": "run", "sync_unit_id": "other", "timestamp": "2026-01-01T00:00:00Z"},
            {"run_id": "run", "sync_unit_id": "unit", "timestamp": "2026-01-01T00:00:00Z", "message": "request"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            path.write_text("\n".join(json.dumps(record) for record in records))
            with patch.dict(os.environ, {"INCIDENT_LOG_FILE": str(path)}):
                result = search_sync_logs("run", "unit", limit=1)
                self.assertEqual(result["total_matches"], 2)
                self.assertTrue(result["truncated"])
                self.assertEqual(result["events"][0]["source_line"], 3)
                self.assertEqual(result["events"][0]["message"], "request")

    def test_grafana_json_export_is_scoped_and_normalized(self):
        entries = [
            {"date": "2026-10-10T07:00:00Z", "line": '{"level":"info","msg":"started"}',
             "fields": {"run_id": "run-a", "sync_unit_id": "unit-a", "level": "info"}},
            {"date": "2026-10-10T07:01:00Z", "line": '{"level":"info","msg":"Jira project not found: error: 405"}',
             "fields": {"run_id": "run-b", "sync_unit_id": "unit-b", "level": "info"}},
            {"date": "2026-10-10T07:02:00Z", "line": '{"level":"info","msg":"other run"}',
             "fields": {"run_id": "run-c", "sync_unit_id": "unit-c", "level": "info"}},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "grafana.json"
            path.write_text(json.dumps(entries))
            with patch.dict(os.environ, {"INCIDENT_LOG_FILE": str(path)}):
                result = search_sync_logs("run-b", "unit-b")
                selected_run, selected_unit, details = select_log_pair(str(path))

        self.assertEqual(result["total_matches"], 1)
        self.assertEqual(result["events"][0]["message"], "Jira project not found: error: 405")
        self.assertEqual(result["events"][0]["source_line"], 2)
        self.assertEqual((selected_run, selected_unit), ("run-b", "unit-b"))
        self.assertGreater(details["score"], 0)

    def test_file_selection_returns_all_distinct_error_pairs_not_info_pairs(self):
        records = [
            {"run_id": "run-info", "sync_unit_id": "unit-info", "level": "info", "message": "started"},
            {"run_id": "run-a", "sync_unit_id": "unit-a", "level": "error", "message": "failed"},
            {"run_id": "run-b", "sync_unit_id": "unit-b", "level": "error", "message": "exception"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            path.write_text("\n".join(json.dumps(record) for record in records))
            pairs = select_log_pairs(str(path))

        self.assertEqual({(run, unit) for run, unit, _ in pairs},
                         {("run-a", "unit-a"), ("run-b", "unit-b")})
        self.assertGreater(pairs[0][2]["score"], 0)

    def test_repo_read_and_search(self):
        result = search_repo("type salesforceExtractor struct", limit=5)
        self.assertTrue(any(hit["path"] == "internal/extractor.go" for hit in result["matches"]))
        source = read_repo("internal/extractor.go", 1, 3)
        self.assertEqual(len(source["lines"]), 3)

    def test_search_and_read_optional_mock_api_repo(self):
        with tempfile.TemporaryDirectory() as mock_repo, patch.dict(os.environ, {"MOCK_API_REPO_PATH": mock_repo}):
            (Path(mock_repo) / "handlers.go").write_text("package mock\nfunc HandlePageError() {}\n")
            result = search_repo("HandlePageError", repo="all")
            self.assertEqual(result["matches"][0]["repo"], "mock_api")
            source = read_repo("handlers.go", 1, 2, repo="mock_api")
        self.assertEqual(source["repo"], "mock_api")
        self.assertIn("2: func HandlePageError() {}", source["lines"])

    def test_service_name_routes_to_matching_extractor_checkout(self):
        with tempfile.TemporaryDirectory() as jira, tempfile.TemporaryDirectory() as salesforce:
            (Path(jira) / "jira.go").write_text("package jira\nfunc JiraOnly() {}\n")
            (Path(salesforce) / "salesforce.go").write_text(
                "package salesforce\nfunc SalesforceOnly() {}\n")
            with patch.dict(os.environ, {
                "JIRA_EXTRACTOR_REPO_PATH": jira,
                "SALESFORCE_EXTRACTOR_REPO_PATH": salesforce,
                "EXTRACTOR_REPO_PATH": jira,
            }, clear=False):
                self.assertEqual(repo_root(service_name="Jira"), Path(jira).resolve())
                self.assertEqual(repo_root(service_name="Salesforce"), Path(salesforce).resolve())
                found = search_repo("SalesforceOnly", service_name="Salesforce")
                source = read_repo("salesforce.go", service_name="Salesforce")

        self.assertEqual(found["matches"][0]["path"], "salesforce.go")
        self.assertIn("SalesforceOnly", "\n".join(source["lines"]))

    def test_service_repo_map_supports_arbitrary_service_names(self):
        with tempfile.TemporaryDirectory() as repo, patch.dict(os.environ, {
            "EXTRACTOR_REPO_PATHS": json.dumps({"Custom CRM": repo}),
        }, clear=True):
            self.assertEqual(repo_root(service_name="custom crm"), Path(repo).resolve())

    def test_non_jira_service_does_not_fall_back_to_generic_jira_checkout(self):
        with patch.dict(os.environ, {"EXTRACTOR_REPO_PATH": self.repo.name}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "No extractor checkout configured for service"):
                repo_root(service_name="Salesforce")

    def test_runtime_uses_fetched_metis_when_no_checkout_is_configured(self):
        with tempfile.TemporaryDirectory() as snapshot, patch.dict(os.environ, {
            "INCIDENT_RUNTIME_SOURCE": "airsync-metis",
        }, clear=True), patch("runtime_source.source_root", return_value=Path(snapshot)) as fetch:
            (Path(snapshot) / "source.go").write_text("package metis\n")
            self.assertEqual(repo_root(service_name="Zendesk"), Path(snapshot))
            self.assertEqual(read_repo("source.go", service_name="Zendesk")["lines"],
                             ["1: package metis"])
            fetch.assert_called_with()

    def test_repo_traversal_rejected(self):
        with self.assertRaises(ValueError):
            read_repo("../../.aws/credentials")

    def test_requires_configured_repo(self):
        with patch.dict(os.environ, {"EXTRACTOR_REPO_PATH": ""}):
            with self.assertRaises(RuntimeError):
                search_repo("salesforce")

    def test_cloudwatch_search_uses_configured_connection(self):
        client = Mock()
        client.start_query.return_value = {"queryId": "query-1"}
        client.get_query_results.return_value = {"status": "Complete", "results": []}
        with patch("investigation.cloudwatch_connection",
                   return_value=(client, "/aws/lambda/example", 24)) as connection:
            result = search_sync_logs("run-1", "unit-1")

        connection.assert_called_once_with(None, prefer_service_mapping=False)
        client.start_query.assert_called_once()
        self.assertEqual(result["query_id"], "query-1")

    def test_cloudwatch_auto_selects_pair_with_strongest_error_evidence(self):
        def row(timestamp, event):
            return [
                {"field": "@timestamp", "value": timestamp},
                {"field": "@message", "value": json.dumps(event)},
                {"field": "@logStream", "value": "stream"},
                {"field": "@ptr", "value": "pointer"},
            ]

        client = Mock()
        client.start_query.return_value = {"queryId": "query-errors"}
        client.get_query_results.return_value = {
            "status": "Complete",
            "results": [
                row("2026-10-10T10:02:00Z", {
                    "run_id": "run-info", "sync_unit_id": "unit-info",
                    "level": "info", "msg": "started",
                }),
                row("2026-10-10T10:01:00Z", {
                    "run_id": "run-error", "sync_unit_id": "unit-error",
                    "level": "error", "msg": "HTTP 404: issue not found",
                }),
            ],
        }
        with patch("investigation.cloudwatch_connection",
                   return_value=(client, "/aws/lambda/example", 24)):
            run, unit, details = select_cloudwatch_log_pair()

        self.assertEqual((run, unit), ("run-error", "unit-error"))
        self.assertGreater(details["score"], 0)
        self.assertEqual(details["query_id"], "query-errors")

    def test_cloudwatch_auto_selection_honors_partial_identifier(self):
        client = Mock()
        client.start_query.return_value = {"queryId": "query-partial"}
        event = {"run_id": "wanted-run", "sync_unit_id": "unit", "level": "error", "msg": "failed"}
        client.get_query_results.return_value = {"status": "Complete", "results": [[
            {"field": "@message", "value": json.dumps(event)},
        ]]}
        with patch("investigation.cloudwatch_connection",
                   return_value=(client, "/aws/lambda/example", 24)):
            run, unit, _ = select_cloudwatch_log_pair(run_id="wanted-run")
        self.assertEqual((run, unit), ("wanted-run", "unit"))

    def test_cloudwatch_selection_returns_all_error_pairs(self):
        def row(event):
            return [{"field": "@message", "value": json.dumps(event)},
                    {"field": "@timestamp", "value": "2026-10-10T10:00:00Z"}]

        client = Mock()
        client.start_query.return_value = {"queryId": "query-all"}
        client.get_query_results.return_value = {"status": "Complete", "results": [
            row({"run_id": "run-a", "sync_unit_id": "unit-a", "level": "error", "msg": "failed"}),
            row({"run_id": "run-b", "sync_unit_id": "unit-b", "level": "error", "msg": "HTTP 500"}),
            row({"run_id": "run-c", "sync_unit_id": "unit-c", "level": "info", "msg": "started"}),
        ]}
        with patch("investigation.cloudwatch_connection",
                   return_value=(client, "/aws/lambda/example", 24)):
            pairs = select_cloudwatch_log_pairs()

        self.assertEqual({(run, unit) for run, unit, _ in pairs},
                         {("run-a", "unit-a"), ("run-b", "unit-b")})
        self.assertTrue(all(details["query_id"] == "query-all" for _, _, details in pairs))


if __name__ == "__main__":
    unittest.main()
