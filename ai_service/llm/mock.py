from context.base import ContextBundle

from .interface import LlmResult

MOCK_REPLY = "Hello! I am happy to talk with you."


class MockLlm:
    def run(self, text: str, intent: str, context: ContextBundle, mode: str) -> LlmResult:
        _ = text, intent, context, mode
        return LlmResult(text=MOCK_REPLY, language="en")
