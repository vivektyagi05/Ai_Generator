from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.hashers import make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from PIL import Image, UnidentifiedImageError
from .models import ChatHistory, EmailOTP, PendingSignup, AIUsage
import json
from django.conf import settings
from django.utils.timezone import localtime
from datetime import date
from django.utils.timezone import now
from django.views.decorators.http import require_POST
from .models import UserProfile

# PHASE 7 backend feature-matrix re-audit: production-safe default
# avatar-upload policy (see profile_avatar()). Centralized here so the
# policy is defined once, not duplicated inline.
MAX_AVATAR_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB
ALLOWED_AVATAR_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
ALLOWED_AVATAR_PIL_FORMATS = {"JPEG", "PNG", "WEBP"}
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
    check_login_throttle,
    record_failed_login,
)



# ================= LOGIN =================
def user_login(request):
    # PHASE 4B Step 13: preserve "where the user was trying to go" (e.g.
    # /plans/) across the login redirect, without ever treating it as
    # authoritative state -- it's just a return URL, validated against
    # open-redirect before use, never a payment/plan/premium value.
    from django.utils.http import url_has_allowed_host_and_scheme

    def _safe_next(candidate):
        if candidate and url_has_allowed_host_and_scheme(
            candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            return candidate
        return None

    next_url = _safe_next(request.POST.get("next") or request.GET.get("next"))

    if request.method == "POST":
        email = (request.POST.get("email") or "").strip().lower()
        password = request.POST.get("password") or ""

        if not email or not password:
            return render(request, "login.html", {"error": "Email and password are required", "next": next_url or ""})

        # PHASE 9 — Step 3: this endpoint previously had NO throttling at
        # all -- unlimited password guesses against any account, bounded
        # only by whatever the network/browser allowed. Two independent
        # caps, mirroring the pattern already used for OTP/signup below:
        # per-IP (stops one source hammering many accounts) and per-email
        # (stops a distributed attempt hammering one account). Checked
        # BEFORE authenticate() so a locked-out attempt doesn't pay the
        # password-hashing cost either.
        if not check_ip_throttle(request, "login"):
            return render(request, "login.html", {
                "error": "Too many login attempts from this network. Please try again later.",
                "next": next_url or "",
            })

        if not check_login_throttle(email):
            return render(request, "login.html", {
                "error": "Too many failed attempts for this account. Please try again later.",
                "next": next_url or "",
            })

        user = authenticate(request, username=email, password=password)
        if user:
            # PHASE 8 ARCHITECTURE RECOVERY -- Priority 2: capture the
            # guest identity BEFORE login(), which rotates the session
            # key (cycle_key()) for session-fixation safety -- reading it
            # after login() would silently see the NEW key and find no
            # guest CreditAccount, migrating nothing.
            from accounts.services import guest_identity, credit_service

            guest_key = guest_identity.get_or_create_guest_key(request)
            login(request, user)
            credit_service.migrate_guest_credits_to_user(guest_key, user)
            return redirect(next_url or "home")

        record_failed_login(email)
        return render(request, "login.html", {"error": "Invalid email or password", "next": next_url or ""})

    return render(request, "login.html", {"next": next_url or ""})


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
@require_POST
def user_logout(request):
    """
    PHASE 8 ARCHITECTURE RECOVERY -- Priority 1 (logout root cause).

    Was previously a bare GET-accessible view reachable from a plain
    <a href="/logout/">. Root cause of the reported "clicking somewhere
    unrelated logs me out" class of bug: logout was a state-changing
    action reachable by *any* GET navigation to this URL -- not just an
    explicit user click on the real Logout link, but also link
    prefetching, a browser restoring/preloading a tab, or any future
    stray href="/logout/" anywhere in the app. A GET-triggerable logout
    is also a logout-CSRF vector (a third-party page can force it with a
    plain <img src="/logout/">-style request).

    Traced every href="/logout/" and {% url 'logout' %} usage in the
    project (2 occurrences, both in templates/partials/nav_app.html, both
    intentional) -- there was no *misplaced* second logout link causing
    this. The bug class is structural: the endpoint itself should never
    have accepted GET. Fixed at the root by requiring POST here and
    changing both nav_app.html logout controls from <a> to a small
    <form method="post"> (see that file), so logout can only ever happen
    via an explicit submit of the real Logout control -- never via
    ordinary link navigation, prefetch, or a stray/future href.
    """
    logout(request)
    return redirect('home')


# ================================
# SAVE CHAT HISTORY
# ================================
#
# PHASE 5 STEP 4: `feature` is optional and, if present, must be one of
# accounts.ai_request_config.ALLOWED_FEATURES -- exactly the same slug the
# generator already sent to /api/ai/ (main.html's saveHistorySafe() call
# sites now pass it through, see PHASE_5_AI_GENERATOR_FORENSIC_FINDINGS.md
# sec.5). An unrecognized or missing value is stored as "" (unknown), never
# guessed from `response` -- guessing at save time would just move the
# sniffing bug from read-time to write-time.
@login_required
def save_history(request):
    if request.method == "POST":
        data = json.loads(request.body)

        from .ai_request_config import ALLOWED_FEATURES
        from .history_input_schema import sanitize_inputs

        raw_feature = data.get("feature")
        feature = raw_feature if raw_feature in ALLOWED_FEATURES else ""

        # PHASE 8 STEP 2/3: `inputs` is optional and only ever whitelisted
        # against history_input_schema.GENERATOR_INPUT_FIELDS for the
        # given `feature` -- an unrecognized feature, a missing/malformed
        # `inputs`, or a client trying to smuggle extra keys all just
        # collapse to {} (see sanitize_inputs docstring). `query`/
        # `response` keep their existing, unrestricted behavior; this is
        # additive, not a replacement for them.
        inputs = sanitize_inputs(feature, data.get("inputs"))

        ChatHistory.objects.create(
            user=request.user,
            query=data.get("query"),
            response=data.get("response"),
            feature=feature,
            inputs=inputs,
        )
        return JsonResponse({"status": "ok"})

    return JsonResponse({"error": "POST required"}, status=405)


# ================================
# VIEW HISTORY PAGE
# ================================
#
# PHASE 5 STEP 4: display "type" now comes from the stored `feature`
# wherever it's known (every row saved after the feature field was added).
# The old content-sniffing heuristic is kept ONLY as a fallback for legacy
# rows saved before this field existed (feature == "") -- and those rows
# are explicitly flagged `is_legacy_classification=True` in the template
# context so the UI can (and does, see templates/history.html) tell the
# user the type is a best-effort guess, not stored fact. This is
# deliberately not a data migration that invents a feature for old rows --
# response text alone is not reliable evidence of which generator produced
# it (see PHASE_5_AI_GENERATOR_FORENSIC_FINDINGS.md sec.4).
_FEATURE_TO_DISPLAY_TYPE = {
    "photo": "image",
    "code": "code",
    "story": "text",
    "application": "text",
    "proposal": "text",
    "chat": "text",
}


def _legacy_sniff_type(response: str) -> str:
    response = response.lower()
    if response.startswith("http"):
        return "image"
    if "<" in response and ">" in response:
        return "code"
    return "text"


# PHASE 6 STEP 6.6 -- human-readable label + whether this row can be
# reopened into a generator. Only feature values that are actual
# generator tabs in main.html are reopenable ("chat" is a valid /api/ai/
# feature but, same as deep-linking in AI_GENERATORS/views.py, has no
# tab to reopen into).
_FEATURE_TO_LABEL = {
    "photo": "Photo",
    "code": "Code",
    "story": "Story",
    "application": "Application",
    "proposal": "Proposal",
    "chat": "Chat",
}
from .history_input_schema import REOPENABLE_FEATURES as _REOPENABLE_FEATURES


@login_required
def history_view(request):
    history_items = ChatHistory.objects.filter(user=request.user).order_by("-created_at")

    final_history = []

    for item in history_items:
        if item.feature:
            item.type = _FEATURE_TO_DISPLAY_TYPE.get(item.feature, "text")
            item.is_legacy_classification = False
            item.feature_label = _FEATURE_TO_LABEL.get(item.feature, item.feature)
            item.is_reopenable = item.feature in _REOPENABLE_FEATURES
        else:
            item.type = _legacy_sniff_type(item.response)
            item.is_legacy_classification = True
            item.feature_label = None
            # No feature on record means we genuinely don't know which
            # generator produced this row -- reopening it would mean
            # guessing, which is the same mistake the old content-sniffing
            # classifier made. Not reopenable, and the template says why.
            item.is_reopenable = False

        final_history.append(item)

    # PHASE 7 GOLDEN UI: history.html now extends base_app.html ->
    # nav_app.html, which reads active_nav to mark the current page in
    # the shared nav (aria-current).
    return render(request, "history.html", {"history": final_history, "active_nav": "history"})



@login_required
@require_POST
def delete_history(request, item_id):
    # PHASE 7 GOLDEN UI / security sweep: this view had NO method
    # restriction -- reachable via a plain GET, which Django's CSRF
    # middleware never checks (GET is exempt by design, since it's
    # meant to be a safe/idempotent method). Confirmed exploitable: a
    # bare `client.get(...)` from an authenticated session deleted the
    # row with no CSRF token at all -- meaning a third-party page could
    # trigger it with a plain <img src="...">/<link> against any logged-
    # in visitor, no form or token needed. The templates that call this
    # (history.html) already POST via a real <form> with {% csrf_token
    # %}, so this only closes the second, unintended GET path -- no
    # legitimate caller is affected.
    ChatHistory.objects.filter(id=item_id, user=request.user).delete()
    return redirect("history")


@login_required
@require_POST
def clear_history(request):
    # PHASE 7 GOLDEN UI / security sweep: same GET-based CSRF-bypass gap
    # as delete_history above, same fix, same reasoning.
    ChatHistory.objects.filter(user=request.user).delete()
    return redirect("history")


@login_required
def dashboard_view(request):
    """
    PHASE 8 STEP 5 -- User Dashboard.

    Audited first (per the Phase 8 forensic-baseline rule): no dashboard
    route/view/template existed anywhere in the project before this --
    confirmed by grepping urls.py/views.py for "dashboard" (zero
    matches). This is a genuine new page, built entirely from data
    services that already exist and are already trusted elsewhere in
    the app -- no new backend computation, no invented numbers:

      - credits: accounts.services.credit_service.get_balance_snapshot(),
        the exact same call profile_data() already makes.
      - entitlement: accounts.services.entitlement_service.
        get_entitlement_summary(), same as profile_data() and the
        generator workspace's own banner.
      - recent_generations: ChatHistory (successful generations only --
        save_history() is only ever called client-side after a
        generation actually succeeds, see main.html's
        runGeneratorLifecycle()), reusing the exact same
        feature_label/is_reopenable derivation history_view() uses, so
        a "Reopen" link here behaves identically to the one on the
        History page rather than being a second, divergent
        implementation.
      - recent_requests: AIUsage, which (unlike ChatHistory) records
        EVERY request outcome including failures/timeouts/denials -- the
        real, honest source for "recent activity" that doesn't quietly
        omit what went wrong. This is the "failed/recent generation
        visibility" Phase 8 sec.6 asks for; ChatHistory alone cannot
        provide it since it only ever contains successes.
      - generation_counts: a real GROUP BY over the user's own
        ChatHistory rows, not a hardcoded or estimated figure.
    """
    from django.db.models import Count

    from accounts.models import Subscription
    from .services import credit_service as _credit_service_dash
    from .services import entitlement_service as _entitlement_service_dash
    from .services import subscription_service

    user = request.user

    credit_account = _credit_service_dash.get_or_create_account(user=user)
    credit_snapshot = _credit_service_dash.get_balance_snapshot(credit_account)
    entitlement_summary = _entitlement_service_dash.get_entitlement_summary(user, None)

    # PHASE 8B Rule 6 -- same blindspot profile_data had before Item 2:
    # once entitlement falls back to FREE, nothing here explained why. Only
    # look it up when there's no live subscription backing the current
    # entitlement (an active Premium user never needs this).
    has_live_subscription = Subscription.objects.filter(
        user=user, status__in=Subscription.LIVE_STATUSES
    ).exists()
    lapsed_subscription = (
        None if has_live_subscription else subscription_service.get_lapsed_subscription_summary(user)
    )

    recent_generations = list(
        ChatHistory.objects.filter(user=user).order_by("-created_at")[:5]
    )
    for item in recent_generations:
        if item.feature:
            item.feature_label = _FEATURE_TO_LABEL.get(item.feature, item.feature)
            item.is_reopenable = item.feature in _REOPENABLE_FEATURES
        else:
            item.feature_label = None
            item.is_reopenable = False

    recent_requests = list(
        AIUsage.objects.filter(user=user).order_by("-created_at")[:8]
    )

    generation_counts = list(
        ChatHistory.objects.filter(user=user)
        .exclude(feature="")
        .values("feature")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    for row in generation_counts:
        row["label"] = _FEATURE_TO_LABEL.get(row["feature"], row["feature"])
    total_generations = ChatHistory.objects.filter(user=user).count()

    return render(
        request,
        "dashboard.html",
        {
            "credits": credit_snapshot,
            "entitlement": entitlement_summary,
            "lapsed_subscription": lapsed_subscription,
            "recent_generations": recent_generations,
            "recent_requests": recent_requests,
            "generation_counts": generation_counts,
            "total_generations": total_generations,
            "active_nav": "dashboard",
        },
    )


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

        # PHASE 8 ARCHITECTURE RECOVERY -- Priority 2: same reasoning as
        # user_login() above -- must read the guest identity before
        # login() rotates the session key.
        from accounts.services import guest_identity, credit_service

        guest_key = guest_identity.get_or_create_guest_key(request)
        login(request, user)
        credit_service.migrate_guest_credits_to_user(guest_key, user)

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
        # PHASE 7 GOLDEN UI: this page now extends base_app.html ->
        # nav_app.html, which reads active_nav to mark the current page
        # in the shared nav (aria-current). Not set before this session
        # because the page had no shared nav to highlight in.
        "active_nav": "profile",
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

    # PHASE 2: server-authoritative plan, replacing the previous hardcoded
    # "Free" literal. Frontend never computes this itself (Step 15).
    from accounts.services import entitlement_service, subscription_service
    entitlement_summary = entitlement_service.get_entitlement_summary(user, None)

    # PHASE 4B Step 10: reuse the existing Phase 1 credit ledger (the same
    # service accounts/views.py:credit_balance already reads) so
    # /profile/data/ carries one authoritative credit snapshot instead of
    # the frontend maintaining its own count. No second credit system.
    from accounts.services import credit_service as _credit_service
    credit_account = _credit_service.get_or_create_account(user=user)
    credit_snapshot = _credit_service.get_balance_snapshot(credit_account)

    # PHASE 3: backend-derived subscription snapshot, reusing the same
    # server-authoritative resolution the /api/subscription/ endpoint uses
    # (Step 20) -- never a second, divergent computation of plan/status.
    from accounts.models import Subscription
    live_subscription = (
        Subscription.objects.filter(user=user, status__in=Subscription.LIVE_STATUSES)
        .select_related("plan")
        .first()
    )
    if live_subscription is not None:
        live_subscription = subscription_service.sync_subscription(live_subscription)
        if live_subscription.status not in Subscription.LIVE_STATUSES:
            live_subscription = None

    if live_subscription is None:
        subscription_summary = {
            "plan": entitlement_summary["plan"],
            "status": "ACTIVE",
            "billing_interval": None,
            "current_period_end": None,
            "cancel_at_period_end": False,
        }
    else:
        subscription_summary = {
            "plan": live_subscription.plan.code,
            "status": live_subscription.status,
            "billing_interval": live_subscription.billing_interval,
            "current_period_end": live_subscription.current_period_end.isoformat()
            if live_subscription.current_period_end
            else None,
            "cancel_at_period_end": live_subscription.cancel_at_period_end,
        }

    # PHASE 8B Rule 5/6 — refund UX. See subscription_service.
    # get_lapsed_subscription_summary's docstring: this doesn't touch
    # subscription_summary's existing shape (no test/consumer relies on it
    # by equality, checked first) -- it's a new, additive key that's null
    # in the common case (never been Premium, or still live).
    lapsed_subscription = (
        subscription_service.get_lapsed_subscription_summary(user) if live_subscription is None else None
    )

    return JsonResponse({
        "name": user.get_full_name() or user.username,
        "email": user.email,
        "phone": profile.phone,
        "bio": profile.bio,
        "avatar": profile.avatar.url if profile.avatar else None,  # ✅ IMPORTANT
        "accountType": entitlement_summary["plan"].title(),
        "twoFactorEnabled": False,
        "stats": {
            "totalChats": chats.count(),
            "daysActive": days_active,
            "satisfaction": 100
        },
        "activities": activities,
        "entitlement": entitlement_summary,
        "subscription": subscription_summary,
        "lapsed_subscription": lapsed_subscription,
        "credits": credit_snapshot,
    })


def plans_page(request):
    """
    PHASE 2 Step 14 / PHASE 4B Step 2 — public pricing page, genuinely
    backed by the Plan/PlanFeatureRule catalog (never a separate hardcoded
    HTML matrix) plus accounts/billing_config.py, the same authoritative
    price source accounts/services/payment_service.py resolves from before
    ever creating a Razorpay order. The template never invents a price.
    """
    from accounts.models import Plan, PlanFeatureRule, Subscription
    from accounts import billing_config
    from accounts.services import entitlement_service, subscription_service

    plans = Plan.objects.filter(is_active=True, is_public=True).order_by("display_order", "code")
    rules = (
        PlanFeatureRule.objects.filter(plan__in=plans)
        .select_related("feature")
        .order_by("feature__code")
    )
    rules_by_plan = {}
    for rule in rules:
        rules_by_plan.setdefault(rule.plan_id, []).append(rule)

    def _pricing_for(plan_code: str):
        if not billing_config.is_payable_plan(plan_code):
            return None
        pricing = {}
        for interval in Subscription.BillingInterval.values:
            try:
                amount_paise, currency = billing_config.resolve_price(plan_code, interval)
            except billing_config.UnpayablePlanError:
                continue
            pricing[interval] = {
                "amount_paise": amount_paise,
                "currency": currency,
                "amount_display": f"₹{amount_paise / 100:,.0f}",
            }
        return pricing or None

    plan_rows = [
        {
            "plan": plan,
            "rules": rules_by_plan.get(plan.id, []),
            "pricing": _pricing_for(plan.code),
        }
        for plan in plans
    ]

    # Current user's real plan/subscription state (Step 2: never show a
    # "Buy Premium" CTA to someone already on that plan, and never let an
    # anonymous visitor's page imply a plan they don't have).
    current_plan_code = None
    live_subscription = None
    if request.user.is_authenticated:
        current_plan_code = entitlement_service.get_current_plan(request.user, None)
        live_subscription = (
            Subscription.objects.filter(user=request.user, status__in=Subscription.LIVE_STATUSES)
            .select_related("plan")
            .first()
        )
        if live_subscription is not None:
            live_subscription = subscription_service.sync_subscription(live_subscription)
            if live_subscription.status not in Subscription.LIVE_STATUSES:
                live_subscription = None

    return render(
        request,
        "plans.html",
        {
            "plan_rows": plan_rows,
            "current_plan_code": current_plan_code,
            "live_subscription": live_subscription,
            # PHASE 7 GOLDEN UI: plans.html now extends base_app.html ->
            # nav_app.html, which reads active_nav to mark the current
            # page in the shared nav (aria-current).
            "active_nav": "plans",
        },
    )


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

    from accounts.services import account_deletion_service

    # PHASE 9 — Step 7: previously called user.delete() here, which
    # CASCADE-deleted CreditTransaction/AIUsage/Subscription/
    # SubscriptionAuditLog/Payment (real Razorpay payment records) along
    # with the User row -- a user's own password was enough to permanently
    # erase their entire financial/audit history. account_deletion_service
    # anonymizes + deactivates instead: the User row (and everything that
    # references it) stays, so that history survives; login becomes
    # impossible; any live subscription is cancelled through the existing
    # subscription_service (never duplicated here); every active session
    # for this user, not just the current one, is revoked. See that
    # module's docstring for the full account of what is and isn't
    # touched, and why.
    account_deletion_service.delete_account(user)

    # request.session.flush() ends THIS session specifically (also covered
    # by delete_account()'s own session sweep, but flushing the current
    # one directly here means the current request's session cookie is
    # rotated/cleared immediately, not just the DB-side session row).
    request.session.flush()
    return JsonResponse({"status": "deleted"})

@login_required
@require_POST
def profile_avatar(request):
    avatar = request.FILES.get("avatar")

    if not avatar:
        return JsonResponse({"error": "No file uploaded"}, status=400)

    # PHASE 7 backend feature-matrix re-audit: this endpoint previously
    # only checked that a file was present -- no type check, no size
    # cap, and the model's ImageField "is this actually an image" Pillow
    # validation does NOT run on a direct .save() call the way this view
    # makes it (only on ModelForm.is_valid()/full_clean()), so that
    # protection was never actually active. Fixed with real,
    # content-level validation below -- production-safe default policy
    # (JPG/PNG/WebP, 5 MB cap) since the project's existing docs don't
    # define a stricter one (checked: no MAX_AVATAR / avatar policy
    # constant existed anywhere in the repo before this).
    if avatar.size > MAX_AVATAR_UPLOAD_BYTES:
        return JsonResponse(
            {"error": "Image must be 5 MB or smaller."}, status=400
        )

    # Content-Type is client-supplied and trivially spoofable (a renamed
    # .exe with a forged "Content-Type: image/png" header would pass
    # this alone) -- it's a fast, cheap pre-filter only. The real
    # boundary is the Pillow decode below, which reads actual pixel
    # data, not metadata either side can lie about.
    if (avatar.content_type or "").lower() not in ALLOWED_AVATAR_CONTENT_TYPES:
        return JsonResponse(
            {"error": "Only JPG, PNG, and WebP images are allowed."}, status=400
        )

    try:
        probe = Image.open(avatar)
        probe.verify()  # raises on truncated/corrupt/non-image data
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        # PHASE 9 — Step 8: Image.DecompressionBombError is a plain
        # Exception subclass (NOT an OSError/ValueError), so it was not
        # actually caught here before -- a decompression-bomb image (huge
        # declared pixel dimensions in a tiny file) would raise past this
        # handler and 500 instead of a clean 400. Pillow's own
        # MAX_IMAGE_PIXELS guard (~89M px) already stops it from actually
        # decompressing into memory either way; this only fixes the
        # response shape, not a real memory-exhaustion hole.
        return JsonResponse(
            {"error": "That file isn't a valid image."}, status=400
        )

    # verify() leaves the Image object unusable for further reads and
    # does not itself always populate .format reliably post-verify --
    # reopen a fresh handle from the start of the file to check the
    # real detected format.
    avatar.seek(0)
    detected_format = Image.open(avatar).format
    if detected_format not in ALLOWED_AVATAR_PIL_FORMATS:
        return JsonResponse(
            {"error": "Only JPG, PNG, and WebP images are allowed."}, status=400
        )
    avatar.seek(0)

    profile, _ = UserProfile.objects.get_or_create(user=request.user)

    # Delete the previous avatar file from storage before saving the new
    # one -- FileField.save() does not do this automatically; leaving it
    # would silently accumulate orphaned files under media/avatars/ on
    # every re-upload.
    if profile.avatar:
        profile.avatar.delete(save=False)

    profile.avatar = avatar
    profile.save()

    # .url is a media URL (e.g. /media/avatars/xyz.png), never a
    # filesystem path -- nothing server-local is exposed here.
    return JsonResponse({
        "status": "uploaded",
        "url": profile.avatar.url,
    })


# ============================================================================
# PHASE 1 CREDIT ENGINE -- Step 14: read-only balance endpoint
# ----------------------------------------------------------------------------
# Returns the caller's authoritative balance from CreditAccount via
# credit_service.get_balance_snapshot(). Never computed from anything the
# client sends -- identity comes from the session (authenticated user, or
# server-issued guest session key), exactly like the AI endpoint.
# This does NOT create an account for a guest who has never made an AI
# request (no reason to hand out a free grant just for checking a balance
# that doesn't exist yet); authenticated users, however, do get an account
# here if they somehow don't have one, since it's 1:1 with their identity.
# ============================================================================
from .services import credit_service as _credit_service
from .services.guest_identity import identify_requester as _identify_requester


def credit_balance(request):
    user, guest_key = _identify_requester(request)

    if user is not None:
        account = _credit_service.get_or_create_account(user=user)
    else:
        account = _credit_service.get_account(guest_key=guest_key)
        if account is None:
            return JsonResponse({
                "balance": 0,
                "reserved_balance": 0,
                "available_balance": 0,
                "lifetime_earned": 0,
                "lifetime_used": 0,
            })

    return JsonResponse(_credit_service.get_balance_snapshot(account))
