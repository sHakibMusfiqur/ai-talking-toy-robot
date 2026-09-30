from typing import Protocol

from pydantic import BaseModel, ConfigDict


class IntentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str


class UnderstandingStage(Protocol):
    def run(self, text: str, mode: str) -> IntentResult: ...
