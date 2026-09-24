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
        from app.config import settings
        cls._orig_mock = settings.email_mock_mode
        settings.email_mock_mode = True
        Base.metadata.create_all(bind=engine)
        app.dependency_overrides[get_db] = override_get_db
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        from app.config import settings
        settings.email_mock_mode = cls._orig_mock
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

    def test_08_email_verification_preserves_chat_history_and_memory_story(self):
        """Verify that verifying email does not corrupt or wipe chat history, messages, or user memory story."""
        email = "history_verify@example.com"
        reg_res = self.client.post(
            "/api/auth/register",
            json={"email": email, "password": "SecureP@ssw0rd123"},
        )
        self.assertEqual(reg_res.status_code, 200)
        token_data = reg_res.json()
        auth_header = {"Authorization": f"Bearer {token_data['access_token']}"}

        # Create chat session, message, memory chunk (story), and transaction before verification
        db = TestingSessionLocal()
        user = db.query(User).filter(User.email == email).first()
        from app.db.models import ChatMessage, ChatSession, MemoryChunk, Transaction
        from decimal import Decimal
        from datetime import date

        session = ChatSession(user_id=user.id, title="Financial Planning Discussion")
        db.add(session)
        db.flush()
        session_id = session.id

        db.add(ChatMessage(session_id=session.id, role="user", content="How much can I save monthly?"))
        db.add(ChatMessage(session_id=session.id, role="assistant", content="Based on your 50k salary, you can save 15k."))
        db.add(MemoryChunk(user_id=user.id, content="User monthly salary is 50000 INR with moderate risk tolerance", source="onboarding"))
        db.add(Transaction(user_id=user.id, amount=Decimal("250.00"), currency="USD", occurred_on=date.today(), description="Groceries"))
        db.commit()

        # Retrieve verification code
        v_token = (
            db.query(EmailVerificationToken)
            .filter(EmailVerificationToken.user_id == user.id, EmailVerificationToken.used.is_(False))
            .first()
        )
        code = v_token.code
        db.close()

        # Complete email verification
        ver_res = self.client.post(
            "/api/auth/verify-email",
            json={"email": email, "code": code},
        )
        self.assertEqual(ver_res.status_code, 200)
        new_token_data = ver_res.json()
        new_auth_header = {"Authorization": f"Bearer {new_token_data['access_token']}"}

        # Verify chat history and session information still accessible via API
        conv_res = self.client.get("/api/conversations", headers=new_auth_header)
        self.assertEqual(conv_res.status_code, 200)
        conversations = conv_res.json()
        self.assertEqual(len(conversations), 1)
        self.assertEqual(conversations[0]["title"], "Financial Planning Discussion")

        conv_detail = self.client.get(f"/api/conversations/{session_id}/messages", headers=new_auth_header)
        self.assertEqual(conv_detail.status_code, 200)
        messages = conv_detail.json()
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["content"], "How much can I save monthly?")

        # Check DB directly to ensure memory story and transactions remain intact
        db = TestingSessionLocal()
        user = db.query(User).filter(User.email == email).first()
        self.assertTrue(user.is_verified)
        self.assertEqual(len(user.chat_sessions), 1)
        self.assertEqual(len(user.memory_chunks), 1)
        self.assertIn("moderate risk tolerance", user.memory_chunks[0].content)
        self.assertEqual(len(user.transactions), 1)
        db.close()

    def test_09_password_reset_preserves_chat_history_and_session_story(self):
        """Verify that resetting password preserves all chat sessions, messages, and user story while updating security."""
        email = "history_reset@example.com"
        reg_res = self.client.post(
            "/api/auth/register",
            json={"email": email, "password": "OriginalP@ssw0rd123"},
        )
        self.assertEqual(reg_res.status_code, 200)
        token_data = reg_res.json()

        # Populate user story, chat sessions, and financial data
        db = TestingSessionLocal()
        user = db.query(User).filter(User.email == email).first()
        from app.db.models import ChatMessage, ChatSession, MemoryChunk, Transaction
        from decimal import Decimal
        from datetime import date

        session = ChatSession(user_id=user.id, title="Investment Portfolio Review")
        db.add(session)
        db.flush()
        session_id = session.id
        db.add(ChatMessage(session_id=session.id, role="user", content="Review my tech stock allocation"))
        db.add(ChatMessage(session_id=session.id, role="assistant", content="Tech represents 40% of your portfolio."))
        db.add(MemoryChunk(user_id=user.id, content="User plans to buy a house in 5 years", source="chat"))
        db.add(Transaction(user_id=user.id, amount=Decimal("1200.00"), currency="USD", occurred_on=date.today(), description="Rent"))
        db.commit()
        db.close()

        # Trigger forgot password
        forgot_res = self.client.post("/api/auth/forgot-password", json={"email": email})
        self.assertEqual(forgot_res.status_code, 200)

        db = TestingSessionLocal()
        user = db.query(User).filter(User.email == email).first()
        reset_token = (
            db.query(PasswordResetToken)
            .filter(PasswordResetToken.user_id == user.id, PasswordResetToken.used.is_(False))
            .first()
        )
        code = reset_token.code
        db.close()

        # Perform password reset
        reset_res = self.client.post(
            "/api/auth/reset-password",
            json={"email": email, "code": code, "new_password": "CompletelyNewP@ssw0rd888"},
        )
        self.assertEqual(reset_res.status_code, 200)

        # Login with new password
        login_res = self.client.post(
            "/api/auth/login",
            json={"email": email, "password": "CompletelyNewP@ssw0rd888"},
        )
        self.assertEqual(login_res.status_code, 200)
        new_auth_header = {"Authorization": f"Bearer {login_res.json()['access_token']}"}

        # Verify chat history is completely preserved and accessible
        conv_res = self.client.get("/api/conversations", headers=new_auth_header)
        self.assertEqual(conv_res.status_code, 200)
        conversations = conv_res.json()
        self.assertEqual(len(conversations), 1)
        self.assertEqual(conversations[0]["title"], "Investment Portfolio Review")

        # Verify messages in conversation
        conv_detail = self.client.get(f"/api/conversations/{session_id}/messages", headers=new_auth_header)
        self.assertEqual(conv_detail.status_code, 200)
        messages = conv_detail.json()
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["content"], "Review my tech stock allocation")

        # Verify memory story and transactions in DB
        db = TestingSessionLocal()
        user = db.query(User).filter(User.email == email).first()
        self.assertEqual(len(user.chat_sessions), 1)
        self.assertEqual(len(user.memory_chunks), 1)
        self.assertIn("buy a house in 5 years", user.memory_chunks[0].content)
        self.assertEqual(len(user.transactions), 1)
        self.assertEqual(user.transactions[0].description, "Rent")
        db.close()

    def test_10_password_reset_revokes_old_sessions_cleanly(self):
        """Ensure password reset revokes old active refresh tokens without corrupting user account state."""
        email = "session_security@example.com"
        reg_res = self.client.post(
            "/api/auth/register",
            json={"email": email, "password": "OriginalP@ssw0rd123"},
        )
        old_rf_token = reg_res.json()["refresh_token"]

        # Request reset and change password
        self.client.post("/api/auth/forgot-password", json={"email": email})
        db = TestingSessionLocal()
        user = db.query(User).filter(User.email == email).first()
        token = db.query(PasswordResetToken).filter(PasswordResetToken.user_id == user.id).first()
        code = token.code
        db.close()

        self.client.post(
            "/api/auth/reset-password",
            json={"email": email, "code": code, "new_password": "NewSecureP@ssw0rd456"},
        )

        # Attempt to refresh token using old session refresh token -> should be rejected (revoked)
        rf_res = self.client.post(
            "/api/auth/refresh",
            json={"refresh_token": old_rf_token},
        )
        self.assertEqual(rf_res.status_code, 401)
        self.assertIn("revoked", rf_res.json()["detail"].lower())

    def test_11_google_linking_preserves_existing_user_history_and_story(self):
        """Ensure linking Google account preserves existing chat history, story, and sessions."""
        email = "existing_google_user@example.com"
        reg_res = self.client.post(
            "/api/auth/register",
            json={"email": email, "password": "Password123!#$"},
        )
        self.assertEqual(reg_res.status_code, 200)

        # Add chat session and memory
        db = TestingSessionLocal()
        user = db.query(User).filter(User.email == email).first()
        from app.db.models import ChatMessage, ChatSession, MemoryChunk
        session = ChatSession(user_id=user.id, title="Pre-Google Linked Chat")
        db.add(session)
        db.flush()
        db.add(ChatMessage(session_id=session.id, role="user", content="Hello before Google link"))
        db.add(MemoryChunk(user_id=user.id, content="User likes index funds", source="onboarding"))
        db.commit()
        user_id = user.id
        db.close()

        # Log in via Google with same email
        g_res = self.client.post(
            "/api/auth/google",
            json={"credential": f"mock-google-token:{email}:google-sub-777:Existing Google Linked"},
        )
        self.assertEqual(g_res.status_code, 200)
        g_token_data = g_res.json()
        self.assertEqual(g_token_data["user_id"], str(user_id))

        # Check conversations with new token
        headers = {"Authorization": f"Bearer {g_token_data['access_token']}"}
        conv_res = self.client.get("/api/conversations", headers=headers)
        self.assertEqual(conv_res.status_code, 200)
        conversations = conv_res.json()
        self.assertEqual(len(conversations), 1)
        self.assertEqual(conversations[0]["title"], "Pre-Google Linked Chat")

    def test_12_multi_user_google_isolation_and_profile_settings(self):
        """Verify strict isolation between distinct Google accounts for chat sessions and profile settings."""
        # 1. Login User Alice
        alice_res = self.client.post(
            "/api/auth/google",
            json={"credential": "mock-google-token:alice_iso@gmail.com:google-sub-alice_iso:Alice"},
        )
        self.assertEqual(alice_res.status_code, 200)
        alice_token = alice_res.json()["access_token"]
        alice_headers = {"Authorization": f"Bearer {alice_token}"}

        # 2. Login User Bob
        bob_res = self.client.post(
            "/api/auth/google",
            json={"credential": "mock-google-token:bob_iso@gmail.com:google-sub-bob_iso:Bob"},
        )
        self.assertEqual(bob_res.status_code, 200)
        bob_token = bob_res.json()["access_token"]
        bob_headers = {"Authorization": f"Bearer {bob_token}"}

        self.assertNotEqual(alice_res.json()["user_id"], bob_res.json()["user_id"])

        # 3. Alice creates conversation and saves profile
        conv_alice = self.client.post(
            "/api/conversations",
            headers=alice_headers,
            json={"title": "Alice's Private Budget Plan"},
        )
        self.assertEqual(conv_alice.status_code, 201)

        prof_alice = self.client.post(
            "/api/users/onboarding",
            headers=alice_headers,
            json={
                "monthly_income": 65000,
                "location": "Bengaluru",
                "goals": ["Retirement", "Home"],
                "risk_tolerance": "moderate",
                "currency": "INR",
            },
        )
        self.assertEqual(prof_alice.status_code, 200)

        # 4. Bob creates conversation and saves profile
        conv_bob = self.client.post(
            "/api/conversations",
            headers=bob_headers,
            json={"title": "Bob's Crypto Strategies"},
        )
        self.assertEqual(conv_bob.status_code, 201)

        prof_bob = self.client.post(
            "/api/users/onboarding",
            headers=bob_headers,
            json={
                "monthly_income": 120000,
                "location": "San Francisco",
                "goals": ["Angel investing"],
                "risk_tolerance": "aggressive",
                "currency": "USD",
            },
        )
        self.assertEqual(prof_bob.status_code, 200)

        # 5. Verify Alice only sees Alice's chats
        alice_convs = self.client.get("/api/conversations", headers=alice_headers).json()
        self.assertEqual(len(alice_convs), 1)
        self.assertEqual(alice_convs[0]["title"], "Alice's Private Budget Plan")

        # 6. Verify Bob only sees Bob's chats
        bob_convs = self.client.get("/api/conversations", headers=bob_headers).json()
        self.assertEqual(len(bob_convs), 1)
        self.assertEqual(bob_convs[0]["title"], "Bob's Crypto Strategies")

        # 7. Verify Alice profile is isolated
        alice_prof_res = self.client.get("/api/users/onboarding/profile", headers=alice_headers).json()
        self.assertEqual(alice_prof_res["monthly_income"], 65000)
        self.assertEqual(alice_prof_res["location"], "Bengaluru")
        self.assertEqual(alice_prof_res["currency"], "INR")

        # 8. Verify Bob profile is isolated
        bob_prof_res = self.client.get("/api/users/onboarding/profile", headers=bob_headers).json()
        self.assertEqual(bob_prof_res["monthly_income"], 120000)
        self.assertEqual(bob_prof_res["location"], "San Francisco")
        self.assertEqual(bob_prof_res["currency"], "USD")


if __name__ == "__main__":
    unittest.main()
