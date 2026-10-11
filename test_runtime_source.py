import io
import json
import os
import tarfile
import unittest
from unittest.mock import Mock, patch

import runtime_source


class RuntimeSourceTests(unittest.TestCase):
    def tearDown(self):
        if runtime_source.source_root.cache_info().currsize:
            import shutil
            shutil.rmtree(runtime_source.source_root(), ignore_errors=True)
            runtime_source.source_root.cache_clear()

    def test_fetches_snapshot_with_secret_token_without_exposing_it_in_url(self):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w:gz") as archive:
            content = b"package metis\n"
            info = tarfile.TarInfo("devrev-airsync-metis-abc/source.go")
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        response = Mock()
        response.read.return_value = stream.getvalue()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=None)
        secrets = Mock()
        secrets.get_secret_value.return_value = {"SecretString": json.dumps({"github_token": "private-token"})}
        with patch.dict(os.environ, {
            "INCIDENT_SOURCE_REF": "abc123",
        }, clear=True), patch("runtime_source.boto3.client", return_value=secrets) as secret_client, \
                patch("runtime_source.urlopen", return_value=response) as fetch:
            root = runtime_source.source_root()
        self.assertEqual((root / "source.go").read_text(), "package metis\n")
        request = fetch.call_args.args[0]
        self.assertIn("devrev/airsync-metis/tarball/abc123", request.full_url)
        self.assertNotIn("private-token", request.full_url)
        self.assertEqual(request.get_header("Authorization"), "Bearer private-token")
        secret_client.assert_called_once_with("secretsmanager", region_name="us-west-2")
        secrets.get_secret_value.assert_called_once_with(SecretId="incident-repro/jira")


if __name__ == "__main__":
    unittest.main()
