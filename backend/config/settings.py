import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_pytest_mqtt_env():
    """Phase 8 tests talk to the real Mosquitto broker, so MQTT_* credentials
    come from the repo-root .env when running under pytest (Compose passes
    them as environment in containerized runs; plain manage.py runs are
    unchanged). Only MQTT_* keys, only as defaults, only under pytest."""
    if "pytest" not in sys.modules:
        return
    env_file = BASE_DIR.parent / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _value = line.split("=", 1)
        key = key.strip()
        if key.startswith("MQTT_"):
            os.environ.setdefault(key, _value.strip())


_load_pytest_mqtt_env()

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
    "devices.apps.DevicesConfig",
    "conversations.apps.ConversationsConfig",
    "memory.apps.MemoryConfig",
    "configuration.apps.ConfigurationConfig",
    "firmware.apps.FirmwareConfig",
    "safety.apps.SafetyConfig",
    "analytics.apps.AnalyticsConfig",
    "mqtt_bridge.apps.MqttBridgeConfig",
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
    # LOCAL UI TESTING ONLY: no-op unless DEBUG + explicit env opt-in +
    # loopback request all hold (see core.local_ui_bypass).
    "core.local_ui_bypass.LocalUiTestBypassMiddleware",
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
# Web control panel assets (Tailwind build output + JS), discovery only —
# serving follows the normal Django DEBUG/dev-server rule.
STATICFILES_DIRS = [BASE_DIR / "static"]
# collectstatic validation target (git-ignored); no static *hosting* is
# configured here — production serving belongs to the deployment phase.
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django_mongodb_backend.fields.ObjectIdAutoField"

LOGIN_URL = "/login/"

# Device pairing codes (Phase 6): configuration-driven, never scattered
# as magic constants. Codes are short-lived and single-use (docs/01 Phase 6).
DEVICE_PAIRING_CODE_LENGTH = int(os.environ.get("DEVICE_PAIRING_CODE_LENGTH", "8"))
DEVICE_PAIRING_CODE_TTL_SECONDS = int(
    os.environ.get("DEVICE_PAIRING_CODE_TTL_SECONDS", "900")
)

# Device access tokens (Phase 7, AR-10): short-lived signed-challenge → JWT
# exchange. Secret is env-configured (rotate independently of Django's
# SECRET_KEY); falls back to SECRET_KEY so dev/test work out of the box.
DEVICE_JWT_SECRET = os.environ.get("DEVICE_JWT_SECRET", SECRET_KEY)
DEVICE_JWT_ALGORITHM = os.environ.get("DEVICE_JWT_ALGORITHM", "HS256")
DEVICE_JWT_TTL_SECONDS = int(os.environ.get("DEVICE_JWT_TTL_SECONDS", "3600"))
DEVICE_JWT_ISSUER = os.environ.get("DEVICE_JWT_ISSUER", "ai-talking-toy-backend")
DEVICE_JWT_AUDIENCE = os.environ.get("DEVICE_JWT_AUDIENCE", "ai-talking-toy-robot")
DEVICE_CHALLENGE_TTL_SECONDS = int(
    os.environ.get("DEVICE_CHALLENGE_TTL_SECONDS", "60")
)

# MQTT bridge (Phase 8, Section 11/12.2): the django-service client's exact
# publish/subscribe rights live in infra/mosquitto role definitions; admin
# credentials only provision dynsec users (device password = its JWT).
MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_BRIDGE_USERNAME = os.environ.get("MQTT_BRIDGE_USERNAME", "django-service")
MQTT_BRIDGE_PASSWORD = os.environ.get("MQTT_BRIDGE_PASSWORD", "")
MQTT_ADMIN_USERNAME = os.environ.get("MQTT_ADMIN_USERNAME", "admin")
MQTT_ADMIN_PASSWORD = os.environ.get("MQTT_ADMIN_PASSWORD", "")
MQTT_RECONNECT_MIN_DELAY_SECONDS = int(
    os.environ.get("MQTT_RECONNECT_MIN_DELAY_SECONDS", "1")
)
MQTT_RECONNECT_MAX_DELAY_SECONDS = int(
    os.environ.get("MQTT_RECONNECT_MAX_DELAY_SECONDS", "60")
)
MQTT_CONNECT_TIMEOUT_SECONDS = float(
    os.environ.get("MQTT_CONNECT_TIMEOUT_SECONDS", "10")
)
MQTT_PUBLISH_TIMEOUT_SECONDS = float(
    os.environ.get("MQTT_PUBLISH_TIMEOUT_SECONDS", "5")
)


AI_SERVICE_URL = os.environ.get("AI_SERVICE_URL", "http://ai-service:8000")
AI_SERVICE_INTERNAL_TOKEN = os.environ.get("AI_SERVICE_INTERNAL_TOKEN", "")
AI_SERVICE_TIMEOUT_SECONDS = float(
    os.environ.get("AI_SERVICE_TIMEOUT_SECONDS", "30")
)

# LOCAL UI TESTING ONLY (never enable in production): an explicit,
# default-off development opt-in that lets a loopback request act as the
# parent named by LOCAL_UI_TEST_USER_EMAIL so the server-rendered
# dashboard/controller can be inspected without the login page. Requires
# DEBUG=True on top of these flags - see core.local_ui_bypass.
LOCAL_UI_TEST_BYPASS = os.environ.get(
    "LOCAL_UI_TEST_BYPASS", "false"
).lower() in ("1", "true", "yes")
LOCAL_UI_TEST_USER_EMAIL = os.environ.get("LOCAL_UI_TEST_USER_EMAIL", "")

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
