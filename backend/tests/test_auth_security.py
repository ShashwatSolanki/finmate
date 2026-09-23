"""Security and attack resilience tests for Member 1 auth system."""

import unittest
import uuid
from datetime import datetime, timedelta, timezone
from jose import jwt

from app.config import settings
from app.security.jwt_tokens import (
    create_access_token,
    create_refresh_token,
    decode_token,
    decode_token_subject,
)
from app.security.rate_limiter import SimpleRateLimiter


class AuthSecurityTests(unittest.TestCase):
    def test_signature_tampering_rejection(self):
        user_id = uuid.uuid4()
        token = create_access_token(user_id)
        parts = token.split(".")
        self.assertEqual(len(parts), 3)

        # Tamper signature
        tampered_sig_token = f"{parts[0]}.{parts[1]}.tampered_signature_xyz"
        self.assertIsNone(decode_token(tampered_sig_token))
        self.assertIsNone(decode_token_subject(tampered_sig_token))

        # Sign valid payload with wrong secret
        now = datetime.now(timezone.utc)
        fake_token = jwt.encode(
            {"sub": str(user_id), "exp": now + timedelta(hours=1), "type": "access"},
            "attacker-secret-key-123",
            algorithm="HS256",
        )
        self.assertIsNone(decode_token(fake_token))

    def test_expired_token_rejection(self):
        user_id = uuid.uuid4()
        past = datetime.now(timezone.utc) - timedelta(minutes=30)
        expired_token = jwt.encode(
            {"sub": str(user_id), "exp": past, "type": "access"},
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
        self.assertIsNone(decode_token(expired_token))
        self.assertIsNone(decode_token_subject(expired_token))

    def test_algorithm_confusion_rejection(self):
        user_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        try:
            none_token = jwt.encode(
                {"sub": str(user_id), "exp": now + timedelta(hours=1), "type": "access"},
                "",
                algorithm="none",
            )
            self.assertIsNone(decode_token(none_token))
        except Exception:
            pass

    def test_brute_force_rate_limiter(self):
        limiter = SimpleRateLimiter(max_attempts=3, window_seconds=10)
        key = "attacker_ip:target@finmate.com"

        self.assertFalse(limiter.is_rate_limited(key))
        limiter.record_attempt(key)
        self.assertFalse(limiter.is_rate_limited(key))
        limiter.record_attempt(key)
        self.assertFalse(limiter.is_rate_limited(key))
        limiter.record_attempt(key)

        # 3 attempts reached; 4th attempt must be blocked
        self.assertTrue(limiter.is_rate_limited(key))

        # Reset on successful login
        limiter.reset(key)
        self.assertFalse(limiter.is_rate_limited(key))

    def test_token_type_confusion_attack(self):
        user_id = uuid.uuid4()
        refresh_str, _, _ = create_refresh_token(user_id)
        self.assertIsNone(decode_token_subject(refresh_str, expected_type="access"))

        access_str = create_access_token(user_id)
        self.assertIsNone(decode_token(access_str, expected_type="refresh"))


if __name__ == "__main__":
    unittest.main()

