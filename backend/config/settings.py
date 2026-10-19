import os
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

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
    # Phase 12 pre-parse gate: header-only, before authentication and
    # any body read, scoped to POST /api/v1/conversations/turn/audio[/].
    "core.audio_upload.AudioUploadBodyGateMiddleware",
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
                "core.context_processors.asset_version",
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

ASSET_VERSION = os.environ.get("ASSET_VERSION", "2026-10-18-4")

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

# Duplicate-command suppression (Phase A): per (authenticated user, robot)
# cooldown on POST /api/v1/devices/<pk>/command/. In-memory and therefore
# per-process (a multi-worker deployment would need a shared store; today
# the backend runs a single process). 0 disables the check.
ROBOT_COMMAND_COOLDOWN_SECONDS = float(
    os.environ.get("ROBOT_COMMAND_COOLDOWN_SECONDS", "1")
)

# Wi-Fi onboarding claim tickets (step 3): the robot requests a single-use
# ticket over the parent's Wi-Fi; only its hash is stored and it expires
# quickly. The redeem endpoint adds a per-user attempt limit (in-memory,
# same single-process limitation as ROBOT_COMMAND_COOLDOWN_SECONDS;
# <= 0 disables it).
DEVICE_CLAIM_TTL_SECONDS = int(os.environ.get("DEVICE_CLAIM_TTL_SECONDS", "600"))
DEVICE_CLAIM_RATE_LIMIT_ATTEMPTS = int(
    os.environ.get("DEVICE_CLAIM_RATE_LIMIT_ATTEMPTS", "10")
)
DEVICE_CLAIM_RATE_LIMIT_WINDOW_SECONDS = int(
    os.environ.get("DEVICE_CLAIM_RATE_LIMIT_WINDOW_SECONDS", "60")
)


AI_SERVICE_URL = os.environ.get("AI_SERVICE_URL", "http://ai-service:8000")
AI_SERVICE_INTERNAL_TOKEN = os.environ.get("AI_SERVICE_INTERNAL_TOKEN", "")
AI_SERVICE_TIMEOUT_SECONDS = float(
    os.environ.get("AI_SERVICE_TIMEOUT_SECONDS", "30")
)


CONVERSATION_MAX_TEXT_LENGTH = int(
    os.environ.get("CONVERSATION_MAX_TEXT_LENGTH", "4000")
)
CONVERSATION_MAX_AUDIO_BASE64_LENGTH = int(
    os.environ.get("CONVERSATION_MAX_AUDIO_BASE64_LENGTH", "1400000")
)
DATA_UPLOAD_MAX_MEMORY_SIZE = int(
    os.environ.get("DATA_UPLOAD_MAX_MEMORY_SIZE", str(5 * 1024 * 1024))
)




def audio_upload_bound_from_env(env_name, raw, default, approved_max):
    """Resolve one Phase 12 upload bound from an env value.

    Empty/unset falls back to the approved default. Everything else is
    strictly validated: ASCII digits only, length-bounded so int() can
    never fail or blow up, at least 1, at most ``approved_max``.
    """
    if raw is None or not str(raw).strip():
        return default
    value = str(raw).strip()
    if len(value) > 10 or not (value.isascii() and value.isdigit()):
        raise ImproperlyConfigured(
            f"{env_name} must be a positive integer of at most 10 digits; "
            f"got {raw!r}."
        )
    number = int(value)
    if number < 1:
        raise ImproperlyConfigured(
            f"{env_name} must be at least 1; got {number}."
        )
    if number > approved_max:
        raise ImproperlyConfigured(
            f"{env_name}={number} exceeds the approved Phase 12 limit of "
            f"{approved_max} bytes; bounds may only be lowered."
        )
    return number


def validate_audio_upload_bounds(file_bytes, body_bytes):
    """Reject configurations where a file could exceed the body gate."""
    if file_bytes > body_bytes:
        raise ImproperlyConfigured(
            "AUDIO_UPLOAD_MAX_FILE_BYTES must not exceed "
            f"AUDIO_UPLOAD_MAX_BODY_BYTES; got file={file_bytes}, "
            f"body={body_bytes}."
        )
    return file_bytes, body_bytes


AUDIO_UPLOAD_MAX_FILE_BYTES = audio_upload_bound_from_env(
    "AUDIO_UPLOAD_MAX_FILE_BYTES",
    os.environ.get("AUDIO_UPLOAD_MAX_FILE_BYTES"),
    1024 * 1024,
    1024 * 1024,
)
AUDIO_UPLOAD_MAX_BODY_BYTES = audio_upload_bound_from_env(
    "AUDIO_UPLOAD_MAX_BODY_BYTES",
    os.environ.get("AUDIO_UPLOAD_MAX_BODY_BYTES"),
    1024 * 1024 + 16 * 1024,
    1024 * 1024 + 16 * 1024,
)
validate_audio_upload_bounds(AUDIO_UPLOAD_MAX_FILE_BYTES, AUDIO_UPLOAD_MAX_BODY_BYTES)
FILE_UPLOAD_MAX_MEMORY_SIZE = AUDIO_UPLOAD_MAX_BODY_BYTES


# limiter.
CONVERSATION_TURN_RATE_LIMIT = os.environ.get(
    "CONVERSATION_TURN_RATE_LIMIT", "30/min"
)


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
