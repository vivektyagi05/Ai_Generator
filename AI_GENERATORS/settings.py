from pathlib import Path
import logging
import os
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

SECRET_KEY = os.getenv("SECRET_KEY")

if not SECRET_KEY:
    raise Exception("SECRET_KEY missing")

DEBUG = os.getenv("DEBUG") == "True"

ALLOWED_HOSTS = ['127.0.0.1', '.onrender.com']

# Cache — used by accounts/rate_limit.py for OTP send cooldowns/windows and
# per-IP throttling. LocMemCache (Django's default when CACHES isn't set)
# is per-process; see accounts/rate_limit.py's module docstring for the
# production implication of that with multiple gunicorn workers. Explicit
# here so the choice is visible rather than implicit.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

# Email is now sent via the Brevo transactional API (see accounts/email/),
# not Django's SMTP backend. Configuration is loaded directly from
# environment variables by accounts/email/config.py — see .env.example for
# the required EMAIL_PROVIDER / BREVO_API_KEY / EMAIL_FROM / EMAIL_FROM_NAME.

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
