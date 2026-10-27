import logging

from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.conf import settings

logger = logging.getLogger(__name__)


def send_verification_email_job(user_id, code):
    """RQ job: send verification email to a user. Called via RQ enqueue."""
    from front.models import EmailVerification
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        logger.error("send_verification_email_job: user %s not found", user_id)
        return

    subject = "Your TheMatrixAi Email Verification Code"
    message = f"""
Hi {user.username},

Thank you for signing up with TheMatrixAi!

Your email verification code is: {code}

Enter this code on the verification page to activate your account.

This code will expire in 30 minutes.

If you did not sign up for TheMatrixAi, please ignore this email.

Best regards,
TheMatrixAi Team
"""
    html_message = f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family: Arial, sans-serif; background: #f4f4f8; padding: 20px;">
  <div style="max-width: 480px; margin: 0 auto; background: white; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.1);">
    <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 24px; text-align: center;">
      <h2 style="color: white; margin: 0;">TheMatrixAi</h2>
      <p style="color: rgba(255,255,255,0.85); margin: 4px 0 0;">Verify your email address</p>
    </div>
    <div style="padding: 32px 24px; text-align: center;">
      <p style="font-size: 16px; color: #333;">Hi <strong>{user.username}</strong>,</p>
      <p style="color: #555;">Enter this code to activate your account:</p>
      <div style="font-size: 36px; letter-spacing: 8px; font-weight: bold; color: #667eea; background: #f0f0ff; padding: 16px; border-radius: 8px; margin: 16px 0;">
        {code}
      </div>
      <p style="color: #888; font-size: 13px;">This code expires in 30 minutes.</p>
    </div>
    <div style="padding: 16px 24px; background: #f9f9fb; text-align: center; font-size: 12px; color: #999;">
      <p style="margin: 0;">TheMatrixAi — AI-powered customer engagement</p>
    </div>
  </div>
</body>
</html>
"""

    try:
        send_mail(
            subject,
            message,
            settings.DEFAULT_FROM_EMAIL,
            [user.email],
            fail_silently=False,
            html_message=html_message,
        )
        logger.info("Verification email sent to %s (code=%s)", user.email, code)
    except Exception:
        logger.exception("Failed to send verification email to %s", user.email)
        raise