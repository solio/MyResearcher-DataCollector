import json
import tempfile
import time
from pathlib import Path
import unittest

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from apps.waygate.hub.push import AUDIENCE, TokenProvider, service_token


class PushCredentialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Ephemeral test key, never saved in the repository or used with a provider.
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.account = {"project_id": "test-project", "sub_account": "test-service-account", "key_id": "test-key",
                       "private_key": cls.key.private_bytes(serialization.Encoding.PEM,
                       serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()}

    def test_actual_pss_signature_and_official_claims(self):
        now = int(time.time())
        token = service_token(self.account, "test-project", now)
        claims = jwt.decode(token, self.key.public_key(), algorithms=["PS256"], audience=AUDIENCE)
        self.assertEqual(claims["iss"], "test-service-account")
        self.assertEqual(claims["exp"] - claims["iat"], 3600)
        self.assertEqual(jwt.get_unverified_header(token)["kid"], "test-key")

    def test_project_mismatch_cannot_sign(self):
        with self.assertRaisesRegex(ValueError, "不一致"):
            service_token(self.account, "other-project")

    def test_token_cache_refresh_and_key_file_rotation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "account.json"
            path.write_text(json.dumps(self.account))
            config = {"project_id": "test-project", "service_account_file": str(path)}
            provider = TokenProvider()
            first = provider.get(config)
            self.assertEqual(first, provider.get(config))
            provider.until = 0
            self.assertNotEqual(first, provider.get(config))  # RSA-PSS salt is randomized
            changed = dict(self.account, key_id="rotated-key")
            path.write_text(json.dumps(changed))
            self.assertEqual(jwt.get_unverified_header(provider.get(config))["kid"], "rotated-key")


if __name__ == "__main__":
    unittest.main()
