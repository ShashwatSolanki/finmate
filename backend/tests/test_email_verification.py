"""Tests for email verification and forgot password reset functionality."""

from datetime import datetime, timedelta, timezone
import unittest
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.db.base import Base
from app.db.models import (
    EmailVerificationToken,
    PasswordResetToken,
    RefreshToken,
    User,
)
from app.main import app

# In-memory SQLite database for isolated testing
TEST_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


class EmailVerificationAndResetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)
        app.dependency_overrides[get_db] = override_get_db
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=engine)

    def test_01_registration_generates_token_and_preview(self):
        email = "verify_test@example.com"
        res = self.client.post(
            "/api/auth/register",
            json={
                "email": email,
                "password": "SecureP@ssw0rd123",
                "display_name": "Verification User",
            },
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("access_token", data)
        self.assertFalse(data["is_verified"])
        self.assertTrue(data["requires_verification"])
        self.assertIsNotNone(data["verification_code_preview"])

        # Check DB
        db = TestingSessionLocal()
        user = db.query(User).filter(User.email == email).first()
        self.assertIsNotNone(user)
        self.assertFalse(user.is_verified)

        tokens = db.query(EmailVerificationToken).filter(EmailVerificationToken.user_id == user.id).all()
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0].code, data["verification_code_preview"])
        self.assertFalse(tokens[0].used)
        db.close()

    def test_02_verify_email_invalid_code(self):
        email = "verify_test@example.com"
        res = self.client.post(
            "/api/auth/verify-email",
            json={"email": email, "code": "999999"},
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("Invalid verification code", res.json()["detail"])

    def test_03_verify_email_success(self):
        email = "verify_test@example.com"
        db = TestingSessionLocal()
        user = db.query(User).filter(User.email == email).first()
        token = (
            db.query(EmailVerificationToken)
            .filter(EmailVerificationToken.user_id == user.id, EmailVerificationToken.used.is_(False))
            .first()
        )
        code = token.code
        db.close()

        res = self.client.post(
            "/api/auth/verify-email",
            json={"email": email, "code": code},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["is_verified"])

        # Check DB updated
        db = TestingSessionLocal()
        user = db.query(User).filter(User.email == email).first()
        self.assertTrue(user.is_verified)
        token_after = db.query(EmailVerificationToken).filter(EmailVerificationToken.code == code).first()
        self.assertTrue(token_after.used)
        db.close()

    def test_04_verify_email_expired_code(self):
        email = "expired_test@example.com"
        self.client.post(
            "/api/auth/register",
            json={"email": email, "password": "SecureP@ssw0rd123"},
        )
        db = TestingSessionLocal()
        user = db.query(User).filter(User.email == email).first()
        expired_token = (
            db.query(EmailVerificationToken)
            .filter(EmailVerificationToken.user_id == user.id, EmailVerificationToken.used.is_(False))
            .first()
        )
        expired_token.expires_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        db.commit()
        code = expired_token.code
        db.close()

        res = self.client.post(
            "/api/auth/verify-email",
            json={"email": email, "code": code},
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("expired", res.json()["detail"])

    def test_05_resend_verification_code(self):
        email = "expired_test@example.com"
        res = self.client.post(
            "/api/auth/resend-verification",
            json={"email": email},
        )
        self.assertEqual(res.status_code, 200)

        # Check that previous unused was invalidated and a new active one was added
        db = TestingSessionLocal()
        user = db.query(User).filter(User.email == email).first()
        active_tokens = (
            db.query(EmailVerificationToken)
            .filter(EmailVerificationToken.user_id == user.id, EmailVerificationToken.used.is_(False))
            .all()
        )
        self.assertEqual(len(active_tokens), 1)
        new_code = active_tokens[0].code
        db.close()

        # Now verify with new code
        res_verify = self.client.post(
            "/api/auth/verify-email",
            json={"email": email, "code": new_code},
        )
        self.assertEqual(res_verify.status_code, 200)
        self.assertTrue(res_verify.json()["is_verified"])

    def test_06_forgot_password_and_reset(self):
        email = "reset_test@example.com"
        reg_res = self.client.post(
            "/api/auth/register",
            json={"email": email, "password": "OriginalP@ssw0rd123"},
        )
        self.assertEqual(reg_res.status_code, 200)

        # Request reset
        res = self.client.post(
            "/api/auth/forgot-password",
            json={"email": email},
        )
        self.assertEqual(res.status_code, 200)

        # Inspect token
        db = TestingSessionLocal()
        user = db.query(User).filter(User.email == email).first()
        reset_token = (
            db.query(PasswordResetToken)
            .filter(PasswordResetToken.user_id == user.id, PasswordResetToken.used.is_(False))
            .first()
        )
        self.assertIsNotNone(reset_token)
        code = reset_token.code
        db.close()

        # Attempt reset with weak password -> 422
        bad_pwd_res = self.client.post(
            "/api/auth/reset-password",
            json={"email": email, "code": code, "new_password": "short"},
        )
        self.assertEqual(bad_pwd_res.status_code, 422)

        # Attempt reset with invalid code -> 400
        bad_code_res = self.client.post(
            "/api/auth/reset-password",
            json={"email": email, "code": "000000", "new_password": "FreshNewP@ssw0rd789"},
        )
        self.assertEqual(bad_code_res.status_code, 400)

        # Success reset
        reset_res = self.client.post(
            "/api/auth/reset-password",
            json={"email": email, "code": code, "new_password": "FreshNewP@ssw0rd789"},
        )
        self.assertEqual(reset_res.status_code, 200)

        # Old password should fail login
        fail_login = self.client.post(
            "/api/auth/login",
            json={"email": email, "password": "OriginalP@ssw0rd123"},
        )
        self.assertEqual(fail_login.status_code, 401)

        # New password should succeed login
        success_login = self.client.post(
            "/api/auth/login",
            json={"email": email, "password": "FreshNewP@ssw0rd789"},
        )
        self.assertEqual(success_login.status_code, 200)
        self.assertTrue(success_login.json()["is_verified"])

    def test_07_google_auth_sets_verified(self):
        res = self.client.post(
            "/api/auth/google",
            json={"credential": "mock-google-token:guser_verified@example.com:sub999888:Verified Google User"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["is_verified"])

        db = TestingSessionLocal()
        user = db.query(User).filter(User.email == "guser_verified@example.com").first()
        self.assertIsNotNone(user)
        self.assertTrue(user.is_verified)
        db.close()


if __name__ == "__main__":
    unittest.main()
