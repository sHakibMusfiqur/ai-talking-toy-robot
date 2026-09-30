from contract.enums import Emotion

MOCK_EMOTION = Emotion.HAPPY


class MockEmotion:
    def run(self, text: str, intent: str) -> Emotion:
        _ = text, intent
        return MOCK_EMOTION
