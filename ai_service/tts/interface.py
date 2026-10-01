from typing import Optional, Protocol

from contract.enums import Emotion


class TtsStage(Protocol):
    def run(self, text: str, language: str, emotion: Emotion) -> Optional[str]: ...
