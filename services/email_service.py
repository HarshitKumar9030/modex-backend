"""
Email Service for Modex.
Sends HTML emails via the Mailgun HTTP API.
"""
import httpx
import logging

from core.config import settings

logger = logging.getLogger("modex.email")


async def send_email_async(to_email: str, subject: str, html_content: str):
    """Send an email via the Mailgun API."""
    if not settings.MAILGUN_API_KEY or not settings.MAILGUN_DOMAIN:
        logger.warning(f"Mailgun not configured. Skipping email to {to_email}: {subject}")
        return

    sender = f"{settings.SENDER_NAME} <{settings.SENDER_EMAIL}>"
    url = f"{settings.MAILGUN_URL}/v3/{settings.MAILGUN_DOMAIN}/messages"

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                auth=("api", settings.MAILGUN_API_KEY),
                data={
                    "from": sender,
                    "to": [to_email],
                    "subject": subject,
                    "html": html_content,
                },
            )
            resp.raise_for_status()
            logger.info(f"Mailgun email sent to {to_email}: {subject}")
    except httpx.HTTPStatusError as e:
        logger.error(f"Mailgun HTTP {e.response.status_code} sending to {to_email}: {e.response.text}")
    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {e}")


async def send_beta_status_email(email: str, status: str):
    """Send an HTML email when beta status changes to approved or rejected."""
    if status == "approved":
        subject = "You're in! Welcome to Modex Beta"
        html = f"""
        <html>
        <body style="font-family: sans-serif; background-color: #0A0A0C; color: #E0E0E0; padding: 40px; text-align: center;">
            <div style="max-width: 500px; margin: 0 auto; background-color: #111113; border: 1px solid #333; border-radius: 16px; padding: 30px;">
                <svg width="40" height="40" viewBox="0 0 24 24" fill="none" style="margin-bottom: 20px;">
                    <path d="M12 2C12 2 12 9 19 12C12 15 12 22 12 22C12 22 12 15 5 12C12 9 12 2 12 2Z" fill="#FADCD0" />
                </svg>
                <h1 style="color: #ffffff; margin-bottom: 15px;">Welcome to Modex!</h1>
                <p style="color: #aaaaaa; line-height: 1.6; margin-bottom: 25px;">
                    Great news! Your beta access request for Modex has been approved. You can now log in and start processing your files with zero friction.
                </p>
                <a href="https://modex.agfe.tech" style="display: inline-block; background-color: #FADCD0; color: #000; text-decoration: none; padding: 12px 24px; border-radius: 50px; font-weight: bold; font-size: 14px;">
                    Launch Modex
                </a>
                <p style="margin-top: 30px; font-size: 12px; color: #666;">
                    If you didn't request this, you can safely ignore this email.
                </p>
            </div>
        </body>
        </html>
        """
    elif status == "rejected":
        subject = "Update regarding your Modex Beta access"
        html = f"""
        <html>
        <body style="font-family: sans-serif; background-color: #0A0A0C; color: #E0E0E0; padding: 40px; text-align: center;">
            <div style="max-width: 500px; margin: 0 auto; background-color: #111113; border: 1px solid #333; border-radius: 16px; padding: 30px;">
                <h1 style="color: #ffffff; margin-bottom: 15px;">Beta Access Update</h1>
                <p style="color: #aaaaaa; line-height: 1.6;">
                    Thank you for your interest in Modex. Unfortunately, we are not able to offer you beta access at this time.
                </p>
                <p style="color: #aaaaaa; line-height: 1.6;">
                    We are slowly rolling out to more users, so we may reach back out in the future!
                </p>
            </div>
        </body>
        </html>
        """
    else:
        return

    await send_email_async(email, subject, html)