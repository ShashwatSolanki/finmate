"""Email dispatching service for FinMate account verification and password reset.
Supports offline mock mode for testing/development and production SMTP delivery.
"""

import logging
import secrets
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import settings

logger = logging.getLogger(__name__)


def generate_otp(length: int = 6) -> str:
    """Generate a cryptographically secure numeric OTP."""
    digits = "0123456789"
    return "".join(secrets.choice(digits) for _ in range(length))


def _send_email(to_email: str, subject: str, text_content: str, html_content: str) -> bool:
    """Internal helper to dispatch emails via SMTP or log to console in mock mode."""
    if settings.email_mock_mode or not settings.smtp_user or not settings.smtp_password:
        logger.info(
            f"[EMAIL SERVICE - MOCK MODE]\n"
            f"  To: {to_email}\n"
            f"  Subject: {subject}\n"
            f"  Message: {text_content}\n"
        )
        print(f"[EMAIL SERVICE] Simulated email sent to {to_email}: {subject}")
        return True

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = settings.smtp_from_email
        msg["To"] = to_email

        msg.attach(MIMEText(text_content, "plain"))
        msg.attach(MIMEText(html_content, "html"))

        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_password)
            server.sendmail(settings.smtp_from_email, to_email, msg.as_string())

        logger.info(f"Email successfully delivered to {to_email}")
        return True
    except Exception as exc:
        logger.error(f"Failed to deliver email to {to_email}: {exc}")
        return False


def send_verification_email(to_email: str, code: str) -> bool:
    """Send a 6-digit email verification OTP."""
    subject = "Verify your FinMate Account"
    text_content = (
        f"Welcome to FinMate!\n\n"
        f"Your verification code is: {code}\n\n"
        f"This code will expire in {settings.verification_code_expire_minutes} minutes.\n"
        f"If you did not request this, please ignore this email."
    )
    html_content = f"""
    <!DOCTYPE html>
    <html>
      <body style="font-family: Arial, sans-serif; background-color: #f7f9fa; padding: 20px;">
        <div style="max-width: 500px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; border: 1px solid #e1e8ed;">
          <h2 style="color: #1da1f2; margin-top: 0;">FinMate Account Verification</h2>
          <p>Thank you for signing up for FinMate. Please use the verification code below to activate your account:</p>
          <div style="background: #f0f4f8; padding: 15px; text-align: center; border-radius: 6px; margin: 20px 0;">
            <span style="font-size: 32px; font-weight: bold; letter-spacing: 5px; color: #102a43;">{code}</span>
          </div>
          <p style="color: #627d98; font-size: 13px;">This code will expire in {settings.verification_code_expire_minutes} minutes. If you did not create this account, please ignore this email.</p>
        </div>
      </body>
    </html>
    """
    return _send_email(to_email, subject, text_content, html_content)


def send_password_reset_email(to_email: str, code: str) -> bool:
    """Send a 6-digit password reset OTP."""
    subject = "FinMate Password Reset Request"
    text_content = (
        f"Hello,\n\n"
        f"We received a request to reset your FinMate account password.\n"
        f"Your password reset code is: {code}\n\n"
        f"This code will expire in {settings.password_reset_code_expire_minutes} minutes.\n"
        f"If you did not make this request, your account is safe and you can ignore this email."
    )
    html_content = f"""
    <!DOCTYPE html>
    <html>
      <body style="font-family: Arial, sans-serif; background-color: #f7f9fa; padding: 20px;">
        <div style="max-width: 500px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; border: 1px solid #e1e8ed;">
          <h2 style="color: #e0245e; margin-top: 0;">Password Reset Request</h2>
          <p>We received a request to reset your FinMate password. Use the verification code below to choose a new password:</p>
          <div style="background: #fff0f5; padding: 15px; text-align: center; border-radius: 6px; margin: 20px 0;">
            <span style="font-size: 32px; font-weight: bold; letter-spacing: 5px; color: #9e0c3f;">{code}</span>
          </div>
          <p style="color: #627d98; font-size: 13px;">This code will expire in {settings.password_reset_code_expire_minutes} minutes. If you did not request a password reset, please ignore this email.</p>
        </div>
      </body>
    </html>
    """
    return _send_email(to_email, subject, text_content, html_content)

