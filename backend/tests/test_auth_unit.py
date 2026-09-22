"""Unit tests for Member 1 authentication and cryptography functions."""

import unittest
import uuid
from datetime import datetime, timedelta, timezone

from app.security.jwt_tokens import (
    create_access_token,
    create_refresh_token,
    decode_token,
    decode_token_subject,
)
from app.security.passwords import (
    hash_password,
    validate_password_strength,
    verify_password,
)


class AuthUnitTests(unittest.TestCase):
    def test_password_hashing_and_verification(self):
        pwd = "SecureP@ssw0rd2026"
        hashed = hash_password(pwd)
        self.assertNotEqual(pwd, hashed)
        self.assertTrue(hashed.startswith("$2b$") or hashed.startswith("$2a$"))
        self.assertTrue(verify_password(pwd, hashed))
        self.assertFalse(verify_password("WrongPassword!", hashed))

    def test_password_strength_validation(self):
        # Valid strong password
        valid, err = validate_password_strength("Str0ng!Pass")
        self.assertTrue(valid)
        self.assertIsNone(err)

        # Too short (< 8 chars)
        valid, err = validate_password_strength("Sh0r!")
        self.assertFalse(valid)
        self.assertIn("at least 8 characters", err)

        # No uppercase
        valid, err = validate_password_strength("lowercase123!")
        self.assertFalse(valid)
        self.assertIn("uppercase", err)

        # No lowercase
        valid, err = validate_password_strength("UPPERCASE123!")
        self.assertFalse(valid)
        self.assertIn("lowercase", err)

        # No digits
        valid, err = validate_password_strength("NoDigitsHere!")
        self.assertFalse(valid)
        self.assertIn("digit", err)

        # No special characters
        valid, err = validate_password_strength("NoSpecialChar123")
        self.assertFalse(valid)
        self.assertIn("special character", err)

    def test_access_token_creation_and_decoding(self):
        user_id = uuid.uuid4()
        token = create_access_token(user_id)
        self.assertIsInstance(token, str)

        payload = decode_token(token, expected_type="access")
        self.assertIsNotNone(payload)
        self.assertEqual(payload.get("sub"), str(user_id))
        self.assertEqual(payload.get("type"), "access")
        self.assertIn("exp", payload)
        self.assertIn("iat", payload)

        # Verify decode_token_subject helper
        decoded_uid = decode_token_subject(token, expected_type="access")
        self.assertEqual(decoded_uid, user_id)

    def test_refresh_token_creation_and_attributes(self):
        user_id = uuid.uuid4()
        token_str, jti, expires_at = create_refresh_token(user_id)

        self.assertIsInstance(token_str, str)
        self.assertIsInstance(jti, str)
        self.assertTrue(expires_at > datetime.now(timezone.utc))

        payload = decode_token(token_str, expected_type="refresh")
        self.assertIsNotNone(payload)
        self.assertEqual(payload.get("sub"), str(user_id))
        self.assertEqual(payload.get("jti"), jti)
        self.assertEqual(payload.get("type"), "refresh")

    def test_token_type_enforcement(self):
        user_id = uuid.uuid4()
        access_token = create_access_token(user_id)
        refresh_token_str, _, _ = create_refresh_token(user_id)

        # Access token must not be accepted when refresh token is expected
        self.assertIsNone(decode_token(access_token, expected_type="refresh"))

        # Refresh token must not be accepted when access token is expected
        self.assertIsNone(decode_token(refresh_token_str, expected_type="access"))
        self.assertIsNone(decode_token_subject(refresh_token_str, expected_type="access"))


if __name__ == "__main__":
    unittest.main()

