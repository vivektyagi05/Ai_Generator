# AI_GENERATORS/urls.py

from django.contrib import admin
from django.urls import path, include
from . import views, api_views

from django.conf import settings
from django.conf.urls.static import static


urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("accounts.urls")),
    path("home/", views.create, name="home"),
    # 🔥 PROFILE ROUTES (Aapke Backend Functions Ke According)

    # 🔥 SINGLE AI ENDPOINT
    path("api/ai/", api_views.ai_generate),
]

urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
