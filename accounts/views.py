from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.hashers import make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from .models import ChatHistory, EmailOTP, PendingSignup
import json
from django.conf import settings
from django.utils.timezone import localtime
from datetime import date
from django.utils.timezone import now
from django.views.decorators.http import require_POST
from .models import UserProfile
from .email_service import (
    send_registration_otp,
    send_reset_otp,
    send_welcome_email,
    send_password_changed_email,
    EmailServiceError,
)
from .email.error_mapping import email_error_response
from .otp_service import issue_otp, verify_otp as verify_otp_record
from .rate_limit import (
    check_send_cooldown,
    check_send_window,
    check_ip_throttle,
    record_send,
)



# ================= LOGIN =================
def user_login(request):
    if request.method == "POST":
        email = (request.POST.get("email") or "").strip().lower()
        password = request.POST.get("password") or ""

        if not email or not password:
            return render(request, "login.html", {"error": "Email and password are required"})

        user = authenticate(request, username=email, password=password)
        if user:
            login(request, user)
            return redirect("home")

        return render(request, "login.html", {"error": "Invalid email or password"})

    return render(request, "login.html")


def user_signup(request):
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        email = (request.POST.get("email") or "").strip().lower()
        password = request.POST.get("password") or ""
        confirm_password = request.POST.get("confirmPassword") or ""

        if not name or not email or not password:
            return render(request, 'signup.html', {
                'error': 'All fields are required'
            })

        if password != confirm_password:
            return render(request, "signup.html", {"error": "Passwords do not match"})

        if User.objects.filter(username=email).exists():
            return render(request, "signup.html", {"error": "Email already registered"})

        try:
            validate_password(password)
        except ValidationError as exc:
            return render(request, "signup.html", {"error": " ".join(exc.messages)})

        if not check_ip_throttle(request, "signup"):
            return render(request, "signup.html", {
                "error": "Too many signup attempts from this network. Please try again later."
            })

        if not check_send_cooldown(email, "signup") or not check_send_window(email, "signup"):
            return render(request, "signup.html", {
                "error": "A verification code was already sent recently. Please wait before requesting another."
            })

        # Hash once, here. The plaintext password is never stored in the
        # session or anywhere else — only this hash, in a server-side
        # pending-registration record keyed by email.
        password_hash = make_password(password)
        PendingSignup.objects.update_or_create(
            email=email,
            defaults={"name": name, "password_hash": password_hash},
        )

        otp = issue_otp(email, "signup")

        try:
            send_registration_otp(email, otp)
        except EmailServiceError:
            return render(request, 'signup.html', {
                'error': 'Could not send verification code. Please try again in a moment.'
            })

        record_send(email, "signup")

        # Session only ever holds a pointer (the email) — never the password.
        request.session["signup_email"] = email
        return redirect("verify_otp")

    return render(request, "signup.html")



# ================================
# LOGOUT
# ================================
def user_logout(request):
    logout(request)
    return redirect('home')


# ================================
# SAVE CHAT HISTORY
# ================================
@login_required
def save_history(request):
    if request.method == "POST":
        data = json.loads(request.body)
        ChatHistory.objects.create(
            user=request.user,
            query=data.get("query"),
            response=data.get("response")
        )
        return JsonResponse({"status": "ok"})

    return JsonResponse({"error": "POST required"}, status=405)


# ================================
# VIEW HISTORY PAGE
# ================================
@login_required
def history_view(request):
    history_items = ChatHistory.objects.filter(user=request.user).order_by("-created_at")

    final_history = []

    for item in history_items:
        response = item.response.lower()

        # Detect content type
        if response.startswith("http"):
            item.type = "image"
        elif "<" in response and ">" in response:
            item.type = "code"
        else:
            item.type = "text"


        final_history.append(item)

    return render(request, "history.html", {"history": final_history})



@login_required
def delete_history(request, item_id):
    ChatHistory.objects.filter(id=item_id, user=request.user).delete()
    return redirect("history")


@login_required
def clear_history(request):
    ChatHistory.objects.filter(user=request.user).delete()
    return redirect("history")

# ========================================#
#            VERIFY OTP  
# ================= VERIFY OTP ============#
# @require_POST
def verify_otp(request):

    # 🔹 GET → OTP PAGE SHOW
    if request.method == "GET":
        email = request.session.get("signup_email")

        if not email:
            return redirect("signup")

        return render(request, "verify_otp.html", {
            "email": email
        })

    # 🔹 POST → OTP VERIFY
    if request.method == "POST":
        otp = (request.POST.get("otp") or "").strip()
        email = request.session.get("signup_email")

        if not otp or not email:
            return JsonResponse({
                "status": "error",
                "message": "Session expired"
            })

        try:
            pending = PendingSignup.objects.get(email=email)
        except PendingSignup.DoesNotExist:
            return JsonResponse({
                "status": "error",
                "message": "Signup session expired. Please sign up again."
            })

        if pending.is_expired():
            pending.delete()
            EmailOTP.objects.filter(email=email, purpose="signup").delete()
            del request.session["signup_email"]
            return JsonResponse({
                "status": "error",
                "message": "Signup session expired. Please sign up again."
            })

        ok, error_message, _status = verify_otp_record(email, "signup", otp)
        if not ok:
            return JsonResponse({"status": "error", "message": error_message})

        # ✅ OTP CORRECT → CREATE USER + LOGIN.
        # pending.password_hash was hashed exactly once, at signup time
        # (make_password). Assign it directly to user.password — never
        # via create_user()/set_password() here, which would hash it again.
        user = User(username=email, email=email, first_name=pending.name)
        user.password = pending.password_hash
        user.save()

        login(request, user)

        name = pending.name
        pending.delete()
        del request.session["signup_email"]

        # Best-effort — never blocks the signup flow if it fails.
        send_welcome_email(email, name)

        return JsonResponse({"status": "success"})

# ================= RESEND OTP =================
@require_POST
def resend_otp(request):

    email = request.session.get("signup_email")
    if not email:
        return JsonResponse({"error": "Session expired"}, status=400)

    if not PendingSignup.objects.filter(email=email).exists():
        return JsonResponse({"error": "Signup session expired. Please sign up again."}, status=400)

    if not check_ip_throttle(request, "resend_otp"):
        return JsonResponse({"error": "Too many requests. Please try again later."}, status=429)

    if not check_send_cooldown(email, "signup"):
        return JsonResponse({"error": "Please wait a moment before requesting another code."}, status=429)

    if not check_send_window(email, "signup"):
        return JsonResponse({"error": "Too many codes requested. Please try again later."}, status=429)

    otp = issue_otp(email, "signup")

    try:
        send_registration_otp(email, otp)
    except EmailServiceError as exc:
        return email_error_response(exc)

    record_send(email, "signup")

    return JsonResponse({"status": "success"})


# ========================== =============#
#         Forgat Password Views
# ========================== ============#

from django.views.decorators.http import require_POST
from django.shortcuts import render
from django.contrib.auth.models import User
from django.conf import settings
from .models import EmailOTP

# Generic, enumeration-resistant response for forgot-password OTP requests —
# deliberately identical whether or not the email is registered.
_FORGOT_SEND_GENERIC_RESPONSE = {
    "status": "otp_sent",
    "message": "If an account exists for this email, a reset code has been sent.",
}


def forgot_page(request):
    return render(request, "forget.html")


@require_POST
def forgot_send_otp(request):
    email = (request.POST.get("email") or "").strip().lower()

    if not email:
        return JsonResponse({"error": "Email required"}, status=400)

    if not check_ip_throttle(request, "forgot_send_otp"):
        return JsonResponse({"error": "Too many requests. Please try again later."}, status=429)

    user_exists = User.objects.filter(email=email).exists()

    # Always set the session pointer and return the same response whether
    # or not the account exists — forgot_verify_otp will simply never find
    # a matching OTP for an email we never sent one to.
    request.session["reset_email"] = email
    request.session["otp_verified"] = False

    if not user_exists:
        return JsonResponse(_FORGOT_SEND_GENERIC_RESPONSE)

    if not check_send_cooldown(email, "reset") or not check_send_window(email, "reset"):
        # Still return the generic response — don't leak throttle state
        # for an existing account either.
        return JsonResponse(_FORGOT_SEND_GENERIC_RESPONSE)

    otp = issue_otp(email, "reset")

    try:
        send_reset_otp(email, otp)
    except EmailServiceError:
        # Do not leak provider-level failure detail through an
        # enumeration-sensitive endpoint; log server-side (handled inside
        # the email service) and still return the generic response.
        return JsonResponse(_FORGOT_SEND_GENERIC_RESPONSE)

    record_send(email, "reset")

    return JsonResponse(_FORGOT_SEND_GENERIC_RESPONSE)


@require_POST
def forgot_resend_otp(request):
    email = request.session.get("reset_email")

    if not email:
        return JsonResponse({"error": "Session expired"}, status=400)

    if not check_ip_throttle(request, "forgot_resend_otp"):
        return JsonResponse({"error": "Too many requests. Please try again later."}, status=429)

    user_exists = User.objects.filter(email=email).exists()
    if not user_exists:
        return JsonResponse(_FORGOT_SEND_GENERIC_RESPONSE)

    if not check_send_cooldown(email, "reset") or not check_send_window(email, "reset"):
        return JsonResponse(_FORGOT_SEND_GENERIC_RESPONSE)

    otp = issue_otp(email, "reset")

    try:
        send_reset_otp(email, otp)
    except EmailServiceError:
        return JsonResponse(_FORGOT_SEND_GENERIC_RESPONSE)

    record_send(email, "reset")

    return JsonResponse({"status": "otp_resent"})


@require_POST
def forgot_verify_otp(request):
    otp = (request.POST.get("otp") or "").strip()
    email = request.session.get("reset_email")

    if not email or not otp:
        return JsonResponse({"error": "Session expired"}, status=400)

    ok, error_message, status_code = verify_otp_record(email, "reset", otp)
    if not ok:
        return JsonResponse({"error": error_message}, status=status_code)

    request.session["otp_verified"] = True

    return JsonResponse({"status": "verified"})


@require_POST
def forgot_reset_password(request):
    if not request.session.get("otp_verified"):
        return JsonResponse({"error": "OTP not verified"}, status=403)

    password = request.POST.get("password") or ""
    email = request.session.get("reset_email")

    if not email:
        return JsonResponse({"error": "Session expired"}, status=400)

    try:
        validate_password(password)
    except ValidationError as exc:
        return JsonResponse({"error": " ".join(exc.messages)}, status=400)

    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        # Session claims otp_verified for an email with no matching user —
        # shouldn't happen via the normal flow, but fail closed rather than
        # 500ing or leaking which branch was hit.
        request.session.flush()
        return JsonResponse({"error": "Password reset failed. Please start over."}, status=400)

    user.set_password(password)
    user.save()

    request.session.flush()

    # Best-effort — never blocks the response if it fails.
    send_password_changed_email(email)

    return JsonResponse({"status": "password_reset"})


# ===========================================#
              #profile view 
# ==========================================#

@login_required
def profile_page(request):
    user = request.user

    context = {
        "full_name": user.first_name,
        "email": user.email,
        "username": user.username,
        "date_joined": user.date_joined,
        "last_login": user.last_login,
    }

    return render(request, "profile.html", context)

@login_required
def profile_data(request):
    user = request.user
    chats = ChatHistory.objects.filter(user=user).order_by("-created_at")

    profile, _ = UserProfile.objects.get_or_create(user=user)

    activities = []
    for c in chats[:5]:
        activities.append({
            "icon": "💬",
            "title": c.query[:40],
            "time": c.created_at.strftime("%d %b %Y, %H:%M")
        })

    days_active = max(1, (now().date() - user.date_joined.date()).days)

    return JsonResponse({
        "name": user.get_full_name() or user.username,
        "email": user.email,
        "phone": profile.phone,
        "bio": profile.bio,
        "avatar": profile.avatar.url if profile.avatar else None,  # ✅ IMPORTANT
        "accountType": "Free",
        "twoFactorEnabled": False,
        "stats": {
            "totalChats": chats.count(),
            "daysActive": days_active,
            "satisfaction": 100
        },
        "activities": activities
    })


@login_required
def profile_update(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)

    data = json.loads(request.body)

    user = request.user
    user.first_name = data.get("name", user.first_name)
    user.save()

    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.phone = data.get("phone", "")
    profile.bio = data.get("bio", "")
    profile.save()

    return JsonResponse({"status": "updated"})


@require_POST
@login_required
def profile_change_password(request):
    data = json.loads(request.body)

    user = request.user
    if not user.check_password(data.get("currentPassword")):
        return JsonResponse({"error": "Wrong current password"}, status=400)

    new_password = data.get("newPassword") or ""
    try:
        validate_password(new_password, user=user)
    except ValidationError as exc:
        return JsonResponse({"error": " ".join(exc.messages)}, status=400)

    user.set_password(new_password)
    user.save()
    update_session_auth_hash(request, user)

    # Best-effort — never blocks the response if it fails.
    send_password_changed_email(user.email)

    return JsonResponse({"status": "password_changed"})


@require_POST
@login_required
def profile_delete(request):
    data = json.loads(request.body)

    user = request.user
    if not user.check_password(data.get("password")):
        return JsonResponse({"error": "Wrong password"}, status=400)

    user.delete()
    return JsonResponse({"status": "deleted"})

@login_required
@require_POST
def profile_avatar(request):
    avatar = request.FILES.get("avatar")

    if not avatar:
        return JsonResponse({"error": "No file uploaded"}, status=400)

    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    profile.avatar = avatar
    profile.save()

    return JsonResponse({
        "status": "uploaded",
        "url": profile.avatar.url
    })
