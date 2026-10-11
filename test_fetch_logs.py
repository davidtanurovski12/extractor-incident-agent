import json
import tempfile
import unittest
from pathlib import Path

from fetch_logs import write_jsonl


class FetchLogsTests(unittest.TestCase):
    def test_writes_events_as_jsonl(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "events.jsonl"
            write_jsonl([{"run_id": "run-1"}, {"run_id": "run-1", "message": "failure"}], path)

            lines = path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0]), {"run_id": "run-1"})
        self.assertEqual(json.loads(lines[1])["message"], "failure")


if __name__ == "__main__":
    unittest.main()
