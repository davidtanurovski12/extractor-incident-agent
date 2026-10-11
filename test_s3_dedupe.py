import json
import unittest
from unittest.mock import patch

from botocore.exceptions import ClientError

import s3_dedupe


class FakeBody:
    def __init__(self, value):
        self.value = value

    def read(self):
        return self.value


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, ContentType, **conditions):
        existing = self.objects.get((Bucket, Key))
        if conditions.get("IfNoneMatch") == "*" and existing:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        if "IfMatch" in conditions and (not existing or existing["ETag"] != conditions["IfMatch"]):
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        etag = f'"{len(self.objects) + (1 if not existing else 0)}"'
        self.objects[(Bucket, Key)] = {"Body": Body, "ETag": etag}
        return {"ETag": etag}

    def get_object(self, Bucket, Key):
        item = self.objects[(Bucket, Key)]
        return {"Body": FakeBody(item["Body"]), "ETag": item["ETag"]}

    def list_objects_v2(self, Bucket, Prefix, MaxKeys):
        return {"Contents": [{"Key": key} for bucket, key in self.objects
                              if bucket == Bucket and key.startswith(Prefix)]}


class S3DedupeTests(unittest.TestCase):
    def test_claim_then_complete_prevents_duplicate_claim(self):
        client = FakeS3()
        with patch.dict("os.environ", {"INCIDENT_DEDUPE_BUCKET": "test-bucket"}, clear=True), \
                patch.object(s3_dedupe, "_client", return_value=client):
            first = s3_dedupe.claim("Salesforce", "run-1", "unit-1")
            self.assertTrue(first["claimed"])
            s3_dedupe.complete(first, "Salesforce", "run-1", "unit-1")
            second = s3_dedupe.claim("Salesforce", "run-1", "unit-1")

        self.assertFalse(second["claimed"])
        self.assertEqual(second["status"], "completed")
        stored = json.loads(next(iter(client.objects.values()))["Body"])
        self.assertEqual(stored["service_name"], "Salesforce")
        self.assertEqual(stored["run_id"], "run-1")
        self.assertEqual(stored["sync_unit_id"], "unit-1")

    def test_different_service_names_have_distinct_keys(self):
        jira_key = s3_dedupe._key("Jira", "run", "unit")
        salesforce_key = s3_dedupe._key("Salesforce", "run", "unit")
        self.assertNotEqual(jira_key, salesforce_key)
        self.assertIn("/airdrop-jira-extractor/jira/", jira_key)
        self.assertIn("/airdrop-jira-extractor/salesforce/", salesforce_key)

    def test_previous_investigations_are_scoped_to_extractor(self):
        client = FakeS3()
        with patch.dict("os.environ", {"INCIDENT_DEDUPE_BUCKET": "test-bucket"}, clear=True), \
                patch.object(s3_dedupe, "_client", return_value=client):
            claim = s3_dedupe.claim("Salesforce", "run-1", "unit-1")
            s3_dedupe.complete(claim, "Salesforce", "run-1", "unit-1", "prior report")
            result = s3_dedupe.previous_investigations("Salesforce")
            other = s3_dedupe.previous_investigations("Jira")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["report"], "prior report")
        self.assertEqual(other, [])


if __name__ == "__main__":
    unittest.main()
