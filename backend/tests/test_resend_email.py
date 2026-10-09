from types import SimpleNamespace

import httpx

from app.services import email_service


def test_resend_email_uses_https_api(monkeypatch):
    monkeypatch.setattr(email_service.settings, "email_provider", "resend")
    monkeypatch.setattr(email_service.settings, "email_mock_mode", False)
    monkeypatch.setattr(email_service.settings, "app_env", "staging")
    monkeypatch.setattr(email_service.settings, "resend_api_key", "test-api-key")
    monkeypatch.setattr(email_service.settings, "resend_from_email", "onboarding@resend.dev")

    captured = {}

    def fake_post(url, *, headers, json, timeout):
        captured.update(url=url, headers=headers, json=json, timeout=timeout)
        return SimpleNamespace(is_success=True, status_code=200)

    monkeypatch.setattr(email_service.httpx, "post", fake_post)

    assert email_service._send_email("recipient@example.com", "Test", "Plain text", "<p>HTML</p>")
    assert captured["url"] == "https://api.resend.com/emails"
    assert captured["headers"]["Authorization"] == "Bearer test-api-key"
    assert captured["json"]["to"] == ["recipient@example.com"]
    assert captured["json"]["text"] == "Plain text"


def test_resend_email_returns_false_for_http_error(monkeypatch):
    monkeypatch.setattr(email_service.settings, "email_provider", "resend")
    monkeypatch.setattr(email_service.settings, "email_mock_mode", False)
    monkeypatch.setattr(email_service.settings, "app_env", "staging")
    monkeypatch.setattr(email_service.settings, "resend_api_key", "test-api-key")

    def fake_post(*args, **kwargs):
        request = httpx.Request("POST", "https://api.resend.com/emails")
        return httpx.Response(401, request=request)

    monkeypatch.setattr(email_service.httpx, "post", fake_post)

    assert not email_service._send_email("recipient@example.com", "Test", "Plain text", "<p>HTML</p>")
