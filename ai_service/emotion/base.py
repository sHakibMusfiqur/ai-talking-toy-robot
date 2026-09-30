from typing import Protocol

from contract.enums import Emotion


class EmotionStage(Protocol):
    def run(self, text: str, intent: str) -> Emotion: ...
