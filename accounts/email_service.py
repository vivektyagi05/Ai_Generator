import requests
from django.conf import settings

RESEND_URL = "https://api.resend.com/emails"

def send_email_async(subject, message, to_email):

    print("🔥 RESEND KEY:", settings.RESEND_API_KEY)

    try:
        headers = {
            "Authorization": f"Bearer {settings.RESEND_API_KEY}",
            "Content-Type": "application/json"
        }

        payload = {
            "from": settings.DEFAULT_FROM_EMAIL,
            "to": [to_email],
            "subject": subject,
            "html": f"<p>{message}</p>"
        }

        res = requests.post(
            RESEND_URL,
            headers=headers,
            json=payload,
            timeout=10
        )

        print("🔥 EMAIL RESPONSE:", res.text)

    except Exception as e:
        print("🔥 Email Error:", e)
