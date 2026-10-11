import json
import logging
import unittest
from unittest.mock import patch

from agent_logging import DeferredCloudWatchActivityHandler


def record(event, **fields):
    return logging.makeLogRecord({"msg": json.dumps({"event": event, **fields}), "levelno": logging.INFO})


class DeferredActivityTests(unittest.TestCase):
    def test_empty_source_does_not_create_stream_or_push_logs(self):
        handler = DeferredCloudWatchActivityHandler("/mock/agent-investigator", "us-east-1")
        with patch("agent_logging.CloudWatchActivityHandler") as cloudwatch:
            handler.emit(record("agent.invocation.started"))
            handler.emit(record("logs.error_pairs_selected", pair_count=0))
            handler.emit(record("agent.invocation.failed"))
        cloudwatch.assert_not_called()

    def test_error_pair_flushes_early_events_then_sends_following_events(self):
        handler = DeferredCloudWatchActivityHandler("/mock/agent-investigator", "us-east-1")
        with patch("agent_logging.CloudWatchActivityHandler") as cloudwatch:
            handler.emit(record("agent.invocation.started"))
            handler.emit(record("logs.error_pairs_selected", pair_count=1))
            handler.emit(record("agent.invocation.returned"))
        cloudwatch.assert_called_once_with("/mock/agent-investigator", "us-east-1", None)
        self.assertEqual([json.loads(call.args[0].msg)["event"] for call in cloudwatch.return_value.emit.call_args_list],
                         ["agent.invocation.started", "logs.error_pairs_selected", "agent.invocation.returned"])

    def test_explicit_id_search_with_events_enables_delivery(self):
        handler = DeferredCloudWatchActivityHandler("/mock/agent-investigator", "us-east-1")
        with patch("agent_logging.CloudWatchActivityHandler") as cloudwatch:
            handler.emit(record("logs.search.completed", match_count=0))
            cloudwatch.assert_not_called()
            handler.emit(record("logs.search.completed", match_count=2))
            cloudwatch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
