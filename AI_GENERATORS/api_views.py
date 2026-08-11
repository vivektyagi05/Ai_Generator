# AI_GENERATORS/api_views.py

import json
import requests
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings


GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


@csrf_exempt
def gemini_api(request):

    if not settings.GROQ_API_KEY:
        return JsonResponse({"error": "API key missing"}, status=500)

    
    if request.method != "POST":
        return JsonResponse({"error": "Invalid request"}, status=405)

    body = json.loads(request.body)
    prompt = body.get("prompt", "").strip()

    headers = {
        "Authorization": f"Bearer {settings.GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.4,
        "max_tokens": 1024
    }

    for attempt in range(2):  # 🔁 retry 2 times
        try:
            res = requests.post(
                GROQ_URL,
                headers=headers,
                json=payload,
                timeout=15   # 🔥 shorter timeout
            )
            data = res.json()

            if "error" in data:
                return JsonResponse({"error": data["error"]}, status=500)

            return JsonResponse({
                "result": data["choices"][0]["message"]["content"]
            })

        except (ConnectionError, Timeout):
            if attempt == 1:
                return JsonResponse({
                    "error": "AI server busy. Please try again in a moment."
                }, status=503)

    return JsonResponse({"error": "Unexpected failure"}, status=500)