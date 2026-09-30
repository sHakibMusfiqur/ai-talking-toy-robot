from typing import Optional, Protocol

from pydantic import BaseModel, ConfigDict


class SttResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    language: str
    confidence: float


class SttStage(Protocol):
    def run(self, audio: bytes, language_hint: Optional[str]) -> SttResult: ...
