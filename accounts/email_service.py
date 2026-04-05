from django.core.mail import send_mail
from django.conf import settings

def send_email_async(subject, message, to_email):

    from django.core.mail import send_mail

    try:
        send_mail(
            subject,
            message,
            settings.EMAIL_HOST_USER,
            [to_email],
            fail_silently=False,
        )
        print("✅ EMAIL SENT SUCCESS")

    except Exception as e:
        print("❌ EMAIL ERROR:", str(e))