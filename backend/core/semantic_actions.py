from rest_framework import serializers

# ONE central backend definition of the frozen semantic action contract
# (docs/01 Section 19 / docs/02 Table 3): the AI service's Action enum is
# the canonical shared list; DANCE is the controller-origin addition used by
# the Phase 8 command tests and the Phase 17 quick actions. The backend
# only validates and passes values through unchanged — the firmware's
# actions/ module owns all physical translation (AR-17). Unknown values are
# rejected at the API boundary, so raw servo/PWM/GPIO-style control can
# never reach the robot through this gate.
SEMANTIC_ACTIONS = frozenset(
    {
        "IDLE",
        "WAVE",
        "DANCE",
        "NOD",
        "TILT_HEAD",
        "POINT",
        "LISTENING_RETRY",
    }
)


def is_semantic_action(value):
    """Case-insensitive membership check against the frozen contract."""
    return isinstance(value, str) and value.upper() in SEMANTIC_ACTIONS


def validate_semantic_action(value):
    """Raise a field-keyed 400 for any action outside the frozen set; the
    value itself is returned unchanged (pass-through, Section 19)."""
    if not is_semantic_action(value):
        raise serializers.ValidationError(
            {
                "action": (
                    "Unknown semantic action. Allowed: "
                    f"{', '.join(sorted(SEMANTIC_ACTIONS))}."
                )
            }
        )
    return value
