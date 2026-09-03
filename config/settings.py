"""
Django settings for the agentic resume-generation app (config project).

Local-first, single-operator (see requirements.md Section 1/2). Secrets are read from environment
variables / a local .env file, never hardcoded or committed (requirements.md Section 13, NFR-003).
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env (if present) into the process environment before reading any setting below.
load_dotenv(BASE_DIR / ".env")

# True only under `manage.py test` (or a test runner invoking manage.py with "test" as the
# subcommand). Used exactly once, by `llm_provider.adapters.get_adapter_for_stage`, to refuse
# routing a real pipeline stage to a FAKE provider outside an automated test run -- the FAKE
# adapter type exists for deterministic tests only, never as an accidentally-left-configured
# production routing target. Tests that need a FakeAdapter without this flag (e.g. exercising the
# guard itself) inject the adapter explicitly rather than relying on this flag being False.
TESTING = "test" in sys.argv


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


DEBUG = _env_bool("DJANGO_DEBUG", default=True)

# SECURITY WARNING: keep the secret key used in production secret!
# A fixed development-only fallback is used when DEBUG is on and no key is configured, so a fresh
# checkout works immediately; production (DEBUG=False) requires DJANGO_SECRET_KEY to be set.
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "django-insecure-dev-only-key-do-not-use-in-production"
    else:
        raise RuntimeError(
            "DJANGO_SECRET_KEY must be set in the environment when DJANGO_DEBUG is not enabled."
        )

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Pipeline apps (see docs/ARCHITECTURE.md Section 2 for each app's responsibility).
    "llm_provider",
    "candidate_memory",
    "job_intake",
    "candidate_matching",
    "resume_builder",
    "reviews",
    "job_applications",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# Database
# https://docs.djangoproject.com/en/5.1/ref/settings/#databases
#
# PostgreSQL only (requirements.md Section 11 / STACK-002) -- run locally via docker-compose.yml.
# No sqlite fallback: the durable system of record for stage outputs, review state, and the
# LLM call audit log is Postgres from day one, per requirements.md Section 11.

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "cvbuilder"),
        "USER": os.environ.get("POSTGRES_USER", "cvbuilder"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "cvbuilder"),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}


# Password validation
# https://docs.djangoproject.com/en/5.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# Internationalization
# https://docs.djangoproject.com/en/5.1/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.1/howto/static-files/

STATIC_URL = "static/"

# Default primary key field type
# https://docs.djangoproject.com/en/5.1/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
