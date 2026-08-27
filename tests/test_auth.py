import unittest

from app.backend import auth


class AuthTests(unittest.TestCase):
    def test_email_is_normalized(self):
        self.assertEqual(auth.normalize_email("  User@Example.COM "), "user@example.com")

    def test_invalid_email_is_rejected(self):
        with self.assertRaises(ValueError):
            auth.normalize_email("not-an-email")

    def test_password_is_hashed_and_verified(self):
        encoded = auth.hash_password("a-strong-password")
        self.assertNotIn("a-strong-password", encoded)
        self.assertTrue(auth.verify_password("a-strong-password", encoded))
        self.assertFalse(auth.verify_password("wrong-password", encoded))

    def test_session_token_is_opaque_and_only_hash_is_stored(self):
        token = auth.new_session_token()
        token_hash = auth.hash_session_token(token)
        self.assertGreaterEqual(len(token), 48)
        self.assertEqual(len(token_hash), 64)
        self.assertNotEqual(token, token_hash)


if __name__ == "__main__":
    unittest.main()
