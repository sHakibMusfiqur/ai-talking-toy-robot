from typing import Protocol

from pydantic import BaseModel, ConfigDict

from context.base import ContextBundle


class LlmResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    language: str


class LlmStage(Protocol):
    def run(self, text: str, intent: str, context: ContextBundle, mode: str) -> LlmResult: ...
