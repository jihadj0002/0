import datetime
import random
import threading
import uuid
import re
import logging

from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.conf import settings
from django.core.mail import send_mail
from django.http import HttpResponse
from django.utils import timezone
from datetime import timedelta

from .models import Contact, EmailVerification, Survay
from back.meta_tracking import send_meta_event

logger = logging.getLogger(__name__)


def home(request):
    context = {
        "year": datetime.date.today().year,
        "matrixai_logo_url": f"{settings.MEDIA_URL}images/matrixai.png"
    }
    return render(request, "front/home01.html", context)


def pricing(request):
    return render(request, "front/pricing.html")

def privacy_policy(request):
    return render(request, "front/p_policy.html")
def terms(request):
    return render(request, "front/terms.html")

def forumm(request):
    if request.method == "POST":

        name = request.POST.get("name")
        phone = request.POST.get("phone")
        business_name = request.POST.get("business_name")
        business_type = request.POST.get("business_type")
        customer_range = request.POST.get("customer_range")
        email = request.POST.get("email")
        social_page = request.POST.get("social_page")

        Survay.objects.create(
            name=name,
            phone=phone,
            business_name=business_name,
            business_type=business_type,
            customer_range=customer_range,
            email=email,
            social_page=social_page,
        )

        from crm.services import create_lead
        lead, created = create_lead(
            None, name=name, phone=phone or "", email=email or "",
            source="website", industry=business_type or "",
            notes=f"Survey: business '{business_name or ''}' · customer range {customer_range or ''} · social page {social_page or ''}",
        )

        send_meta_event(
            event_name="Lead",
            event_id=str(uuid.uuid4()),
            request=request,
            email=email or "",
        )

        return redirect('front:home')

    return render(request, "front/forum.html")

def contact(request):
    if request.method == "POST":
        name = request.POST.get("name")
        email = request.POST.get("email")
        business = request.POST.get("business")

        Contact.objects.create(name=name, email=email, business=business)

        from crm.services import create_lead
        lead, created = create_lead(
            None, name=name, phone="", email=email or "",
            source="website", notes=f"Contact form — business: {business or ''}",
        )

        send_meta_event(
            event_name="Lead",
            event_id=str(uuid.uuid4()),
            request=request,
            email=email or "",
        )

        return HttpResponse("✅ Thank you! We'll contact you soon.")

    return HttpResponse("❌ Invalid request.")




def _post_login_redirect(user):
    """Staff go to the CRM; resellers go to manage users; tenants with an incomplete setup go to the wizard."""
    try:
        if user.staff_profile.role == "reseller" and user.staff_profile.is_active:
            return redirect("back:manage_users")
    except Exception:
        pass
    try:
        if user.staff_profile.is_active:
            return redirect("crm:dashboard")
    except Exception:
        pass
    from back.views import _needs_setup
    if _needs_setup(user):
        return redirect("back:setup")
    return redirect("back:dashboard")


def _authenticate_by_email_or_username(identifier, password):
    """Try username first, then email. Returns user or None."""
    user = authenticate(username=identifier, password=password)
    if user is not None:
        return user
    try:
        user_obj = User.objects.get(email__iexact=identifier)
    except User.DoesNotExist:
        user_obj = None
    if user_obj is not None:
        user = authenticate(username=user_obj.username, password=password)
        if user is not None:
            return user
        if user_obj.check_password(password):
            return user_obj
        return None
    try:
        user_obj = User.objects.get(username__iexact=identifier)
        if user_obj.check_password(password):
            return user_obj
    except User.DoesNotExist:
        pass
    return None


def login_view(request):
    if request.user.is_authenticated:
        return _post_login_redirect(request.user)

    next_url = request.GET.get('next', '')

    if request.method == 'POST':
        identifier = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')

        user = _authenticate_by_email_or_username(identifier, password)

        if user is not None:
            if not user.is_active:
                messages.error(
                    request,
                    "Your email is not verified yet. "
                    f'<a href="/verify-email/{user.username}/resend/" class="alert-link">Resend verification code</a>.'
                )
                return redirect("front:verify_email", username=user.username)

            login(request, user)
            messages.success(request, f"Welcome back, {user.username}!")
            if next_url:
                return redirect(next_url)
            return _post_login_redirect(user)
        else:
            messages.error(request, "Invalid username/email or password.")
            form = AuthenticationForm()
            return render(request, 'front/login.html', {
                'form': form,
                'next': next_url
            })
    else:
        form = AuthenticationForm()

    return render(request, 'front/login.html', {
        'form': form,
        'next': next_url
    })


def _generate_verification_code():
    return str(random.randint(100000, 999999))


def _send_verification_email(user, code):
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


def _send_verification_email_async(user, code):
    if settings.EMAIL_BACKEND == "django.core.mail.backends.locmem.EmailBackend":
        _send_verification_email(user, code)
        return
    try:
        from api.concurrency import submit_sync
        submit_sync(_send_verification_email, user, code)
        logger.info("Verification email dispatched for %s", user.email)
    except Exception:
        logger.exception("Failed to dispatch verification email for %s — sending inline", user.email)
        _send_verification_email(user, code)


def signup(request):
    if request.method == "POST":
        try:
            username = request.POST.get('username', '').strip()
            email = request.POST.get('email', '').strip()
            password = request.POST.get('password', '')
            confirm_password = request.POST.get('confirm_password', '')

            if not username or not email or not password:
                messages.error(request, "All fields are required.")
                return redirect("front:signup")

            if password != confirm_password:
                messages.error(request, "Passwords do not match.")
                return redirect("front:signup")

            if len(username) < 3 or len(username) > 30:
                messages.error(request, "Username must be between 3 and 30 characters.")
                return redirect("front:signup")

            if not re.match(r'^[\w.@+-]+\Z', username):
                messages.error(request, "Username contains invalid characters.")
                return redirect("front:signup")

            if len(password) < 8:
                messages.error(request, "Password must be at least 8 characters long.")
                return redirect("front:signup")

            email_re = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
            if not email_re.match(email):
                messages.error(request, "Please enter a valid email address.")
                return redirect("front:signup")

            if User.objects.filter(username__iexact=username).exists():
                messages.error(request, "Username is already taken.")
                return redirect("front:signup")

            if User.objects.filter(email__iexact=email).exists():
                messages.error(request, "An account with this email already exists.")
                return redirect("front:signup")

            user = User.objects.create(
                username=username,
                email=email,
                is_active=False,
            )
            user.set_password(password)
            user.save(update_fields=["password"])

            code = _generate_verification_code()
            EmailVerification.objects.update_or_create(
                user=user,
                defaults={"code": code, "is_used": False, "attempts": 0},
            )

            _send_verification_email_async(user, code)

            messages.success(
                request,
                f"Account created! We sent a 6-digit code to {email}. "
                "Please verify your email to activate your account."
            )
            return redirect("front:verify_email", username=user.username)

        except Exception as e:
            messages.error(request, "Something went wrong. Please try again.")

    return render(request, "front/sign-up.html")


def verify_email(request, username):
    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        messages.error(request, "Invalid verification link.")
        return redirect("front:signup")

    if user.is_active:
        messages.success(request, "Your email is already verified. Please sign in.")
        return redirect("front:login")

    verification, created = EmailVerification.objects.get_or_create(
        user=user,
        defaults={"code": _generate_verification_code(), "is_used": False},
    )

    if request.method == "POST":
        code_entered = request.POST.get("code", "").strip()

        if verification.is_used:
            messages.success(request, "This account is already verified.")
            return redirect("front:login")

        expiry = verification.created_at + timedelta(minutes=30)
        if timezone.now() > expiry:
            verification.code = _generate_verification_code()
            verification.attempts = 0
            verification.created_at = timezone.now()
            verification.save(update_fields=["code", "attempts", "created_at"])
            _send_verification_email_async(user, verification.code)
            messages.error(
                request,
                "Your verification code has expired. A new code has been sent to your email."
            )
            return redirect("front:verify_email", username=username)

        if verification.attempts >= 5:
            verification.code = _generate_verification_code()
            verification.attempts = 0
            verification.created_at = timezone.now()
            verification.save(update_fields=["code", "attempts", "created_at"])
            _send_verification_email_async(user, verification.code)
            messages.error(
                request,
                "Too many failed attempts. A new code has been sent to your email."
            )
            return redirect("front:verify_email", username=username)

        if code_entered != verification.code:
            verification.attempts += 1
            verification.save(update_fields=["attempts"])
            remaining = 5 - verification.attempts
            messages.error(
                request,
                f"Incorrect code. {remaining} attempt{'s' if remaining != 1 else ''} remaining."
            )
            return redirect("front:verify_email", username=username)

        user.is_active = True
        user.save(update_fields=["is_active"])
        verification.is_used = True
        verification.save(update_fields=["is_used"])

        threading.Thread(
            target=send_meta_event,
            kwargs={
                "event_name": "CompleteRegistration",
                "event_id": str(uuid.uuid4()),
                "request": request,
                "email": user.email,
            },
            daemon=True,
        ).start()

        messages.success(request, "Email verified successfully! You can now sign in.")
        return redirect("front:login")

    masked_email = user.email[:3] + "***" + user.email[user.email.find("@"):]
    return render(request, "front/verify-email.html", {
        "username": username,
        "masked_email": masked_email,
        "attempts_remaining": 5 - verification.attempts,
    })


def resend_verification(request, username):
    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        messages.error(request, "Invalid request.")
        return redirect("front:signup")

    if user.is_active:
        messages.success(request, "Your email is already verified.")
        return redirect("front:login")

    code = _generate_verification_code()
    EmailVerification.objects.update_or_create(
        user=user,
        defaults={"code": code, "is_used": False, "attempts": 0},
    )

    _send_verification_email_async(user, code)
    messages.success(request, "A new verification code has been sent to your email.")
    return redirect("front:verify_email", username=username)


def logout_view(request):
    logout(request)
    messages.success(request, "You have been successfully logged out.")
    return redirect('front:home')