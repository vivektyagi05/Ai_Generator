from django.db import models
from django.utils import timezone
from datetime import timedelta

class ChatHistory(models.Model):
    user = models.ForeignKey("auth.User", on_delete=models.CASCADE)
    query = models.TextField()
    response = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

from django.db import models
from django.utils import timezone
from datetime import timedelta

class EmailOTP(models.Model):
    PURPOSE_CHOICES = (
        ("reset", "Password Reset"),
        ("signup", "Signup"),
    )

    email = models.EmailField()
    otp = models.CharField(max_length=6)
    purpose = models.CharField(max_length=10, choices=PURPOSE_CHOICES)

    attempts = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["email", "purpose"],
                name="accounts_emailotp_one_active_per_email_purpose",
            ),
        ]

    def is_expired(self):
        return timezone.now() > self.created_at + timedelta(minutes=5)

    def __str__(self):
        return f"{self.email} - {self.purpose}"


class PendingSignup(models.Model):
    """
    Server-side holding record for a signup that hasn't completed OTP
    verification yet.

    Replaces storing the raw password in request.session["signup_data"]:
    the password is hashed with Django's password hasher *once*, here, at
    signup time, and only that hash — never the plaintext — is ever
    persisted or referenced from the session. The session only carries the
    email as a pointer to this record.
    """

    email = models.EmailField(unique=True)
    name = models.CharField(max_length=150)
    password_hash = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    def is_expired(self):
        return timezone.now() > self.created_at + timedelta(minutes=15)

    def __str__(self):
        return f"pending signup: {self.email}"



from django.contrib.auth.models import User

class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    avatar = models.ImageField(upload_to="avatars/", blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True)
    bio = models.TextField(blank=True)

    def __str__(self):
        return self.user.username