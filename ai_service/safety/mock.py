from contract.enums import SafetyAction

from .base import SafetyResult

_RESULT = SafetyResult(flagged=False, category=None, action_taken=SafetyAction.ALLOWED)


class MockSafety:
    def pre_check(self, text: str, intent: str) -> SafetyResult:
        _ = text, intent
        return _RESULT

    def post_check(self, text: str) -> SafetyResult:
        _ = text
        return _RESULT
