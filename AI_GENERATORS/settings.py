from pathlib import Path
import logging
import os
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# PHASE 8A — forensic audit item: a key pasted into .env with surrounding
# whitespace or accidental quote characters (e.g. GROQ_API_KEY="gsk_..." or
# GROQ_API_KEY=gsk_...\n with a trailing newline from some shells) used to
# be sent to Groq byte-for-byte, producing an auth failure that looked
# identical to a missing key. Normalize once, here, so every caller
# (AI_GENERATORS/api_views.py, any future health-check) sees the same,
# already-clean value.
_raw_groq_key = os.getenv("GROQ_API_KEY")
if _raw_groq_key is not None:
    _raw_groq_key = _raw_groq_key.strip()
    if len(_raw_groq_key) >= 2 and _raw_groq_key[0] == _raw_groq_key[-1] and _raw_groq_key[0] in ("'", '"'):
        _raw_groq_key = _raw_groq_key[1:-1].strip()
GROQ_API_KEY = _raw_groq_key or ""

SECRET_KEY = os.getenv("SECRET_KEY")

if not SECRET_KEY:
    raise Exception("SECRET_KEY missing")

DEBUG = os.getenv("DEBUG") == "True"

ALLOWED_HOSTS = ['127.0.0.1', '.onrender.com']

# Cache — used by accounts/rate_limit.py for OTP send cooldowns/windows,
# per-IP throttling, and the AI generation rate limiter. accounts/rate_limit.py
# talks only to Django's cache API (cache.get/set/incr), so it needs zero
# code changes to become production-safe here.
#
# PHASE 9 — Step 2: LocMemCache is per-process. With multiple Gunicorn
# workers (this app's Procfile runs several), each worker keeps its own
# counters, so the *effective* limit becomes (configured limit) x (worker
# count) and a client can partially evade a single worker's cooldown by
# landing on another. That's fine for local dev (single process) but not
# for a real multi-worker deployment.
#
# Fix: if REDIS_URL is set, use Django's built-in Redis cache backend
# (no extra dependency needed — django.core.cache.backends.redis.RedisCache
# has shipped in Django since 4.0) so all workers/instances share one set
# of counters. If it isn't set, fall back to LocMemCache so local
# development and CI keep working with zero setup, and log a one-time
# warning in production so the gap is visible instead of silent.
REDIS_URL = os.getenv("REDIS_URL", "")

if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        }
    }
    if not DEBUG:
        logging.getLogger("accounts").warning(
            "REDIS_URL is not set: rate limiting is running on a "
            "per-process LocMemCache in a non-DEBUG environment. If more "
            "than one worker/instance serves traffic, OTP/login/AI rate "
            "limits are effectively multiplied by the process count. Set "
            "REDIS_URL to share counters across processes."
        )

# Email is now sent via the Brevo transactional API (see accounts/email/),
# not Django's SMTP backend. Configuration is loaded directly from
# environment variables by accounts/email/config.py — see .env.example for
# the required EMAIL_PROVIDER / BREVO_API_KEY / EMAIL_FROM / EMAIL_FROM_NAME.

# PHASE 4 — Razorpay payment configuration. Loaded from the environment
# ONLY -- never hardcoded, never committed (see .env.example). These are
# read here (not directly via os.getenv in accounts/services/razorpay_client.py)
# so every other module always goes through Django settings the same way it
# already does for GROQ_API_KEY/SECRET_KEY above, and so tests can override
# them with Django's @override_settings instead of mutating the environment.
#
# Deliberately not validated with `raise Exception(...)` at import time the
# way SECRET_KEY is above: unlike SECRET_KEY, a missing Razorpay credential
# should not prevent the whole site (login, AI generators, etc.) from
# starting up -- only the billing endpoints need it, and they fail safely
# (503, no secret ever logged) at the moment they're actually called if it's
# absent. See accounts/services/razorpay_client.py:get_client().
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")

# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',


    # local apps
    'accounts.apps.AccountsConfig',
]

LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
]

ROOT_URLCONF = 'AI_GENERATORS.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],  # ✅ Custom templates directory
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'AI_GENERATORS.wsgi.application'


# Database
# https://docs.djangoproject.com/en/3.2/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}


# Password validation
# https://docs.djangoproject.com/en/3.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/3.2/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'Asia/Kolkata'   # ✅ Better for Indian region

USE_I18N = True

USE_L10N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/3.2/howto/static-files/

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'  # For collectstatic in production
# PHASE 4B: project-root static/ (css/js for pricing + profile billing UI),
# same convention as TEMPLATES['DIRS'] above -- no app has its own static/
# directory, so without this the staticfiles finder would never see it.
STATICFILES_DIRS = [BASE_DIR / 'static']

# Default primary key field type
# https://docs.djangoproject.com/en/3.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = False

# In production (behind Render/whatever reverse proxy terminates TLS),
# also mark cookies Secure so they're never sent over plain HTTP.
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# PHASE 9 — Step 1: SECURE_SSL_REDIRECT / HSTS / proxy header.
#
# Left OFF by default even when DEBUG=False, because whether these are
# correct depends on deployment topology, not just on being "in
# production": if TLS terminates at a load balancer/reverse proxy that
# already redirects HTTP->HTTPS (Render's default routing does), Django
# also redirecting can create a redirect loop unless
# SECURE_PROXY_SSL_HEADER is set to trust the proxy's forwarded-proto
# header. Getting SECURE_PROXY_SSL_HEADER wrong (trusting a header an
# attacker can spoof directly) is worse than the check --deploy warning it
# silences, so this is opt-in via env rather than silently enabled.
#
# Set BEHIND_TLS_PROXY=True once confirmed the deployment's proxy (a) sets
# X-Forwarded-Proto itself, and (b) cannot be reached by clients directly
# (bypassing the proxy) — otherwise a client could spoof the header and
# defeat the redirect logic it's meant to enforce.
if not DEBUG and os.getenv("BEHIND_TLS_PROXY") == "True":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True


# ── Logging ──────────────────────────────────────────────────────────────
#
# Every logger under accounts.* (email service, providers, retry, views)
# uses the standard `logging` module with `extra={...}` fields — see
# accounts/email/service.py, retry.py, providers/brevo_provider.py.
#
# Dev: human-readable, includes request_id/status_code/latency/etc via a
# formatter that prints `extra` fields inline.
# Prod (DEBUG=False): still stdout/stderr (Render/containers capture that
# directly — no log files, no extra logging dependency needed), but a
# flatter one-line-per-record format that's easy to grep/ingest.
#
# NEVER log: OTP values, passwords, API keys, SECRET_KEY, session/CSRF
# tokens, or Authorization headers. Only operationally useful fields
# (request_id, recipient, status_code, error type, latency) are logged —
# see the call sites for what's actually passed in `extra`.

class _ExtraFieldsFormatter(logging.Formatter):
    """Appends any non-standard `extra={...}` fields to the log line."""

    _STANDARD_KEYS = {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "message", "taskName",
    }

    def format(self, record):
        base = super().format(record)
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in self._STANDARD_KEYS
        }
        if not extras:
            return base
        extras_str = " ".join(f"{k}={v!r}" for k, v in sorted(extras.items()))
        return f"{base} | {extras_str}"


LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "dev": {
            "()": f"{__name__}._ExtraFieldsFormatter",
            "format": "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        },
        "prod": {
            "()": f"{__name__}._ExtraFieldsFormatter",
            "format": "%(asctime)s level=%(levelname)s logger=%(name)s msg=%(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "dev" if DEBUG else "prod",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO" if DEBUG else "WARNING",
            "propagate": False,
        },
        "accounts": {
            "handlers": ["console"],
            "level": "DEBUG" if DEBUG else "INFO",
            "propagate": False,
        },
    },
}
