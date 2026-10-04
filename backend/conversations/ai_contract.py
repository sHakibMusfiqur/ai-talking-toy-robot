INPUT_FIELDS = frozenset(
    {
        "conversation_id",
        "child_profile_id",
        "robot_id",
        "text",
        "audio",
        "language_hint",
        "context",
        "mode",
    }
)
CONTEXT_FIELDS = frozenset({"recent_turns", "memories"})
RECENT_TURN_FIELDS = frozenset({"role", "text"})
MEMORY_WRITE_FIELDS = frozenset({"key", "value"})

# AI Output Contract (Section 15.3) - exactly 8 fields, no more, no less.
OUTPUT_FIELDS = frozenset(
    {
        "conversation_id",
        "text",
        "language",
        "emotion",
        "action",
        "safety",
        "audio",
        "memory_writes",
    }
)
SAFETY_FIELDS = frozenset({"flagged", "category", "action_taken"})

# Frozen enums (docs/01 Section 19 / Section 15.3).
LANGUAGES = frozenset({"en", "bn"})
LANGUAGE_HINTS = frozenset({"en", "bn", "auto"})
MODES = frozenset({"conversation", "story", "quiz"})
EMOTIONS = frozenset({"HAPPY", "CURIOUS", "THINKING", "CONCERNED", "NEUTRAL"})
ACTIONS = frozenset(
    {"WAVE", "NOD", "TILT_HEAD", "IDLE", "POINT", "LISTENING_RETRY"}
)
SAFETY_CATEGORIES = frozenset({"PERSONAL_INFO_REQUEST", "UNSAFE_TOPIC"})
SAFETY_ACTIONS = frozenset(
    {"ALLOWED", "REDIRECTED", "BLOCKED", "ALLOWED_WITH_CAUTION"}
)
RECENT_TURN_ROLES = frozenset({"user", "assistant"})


HARD_FAILURE_TEXT = (
    "I'm having trouble thinking right now, ask me again in a bit."
)
HARD_FAILURE_EMOTION = "CONCERNED"
HARD_FAILURE_ACTION = "IDLE"
HARD_FAILURE_LANGUAGE = "en"


FAILURE_REASONS = frozenset(
    {"config", "timeout", "unavailable", "http_error", "invalid_response"}
)


class AIContractError(Exception):
    """The AI service response violated the frozen Output Contract.

    The message never echoes contract *values* (they can contain child
    input) - only field names.
    """


def _require_dict(value, where):
    if not isinstance(value, dict):
        raise AIContractError(f"{where} must be an object")
    return value


def _require_keys(value, expected, where):
    actual = set(value)
    if actual != set(expected):
        unexpected = sorted(actual - set(expected))
        missing = sorted(set(expected) - actual)
        raise AIContractError(
            f"{where} fields differ (unexpected={unexpected}, missing={missing})"
        )


def _require_str(value, where):
    if not isinstance(value, str) or not value.strip():
        raise AIContractError(f"{where} must be a non-empty string")


def _validate_memory_writes(value):
    if not isinstance(value, list):
        raise AIContractError("memory_writes must be a list")
    for entry in value:
        _require_dict(entry, "memory_writes entry")
        _require_keys(entry, MEMORY_WRITE_FIELDS, "memory_writes entry")
        _require_str(entry["key"], "memory_writes key")
        _require_str(entry["value"], "memory_writes value")


def _validate_safety(value):
    if value is None:
        return None
    _require_dict(value, "safety")
    _require_keys(value, SAFETY_FIELDS, "safety")
    if not isinstance(value["flagged"], bool):
        raise AIContractError("safety.flagged must be a boolean")
    category = value["category"]
    if category is not None and category not in SAFETY_CATEGORIES:
        raise AIContractError("safety.category is not in the frozen taxonomy")
    if value["flagged"] and category is None:
        raise AIContractError("flagged safety must carry a category")
    if value["action_taken"] not in SAFETY_ACTIONS:
        raise AIContractError("safety.action_taken is not in the frozen set")
    return value


def validate_ai_input(payload, *, conversation_id):
    """Self-check for the body Django is about to send (Section 15.2).

    The AI service would answer `422` for any deviation anyway; validating
    locally keeps a builder bug from ever reaching the wire and makes the
    failure mode our own controlled one.
    """
    _require_dict(payload, "AI input")
    _require_keys(payload, INPUT_FIELDS, "AI input")

    for field in ("conversation_id", "child_profile_id", "robot_id"):
        _require_str(payload[field], field)
    if payload["conversation_id"] != conversation_id:
        raise AIContractError("conversation_id does not match the conversation")

    has_text = payload["text"] is not None
    has_audio = payload["audio"] is not None
    if has_text == has_audio:
        raise AIContractError("exactly one of text or audio is required")
    if has_text:
        _require_str(payload["text"], "text")
    else:
        _require_str(payload["audio"], "audio")

    hint = payload["language_hint"]
    if hint is not None and hint not in LANGUAGE_HINTS:
        raise AIContractError("language_hint is not in the frozen set")
    if payload["mode"] not in MODES:
        raise AIContractError("mode is not in the frozen set")

    context = _require_dict(payload["context"], "context")
    _require_keys(context, CONTEXT_FIELDS, "context")
    turns = context["recent_turns"]
    if not isinstance(turns, list):
        raise AIContractError("context.recent_turns must be a list")
    for turn in turns:
        _require_dict(turn, "recent turn")
        _require_keys(turn, RECENT_TURN_FIELDS, "recent turn")
        if turn["role"] not in RECENT_TURN_ROLES:
            raise AIContractError("recent turn role is not in the frozen set")
        _require_str(turn["text"], "recent turn text")
    _validate_memory_writes(context["memories"])
    return payload


def validate_ai_output(data, *, conversation_id):
    """Validate a parsed AI response against the frozen Output Contract.

    Returns the response unchanged so callers keep one trusted dict.
    Raises `AIContractError` on any deviation - the caller converts that
    into the local hard-failure fallback instead of forwarding a
    contract-breaking response.
    """
    _require_dict(data, "AI response")
    _require_keys(data, OUTPUT_FIELDS, "AI response")

    if data["conversation_id"] != conversation_id:
        raise AIContractError("conversation_id does not match the request")

    _require_str(data["text"], "text")

    if data["language"] not in LANGUAGES:
        raise AIContractError("language is not in the frozen set")
    if data["emotion"] not in EMOTIONS:
        raise AIContractError("emotion is not in the frozen set")
    if data["action"] not in ACTIONS:
        raise AIContractError("action is not in the frozen set")

    _validate_safety(data["safety"])

    audio = data["audio"]
    if audio is not None and (
        not isinstance(audio, str) or not audio.strip()
    ):
        raise AIContractError("audio must be null or a non-empty string")

    _validate_memory_writes(data["memory_writes"])
    return data
