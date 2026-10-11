import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from source_repro import create, remove


class Response:
    status = 200

    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return json.dumps(self.value).encode() if self.value else b""


CASES = (
    ("salesforce", {"instance_url": "https://sf.example", "access_token": "TOKEN"},
     {"id": "001TEST"}, {"Name": None}, "/services/data/v60.0/sobjects/Account"),
    ("servicenow", {"instance_url": "https://snow.example", "username": "user", "password": "PASS"},
     {"result": {"sys_id": "deadbeef"}}, {"result": {"short_description": None}},
     "/api/now/table/incident"),
    ("zendesk", {"instance_url": "https://zd.example", "email": "user@example.com", "api_token": "TOKEN"},
     {"ticket": {"id": 42}}, {"ticket": {"subject": None}}, "/api/v2/tickets.json"),
)


class SourceReproTests(unittest.TestCase):
    def test_each_adapter_creates_and_removes_only_its_marked_record(self):
        for source, config, created, detail, create_path in CASES:
            with self.subTest(source=source), tempfile.TemporaryDirectory() as directory:
                manifest = Path(directory) / "case.json"
                requests = []

                def fake_open(request, timeout):
                    requests.append((request.get_method(), request.full_url,
                                     json.loads(request.data) if request.data else None))
                    if request.get_method() == "POST":
                        return Response(created)
                    if request.get_method() == "DELETE":
                        return Response({})
                    if len([r for r in requests if r[0] == "GET"]) > 1:
                        raise urllib.error.HTTPError(request.full_url, 404, "gone", {}, None)
                    value = json.loads(json.dumps(detail))
                    if source == "salesforce":
                        value["Name"] = saved["marker"]
                    elif source == "servicenow":
                        value["result"]["short_description"] = saved["marker"]
                    else:
                        value["ticket"]["subject"] = saved["marker"]
                    return Response(value)

                with patch("urllib.request.urlopen", side_effect=fake_open):
                    saved = create(config, source, manifest)
                    self.assertTrue(requests[0][1].endswith(create_path))
                    self.assertEqual(json.loads(manifest.read_text())["record_id"], saved["record_id"])
                    remove(config, source, manifest)
                    self.assertEqual([r[0] for r in requests], ["POST", "GET", "DELETE", "GET"])
                    self.assertEqual(json.loads(manifest.read_text())["state"], "removed")

    def test_refuses_to_remove_changed_record(self):
        source, config, created, _, _ = CASES[0]
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "case.json"
            with patch("urllib.request.urlopen", return_value=Response(created)):
                create(config, source, manifest)
            with patch("urllib.request.urlopen", return_value=Response({"Name": "someone else's record"})) as call:
                with self.assertRaises(ValueError):
                    remove(config, source, manifest)
                self.assertEqual(call.call_count, 1)  # no DELETE


if __name__ == "__main__":
    unittest.main()
