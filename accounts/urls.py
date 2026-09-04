from django.urls import path
from . import views, entitlement_views, subscription_views, billing_views, webhook_views
from django.shortcuts import redirect



urlpatterns = [
    path('', lambda request: redirect('home')),

    path('login/', views.user_login, name='login'),
    path('signup/', views.user_signup, name='signup'),
    path("verify-otp/", views.verify_otp, name="verify_otp"),
    path('logout/', views.user_logout, name='logout'),
    path("resend-otp/", views.resend_otp, name="resend_otp"),


    # save history (AJAX)
    path('save_history/', views.save_history, name='save_history'),

    # show history page
    path("history/", views.history_view, name="history"),
    path("history/delete/<int:item_id>/", views.delete_history, name="delete_history"),
    path("history/clear/", views.clear_history, name="clear_history"),

    # PHASE 8 STEP 5 -- User Dashboard
    path("dashboard/", views.dashboard_view, name="dashboard"),

    # password reset
    path("forgot/", views.forgot_page, name="forgot"),
    path("forgot/send-otp/", views.forgot_send_otp, name="forgot_send_otp"),
    path("forgot/resend-otp/", views.forgot_resend_otp, name="forgot_resend_otp"),
    path("forgot/verify-otp/", views.forgot_verify_otp, name="forgot_verify_otp"),
    path("forgot/reset-password/", views.forgot_reset_password, name="forgot_reset_password"),

    # PHASE 1 credit engine -- read-only balance (Step 14)
    path("credits/balance/", views.credit_balance, name="credit_balance"),

    # PHASE 2 entitlement & plan engine -- read-only APIs (Step 12/13)
    path("api/entitlements/", entitlement_views.entitlements_view, name="entitlements"),
    path("api/plans/", entitlement_views.plans_view, name="plans"),
    path("api/ai/costs/", entitlement_views.costs_view, name="ai_costs"),
    path("plans/", views.plans_page, name="plans_page"),

    # PHASE 3 subscription & billing domain -- read + user-initiated
    # cancel/restore only; no client-facing activate endpoint exists
    # (see accounts/subscription_views.py docstring).
    path("api/subscription/", subscription_views.subscription_view, name="subscription"),
    path("api/subscription/cancel/", subscription_views.subscription_cancel_view, name="subscription_cancel"),
    path("api/subscription/restore/", subscription_views.subscription_restore_view, name="subscription_restore"),

    # PHASE 4 -- real Razorpay payment integration. create-order/verify-payment
    # are authenticated, client-facing, and never accept amount/status from
    # the client (see accounts/billing_views.py docstring). The webhook is
    # the only path that can activate a subscription from a payment (see
    # accounts/webhook_views.py docstring).
    path("api/billing/create-order/", billing_views.create_order_view, name="billing_create_order"),
    path("api/billing/verify-payment/", billing_views.verify_payment_view, name="billing_verify_payment"),
    path("api/billing/payment-status/<str:order_id>/", billing_views.payment_status_view, name="billing_payment_status"),
    path("api/webhooks/razorpay/", webhook_views.razorpay_webhook_view, name="razorpay_webhook"),

    # 🔥 PROFILE ROUTES (Aapke Backend Functions Ke According)
     path("profile/", views.profile_page, name="profile"),
    path("profile/data/", views.profile_data, name="profile_data"),
    path("profile/update/", views.profile_update, name="profile_update"),
    path("profile/change-password/", views.profile_change_password, name="profile_change_password"),
    path("profile/delete/", views.profile_delete, name="profile_delete"),
    path("profile/avatar/", views.profile_avatar, name="profile_avatar"),
            
]



