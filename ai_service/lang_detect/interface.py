from typing import Optional, Protocol

from pydantic import BaseModel, ConfigDict

from contract.enums import Language


class LanguageDetectResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: Language
    confidence: float


class LangDetectStage(Protocol):
    def run(self, text: str, language_hint: Optional[str]) -> LanguageDetectResult: ...
