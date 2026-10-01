from typing import Optional

from contract.enums import Emotion

from .interface import TtsStage

MOCK_AUDIO: Optional[str] = None


class MockTts:
    def run(self, text: str, language: str, emotion: Emotion) -> Optional[str]:
        _ = text, language, emotion
        return MOCK_AUDIO
