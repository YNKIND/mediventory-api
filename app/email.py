import os

import httpx
from dotenv import load_dotenv

load_dotenv()

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
EMAIL_FROM = os.getenv("EMAIL_FROM", "Mediventory <onboarding@resend.dev>")
APP_URL = (os.getenv("APP_URL") or "http://localhost:5173").rstrip("/")


def send_email(to: str, subject: str, body_html: str) -> bool:
    """Send an email. If no provider is configured, print it to the console
    so development works without external setup."""
    if not RESEND_API_KEY:
        print("\n" + "=" * 60)
        print("EMAIL NOT SENT (no RESEND_API_KEY configured)")
        print(f"To:      {to}")
        print(f"Subject: {subject}")
        print("-" * 60)
        print(body_html)
        print("=" * 60 + "\n")
        return False

    try:
        response = httpx.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"from": EMAIL_FROM, "to": [to], "subject": subject, "html": body_html},
            timeout=10.0,
        )
        if response.status_code >= 400:
            print(f"Email provider error {response.status_code}: {response.text}")
            return False
        return True
    except Exception as exc:
        print(f"Email send failed: {exc}")
        return False


def send_password_reset(to: str, full_name: str, raw_token: str) -> bool:
    link = f"{APP_URL}/?reset_token={raw_token}"
    html = f"""
    <div style="font-family: Arial, sans-serif; color: #0B1524; line-height: 1.6;">
      <h2 style="color: #1B69E8;">Reset your Mediventory password</h2>
      <p>Hi {full_name},</p>
      <p>Someone asked to reset the password for this account. If it was you, use the link below. It expires in one hour.</p>
      <p style="margin: 24px 0;">
        <a href="{link}" style="background: #1B69E8; color: #fff; padding: 12px 22px;
           border-radius: 8px; text-decoration: none; font-weight: 600;">Set a new password</a>
      </p>
      <p style="font-size: 13px; color: #566172;">If you did not request this, you can ignore this email and your password stays unchanged.</p>
    </div>
    """
    return send_email(to, "Reset your Mediventory password", html)


def send_invite(to: str, full_name: str, raw_token: str, inviter: str) -> bool:
    link = f"{APP_URL}/?reset_token={raw_token}"
    html = f"""
    <div style="font-family: Arial, sans-serif; color: #0B1524; line-height: 1.6;">
      <h2 style="color: #1B69E8;">You have been added to Mediventory</h2>
      <p>Hi {full_name},</p>
      <p>{inviter} created an account for you. Set your password using the link below. It expires in one hour.</p>
      <p style="margin: 24px 0;">
        <a href="{link}" style="background: #1B69E8; color: #fff; padding: 12px 22px;
           border-radius: 8px; text-decoration: none; font-weight: 600;">Set your password</a>
      </p>
    </div>
    """
    return send_email(to, "Your Mediventory account", html)


def email_configured() -> bool:
    return bool(RESEND_API_KEY)