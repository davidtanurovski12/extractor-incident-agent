import unittest

from devrev_issue import build_issue_payload


class DevRevIssuePayloadTests(unittest.TestCase):
    def test_builds_normalized_issue_title_and_payload(self):
        payload = build_issue_payload(
            " Jira ", " invalid previous IDs ", "Evidence and fix",
            " don:core:part/1 ", [" DEVU-1 ", "", "DEVU-2"],
        )
        self.assertEqual(payload, {
            "type": "issue",
            "title": "Extractor incident: Jira - invalid previous IDs",
            "body": "Evidence and fix",
            "applies_to_part": "don:core:part/1",
            "owned_by": ["DEVU-1", "DEVU-2"],
        })

    def test_rejects_missing_error_name(self):
        with self.assertRaisesRegex(ValueError, "external_system and short_error"):
            build_issue_payload("Jira", " ", "Evidence", "part/1", ["DEVU-1"])

    def test_rejects_missing_owner(self):
        with self.assertRaisesRegex(ValueError, "at least one DevRev issue owner"):
            build_issue_payload("Jira", "HTTP 403", "Evidence", "part/1", [" "])


if __name__ == "__main__":
    unittest.main()
