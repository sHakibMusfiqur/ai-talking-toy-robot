
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from contract.enums import SafetyAction, SafetyCategory


class SafetyResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flagged: bool
    category: SafetyCategory | None = None
    action_taken: SafetyAction


class SafetyStage(Protocol):
    """Combined safety stage (pre + post checks)."""

    def pre_check(self, text: str, intent: str) -> SafetyResult: ...
    def post_check(self, text: str) -> SafetyResult: ...
