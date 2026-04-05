from django.core.mail import send_mail
from django.conf import settings

import requests
from django.conf import settings

def send_email_async(subject, message, to_email):
    print("🔥 EMAIL FUNCTION STARTED")

    try:
        res = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {settings.RESEND_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "from": "onboarding@resend.dev",
                "to": [to_email],
                "subject": subject,
                "html": f"<p>{message}</p>"
            },
            timeout=10
        )

        print("✅ EMAIL SENT:", res.text)

    except Exception as e:
        print("❌ EMAIL ERROR:", e)