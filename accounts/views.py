from pyexpat.errors import messages
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.contrib.auth import update_session_auth_hash
from django.http import JsonResponse
from django.core.mail import send_mail
from .models import ChatHistory, EmailOTP
import json, random
from django.conf import settings
from django.utils.timezone import localtime
from datetime import date
from django.http import JsonResponse
from django.utils.timezone import now
from django.views.decorators.http import require_POST
from .models import UserProfile
import threading
from .email_service import send_email_async



# ================= LOGIN =================
def user_login(request):
    if request.method == "POST":
        email = request.POST.get("email").lower()
        password = request.POST.get("password")

        user = authenticate(request, username=email, password=password)
        if user:
            login(request, user)
            return redirect("home")

        return render(request, "login.html", {"error": "Invalid email or password"})

    return render(request, "login.html")

print("EMAIL_BACKEND:", settings.EMAIL_BACKEND)

# ================= SIGNUP (SEND OTP) =================
def user_signup(request):
    if request.method == "POST":
        name = request.POST.get("name")
        email = request.POST.get("email").lower()
        password = request.POST.get("password")
        confirm_password = request.POST.get("confirmPassword")

        if not name or not email or not password:
            return render(request, 'signup.html', {
                'error': 'All fields are required'
            })

        if password != confirm_password:
            return render(request, "signup.html", {"error": "Passwords do not match"})

        if User.objects.filter(username=email).exists():
            return render(request, "signup.html", {"error": "Email already registered"})

        otp = str(random.randint(100000, 999999))

        EmailOTP.objects.update_or_create(
            email=email,
            purpose="signup",
            defaults={"otp": otp}
        )


        threading.Thread(
            target=send_email_async,
            args=("Your abc.com OTP", f"Your OTP is {otp}", email)
        ).start()



        request.session["signup_data"] = {
            "name": name,
            "email": email,
            "password": password
        }
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
        signup_data = request.session.get("signup_data")

        if not signup_data:
            return redirect("signup")

        return render(request, "verify_otp.html", {
            "email": signup_data["email"]
        })

    # 🔹 POST → OTP VERIFY
    if request.method == "POST":
        otp = request.POST.get("otp")
        signup_data = request.session.get("signup_data")

        if not otp or not signup_data:
            return JsonResponse({
                "status": "error",
                "message": "Session expired"
            })

        email = signup_data["email"]

        try:
            record = EmailOTP.objects.get(email=email, purpose="signup")
        except EmailOTP.DoesNotExist:
            return JsonResponse({
                "status": "error",
                "message": "OTP expired"
            })

        if record.otp != otp:
            return JsonResponse({
                "status": "error",
                "message": "Invalid OTP"
            })

        # ✅ OTP CORRECT → CREATE USER + LOGIN
        user = User.objects.create_user(
            username=email,
            email=email,
            password=signup_data["password"],
            first_name=signup_data["name"]
        )

        login(request, user)

        record.delete()
        del request.session["signup_data"]

        return JsonResponse({"status": "success"})

# ================= RESEND OTP =================
@require_POST
def resend_otp(request):

    signup_data = request.session.get("signup_data")
    if not signup_data:
        return JsonResponse({"error": "Session expired"}, status=400)

    email = signup_data["email"]
    otp = str(random.randint(100000, 999999))

    EmailOTP.objects.update_or_create(
        email=email,
        purpose="signup",
        defaults={"otp": otp}
    )

    threading.Thread(
        target=send_email_async,
        args=("Your abc.com Password Reset OTP", f"Your OTP is {otp}", email)
    ).start()


    return JsonResponse({"status": "success"})


# ========================== =============#
#         Forgat Password Views
# ========================== ============#

import random
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.shortcuts import render
from django.contrib.auth.models import User
from django.utils import timezone
from django.conf import settings
from django.core.mail import send_mail
from .models import EmailOTP


def forgot_page(request):
    return render(request, "forget.html")


@require_POST
def forgot_send_otp(request):
    email = request.POST.get("email", "").strip()

    if not email:
        return JsonResponse({"error": "Email required"}, status=400)

    if not User.objects.filter(email=email).exists():
        return JsonResponse({"error": "Email not registered"}, status=400)

    # delete old reset OTPs
    EmailOTP.objects.filter(email=email, purpose="reset").delete()

    otp = str(random.randint(100000, 999999))

    EmailOTP.objects.create(
        email=email,
        otp=otp,
        purpose="reset"
    )

    threading.Thread(
        target=send_email_async,
        args=("Your Password Reset OTP", f"Your OTP is {otp}", email)
    ).start()


    request.session["reset_email"] = email
    request.session["otp_verified"] = False

    return JsonResponse({"status": "otp_sent"})


@require_POST
def forgot_resend_otp(request):
    email = request.session.get("reset_email")

    if not email:
        return JsonResponse({"error": "Session expired"}, status=400)

    EmailOTP.objects.filter(email=email, purpose="reset").delete()

    otp = str(random.randint(100000, 999999))

    EmailOTP.objects.create(
        email=email,
        otp=otp,
        purpose="reset"
    )

    threading.Thread(
        target=send_email_async,
        args=("Forget Reset OTP", f"Your OTP is {otp}", email)
    ).start()


    return JsonResponse({"status": "otp_resent"})


@require_POST
def forgot_verify_otp(request):
    otp = request.POST.get("otp", "").strip()
    email = request.session.get("reset_email")

    if not email or not otp:
        return JsonResponse({"error": "Session expired"}, status=400)

    record = EmailOTP.objects.filter(
        email=email,
        purpose="reset"
    ).order_by("-id").first()

    if not record:
        return JsonResponse({"error": "OTP not found"}, status=400)

    if record.is_expired():
        record.delete()
        return JsonResponse({"error": "OTP expired"}, status=400)

    if record.attempts >= 5:
        record.delete()
        return JsonResponse({"error": "Too many attempts"}, status=403)

    if record.otp != otp:
        record.attempts += 1
        record.save()
        return JsonResponse({"error": "Invalid OTP"}, status=400)

    request.session["otp_verified"] = True
    record.delete()

    return JsonResponse({"status": "verified"})


@require_POST
def forgot_reset_password(request):
    if not request.session.get("otp_verified"):
        return JsonResponse({"error": "OTP not verified"}, status=403)

    password = request.POST.get("password", "")
    email = request.session.get("reset_email")

    if not password or len(password) < 8:
        return JsonResponse({"error": "Weak password"}, status=400)

    user = User.objects.get(email=email)
    user.set_password(password)
    user.save()

    request.session.flush()
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

    user.set_password(data.get("newPassword"))
    user.save()
    update_session_auth_hash(request, user)

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
