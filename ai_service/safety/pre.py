from typing import Protocol

from safety.interface import SafetyResult


class PreSafetyStage(Protocol):
    def pre_check(self, text: str, intent: str) -> SafetyResult: ...
