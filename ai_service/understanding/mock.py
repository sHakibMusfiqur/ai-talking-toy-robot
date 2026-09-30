from .base import IntentResult

MOCK_INTENT = "small_talk"


class MockUnderstanding:
    def run(self, text: str, mode: str) -> IntentResult:
        _ = text, mode
        return IntentResult(intent=MOCK_INTENT)
