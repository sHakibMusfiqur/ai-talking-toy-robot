from enum import Enum


class Emotion(str, Enum):
    HAPPY = "HAPPY"
    CURIOUS = "CURIOUS"
    THINKING = "THINKING"
    CONCERNED = "CONCERNED"
    NEUTRAL = "NEUTRAL"


class Action(str, Enum):
    WAVE = "WAVE"
    NOD = "NOD"
    TILT_HEAD = "TILT_HEAD"
    IDLE = "IDLE"
    POINT = "POINT"
    LISTENING_RETRY = "LISTENING_RETRY"


class SafetyCategory(str, Enum):
    PERSONAL_INFO_REQUEST = "PERSONAL_INFO_REQUEST"
    UNSAFE_TOPIC = "UNSAFE_TOPIC"


class SafetyAction(str, Enum):
    ALLOWED = "ALLOWED"
    REDIRECTED = "REDIRECTED"
    BLOCKED = "BLOCKED"
    ALLOWED_WITH_CAUTION = "ALLOWED_WITH_CAUTION"


class Mode(str, Enum):
    CONVERSATION = "conversation"
    STORY = "story"
    QUIZ = "quiz"


class LanguageHint(str, Enum):
    """Section 15.1: language hint comes from device config (Bangla/English)
    or is 'auto'."""

    EN = "en"
    BN = "bn"
    AUTO = "auto"


class Language(str, Enum):
    """Language actually used in the reply (Section 15.3)."""

    EN = "en"
    BN = "bn"


# Banned hardware-level keys (Section 15.3 explicit non-goal): never present
# in any AI output, at any depth.
HARDWARE_KEY_BANNERS = (
    "servo",
    "motor",
    "gpio",
    "pwm",
    "led_pin",
    "raw_motor_command",
)
