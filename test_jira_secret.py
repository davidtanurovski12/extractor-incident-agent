import unittest
from unittest.mock import patch

from jira_secret import configure_devrev_token


class JiraSecretTests(unittest.TestCase):
    def test_devrev_token_configurer_uses_the_shared_secret(self):
        with patch("jira_secret.store_devrev_token") as store:
            configure_devrev_token("secrets-client", "incident-repro/jira", "  hidden-token  ")
        store.assert_called_once_with("secrets-client", "incident-repro/jira", "hidden-token")

    def test_devrev_token_configurer_rejects_blank_input(self):
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            configure_devrev_token("secrets-client", "incident-repro/jira", " ")

if __name__ == "__main__":
    unittest.main()
