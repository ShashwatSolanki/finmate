"""Integration tests for Member 1 authentication, user management, and OAuth flows."""

import unittest
import uuid
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.db.base import Base
from app.db.models import User, RefreshToken, OAuthAccount
from app.main import app

# In-memory SQLite database for test isolation
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


class AuthIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)
        app.dependency_overrides[get_db] = override_get_db
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=engine)

    def test_01_registration_password_complexity(self):
        # Weak password must fail validation
        res = self.client.post(
            "/api/auth/register",
            json={"email": "weak@example.com", "password": "weak"},
        )
        self.assertEqual(res.status_code, 422)

        # Strong password succeeds
        res = self.client.post(
            "/api/auth/register",
            json={
                "email": "testuser@finmate.com",
                "password": "SecureP@ssw0rd123",
                "display_name": "Test User",
            },
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("access_token", data)
        self.assertIn("refresh_token", data)
        self.assertEqual(data["token_type"], "bearer")

        # Duplicate email registration must return 409
        dup_res = self.client.post(
            "/api/auth/register",
            json={"email": "testuser@finmate.com", "password": "SecureP@ssw0rd123"},
        )
        self.assertEqual(dup_res.status_code, 409)

    def test_02_login_and_profile_access(self):
        # Invalid password returns 401
        bad_res = self.client.post(
            "/api/auth/login",
            json={"email": "testuser@finmate.com", "password": "WrongPassword!123"},
        )
        self.assertEqual(bad_res.status_code, 401)

        # Valid login returns tokens
        login_res = self.client.post(
            "/api/auth/login",
            json={"email": "testuser@finmate.com", "password": "SecureP@ssw0rd123"},
        )
        self.assertEqual(login_res.status_code, 200)
        tokens = login_res.json()
        access_token = tokens["access_token"]

        # Access /api/users/me with Bearer token
        me_res = self.client.get(
            "/api/users/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        self.assertEqual(me_res.status_code, 200)
        user_info = me_res.json()
        self.assertEqual(user_info["email"], "testuser@finmate.com")
        self.assertEqual(user_info["display_name"], "Test User")
        self.assertTrue(user_info["is_active"])
        self.assertTrue(user_info["has_password"])

    def test_03_refresh_token_rotation(self):
        login_res = self.client.post(
            "/api/auth/login",
            json={"email": "testuser@finmate.com", "password": "SecureP@ssw0rd123"},
        )
        old_refresh = login_res.json()["refresh_token"]

        # Use refresh token to obtain a new pair
        refresh_res = self.client.post(
            "/api/auth/refresh",
            json={"refresh_token": old_refresh},
        )
        self.assertEqual(refresh_res.status_code, 200)
        new_tokens = refresh_res.json()
        self.assertIn("access_token", new_tokens)
        self.assertIn("refresh_token", new_tokens)
        new_refresh = new_tokens["refresh_token"]
        self.assertNotEqual(old_refresh, new_refresh)

        # Attempt to reuse old revoked refresh token -> Must fail with 401
        reuse_res = self.client.post(
            "/api/auth/refresh",
            json={"refresh_token": old_refresh},
        )
        self.assertEqual(reuse_res.status_code, 401)

    def test_04_user_profile_update_and_change_password(self):
        login_res = self.client.post(
            "/api/auth/login",
            json={"email": "testuser@finmate.com", "password": "SecureP@ssw0rd123"},
        )
        token = login_res.json()["access_token"]
        auth_header = {"Authorization": f"Bearer {token}"}

        # Update display name
        patch_res = self.client.patch(
            "/api/users/me",
            json={"display_name": "Updated Name"},
            headers=auth_header,
        )
        self.assertEqual(patch_res.status_code, 200)
        self.assertEqual(patch_res.json()["display_name"], "Updated Name")

        # Change password with invalid current password -> 400
        bad_change = self.client.post(
            "/api/users/change-password",
            json={"current_password": "WrongOldPassword!1", "new_password": "BrandNewP@ssw0rd456"},
            headers=auth_header,
        )
        self.assertEqual(bad_change.status_code, 400)

        # Change password with valid current password
        good_change = self.client.post(
            "/api/users/change-password",
            json={"current_password": "SecureP@ssw0rd123", "new_password": "BrandNewP@ssw0rd456"},
            headers=auth_header,
        )
        self.assertEqual(good_change.status_code, 200)

        # Login with old password fails
        old_login = self.client.post(
            "/api/auth/login",
            json={"email": "testuser@finmate.com", "password": "SecureP@ssw0rd123"},
        )
        self.assertEqual(old_login.status_code, 401)

        # Login with new password succeeds
        new_login = self.client.post(
            "/api/auth/login",
            json={"email": "testuser@finmate.com", "password": "BrandNewP@ssw0rd456"},
        )
        self.assertEqual(new_login.status_code, 200)

    def test_05_google_oauth_and_account_linking(self):
        # 1. Sign in with Google (new user)
        mock_cred = "mock-google-token:oauth_user@finmate.com:google-sub-7890:OAuth User"
        g_res = self.client.post(
            "/api/auth/google",
            json={"credential": mock_cred},
        )
        self.assertEqual(g_res.status_code, 200)
        g_tokens = g_res.json()
        self.assertIn("access_token", g_tokens)
        g_access_token = g_tokens["access_token"]

        # Check profile of Google user
        me_res = self.client.get(
            "/api/users/me",
            headers={"Authorization": f"Bearer {g_access_token}"},
        )
        self.assertEqual(me_res.status_code, 200)
        self.assertEqual(me_res.json()["email"], "oauth_user@finmate.com")
        self.assertEqual(me_res.json()["auth_provider"], "google")

        # 2. Existing password user linking their Google account
        login_res = self.client.post(
            "/api/auth/login",
            json={"email": "testuser@finmate.com", "password": "BrandNewP@ssw0rd456"},
        )
        user_token = login_res.json()["access_token"]
        user_header = {"Authorization": f"Bearer {user_token}"}

        link_cred = "mock-google-token:testuser@finmate.com:google-sub-9999:Test User"
        link_res = self.client.post(
            "/api/auth/link/google",
            json={"credential": link_cred},
            headers=user_header,
        )
        self.assertEqual(link_res.status_code, 200)

        # Verify linked status
        me_res = self.client.get("/api/users/me", headers=user_header)
        self.assertTrue(me_res.json()["google_linked"])

        # Unlink Google account
        unlink_res = self.client.post("/api/auth/unlink/google", headers=user_header)
        self.assertEqual(unlink_res.status_code, 200)

        # Verify unlinked status
        me_res = self.client.get("/api/users/me", headers=user_header)
        self.assertFalse(me_res.json()["google_linked"])

    def test_06_logout(self):
        login_res = self.client.post(
            "/api/auth/login",
            json={"email": "testuser@finmate.com", "password": "BrandNewP@ssw0rd456"},
        )
        refresh_token = login_res.json()["refresh_token"]

        # Logout
        logout_res = self.client.post(
            "/api/auth/logout",
            json={"refresh_token": refresh_token},
        )
        self.assertEqual(logout_res.status_code, 200)

        # Refresh attempt with logged out token fails
        fail_res = self.client.post(
            "/api/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        self.assertEqual(fail_res.status_code, 401)


if __name__ == "__main__":
    unittest.main()

