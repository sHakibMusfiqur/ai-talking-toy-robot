import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "insecure-development-only-set-dj-secret-key")

DEBUG = os.environ.get("DJANGO_DEBUG", "false").lower() in ("1", "true", "yes")

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get(
        "DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver"
    ).split(",")
    if host.strip()
]

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "django.contrib.sessions",
    "django.contrib.messages",
    "core.apps.MongoContentTypesConfig",
    "core.apps.MongoAuthConfig",
    "rest_framework",
    "core.apps.CoreConfig",
    "accounts.apps.AccountsConfig",
]

MIGRATION_MODULES = {
    "auth": "mongo_migrations.auth",
    "contenttypes": "mongo_migrations.contenttypes",
}

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
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django_mongodb_backend",
        "NAME": os.environ.get("MONGO_DB_NAME", "ai_talking_toy"),
        "HOST": os.environ.get(
            "MONGO_HOST", "mongodb://localhost:27017/?directConnection=true"
        ),
    }
}

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "en"

TIME_ZONE = "Asia/Dhaka"

USE_I18N = True

USE_TZ = True

STATIC_URL = "static/"

DEFAULT_AUTO_FIELD = "django_mongodb_backend.fields.ObjectIdAutoField"

LOGIN_URL = "/login/"

LOGIN_REDIRECT_URL = "/"

LOGOUT_REDIRECT_URL = "/login/"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "core.permissions.AuthRequired",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "EXCEPTION_HANDLER": "core.exceptions.api_exception_handler",
}
