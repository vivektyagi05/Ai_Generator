from django.urls import path
from . import views
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

    # password reset
    path("forgot/", views.forgot_page, name="forgot"),
    path("forgot/send-otp/", views.forgot_send_otp, name="forgot_send_otp"),
    path("forgot/resend-otp/", views.forgot_resend_otp, name="forgot_resend_otp"),
    path("forgot/verify-otp/", views.forgot_verify_otp, name="forgot_verify_otp"),
    path("forgot/reset-password/", views.forgot_reset_password, name="forgot_reset_password"),

    # 🔥 PROFILE ROUTES (Aapke Backend Functions Ke According)
     path("profile/", views.profile_page, name="profile"),
    path("profile/data/", views.profile_data, name="profile_data"),
    path("profile/update/", views.profile_update, name="profile_update"),
    path("profile/change-password/", views.profile_change_password, name="profile_change_password"),
    path("profile/delete/", views.profile_delete, name="profile_delete"),
    path("profile/avatar/", views.profile_avatar, name="profile_avatar"),
            
]



