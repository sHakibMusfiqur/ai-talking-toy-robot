from typing import Protocol

from safety.interface import SafetyResult


class PostSafetyStage(Protocol):
    def post_check(self, text: str) -> SafetyResult: ...
