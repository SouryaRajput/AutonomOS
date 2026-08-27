import os
import unittest

from core.inference.secrets import EnvSecretStore, redact_secret_text


class TestSecretStore(unittest.TestCase):

    def test_env_secret_store_resolution_and_overrides(self):
        store = EnvSecretStore()
        store.set_secret("OPENROUTER_API_KEY", "sk-or-v1-secretkey99999")

        # Resolves direct key
        val = store.get_secret("OPENROUTER_API_KEY")
        self.assertEqual(val, "sk-or-v1-secretkey99999")

        # Resolves prefixed 'env:...' key
        val_prefixed = store.get_secret("env:OPENROUTER_API_KEY")
        self.assertEqual(val_prefixed, "sk-or-v1-secretkey99999")

        # Non-existent key returns None
        self.assertIsNone(store.get_secret("NONEXISTENT_KEY"))

    def test_redact_secret_text_masks_keys(self):
        secrets = ["sk-or-v1-secretkey99999", "secret-password-1234"]

        text = "Connecting with Authorization: Bearer sk-or-v1-secretkey99999 to endpoint."
        redacted = redact_secret_text(text, secrets)

        self.assertNotIn("sk-or-v1-secretkey99999", redacted)
        self.assertIn("[REDACTED_API_KEY]", redacted)


if __name__ == "__main__":
    unittest.main()
