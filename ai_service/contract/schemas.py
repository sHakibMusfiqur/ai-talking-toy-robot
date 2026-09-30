import base64
import binascii
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .enums import Action, Emotion, Language, LanguageHint, Mode, SafetyAction, SafetyCategory

STRICT = ConfigDict(extra="forbid")


class MemoryWrite(BaseModel):
    model_config = STRICT

    key: str = Field(min_length=1)
    value: str = Field(min_length=1)


class RecentTurn(BaseModel):
    model_config = STRICT

    role: Literal["user", "assistant"]
    text: str = Field(min_length=1)


class ContextBlock(BaseModel):
    """Pre-fetched conversation context supplied by Django on every call
    (AR-5b: the AI service holds no DB connection and no session state)."""

    model_config = STRICT

    recent_turns: list[RecentTurn] = Field(default_factory=list)
    memories: list[MemoryWrite] = Field(default_factory=list)


class AIInputContract(BaseModel):
    model_config = STRICT

    conversation_id: str = Field(min_length=1)
    child_profile_id: str = Field(min_length=1)
    robot_id: str = Field(min_length=1)
    text: Optional[str] = Field(default=None, min_length=1)
    # Base64 encoding of the contract's binary `audio` field (JSON transport).
    audio: Optional[str] = None
    language_hint: Optional[LanguageHint] = None
    context: ContextBlock
    mode: Mode = Mode.CONVERSATION

    @field_validator("text", "audio")
    @classmethod
    def _not_blank(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            raise ValueError("must not be blank")
        return v

    @field_validator("audio")
    @classmethod
    def _valid_base64(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        try:
            decoded = base64.b64decode(v, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("audio must be valid base64") from exc
        if not decoded:
            raise ValueError("audio must decode to non-empty bytes")
        return v

    @model_validator(mode="after")
    def _exactly_one_of_text_or_audio(self) -> "AIInputContract":
        # Section 15.2: `audio` (voice turn) OR `text` (text turn).
        if (self.text is None) == (self.audio is None):
            raise ValueError("exactly one of 'text' or 'audio' must be provided")
        return self


class SafetyFlag(BaseModel):
    model_config = STRICT

    flagged: bool
    category: Optional[SafetyCategory] = None
    action_taken: SafetyAction


class AIOutputContract(BaseModel):
    """Section 15.3 shape. `safety` may be null only for the Section 15.1
    STT-failure fallback. `audio` is null while TTS is stubbed (Phase 9)."""

    model_config = STRICT

    conversation_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    language: Language
    emotion: Emotion
    action: Action
    safety: Optional[SafetyFlag] = None
    audio: Optional[str] = None
    memory_writes: list[MemoryWrite] = Field(default_factory=list)
