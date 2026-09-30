from contract.schemas import AIInputContract, AIOutputContract, MemoryWrite, SafetyFlag

from contract.enums import Action, Emotion, Language


def assemble(
    payload: AIInputContract,
    text: str,
    language: Language,
    emotion: Emotion,
    action: Action,
    safety: SafetyFlag | None,
    audio: str | None,
    memory_writes: list[MemoryWrite] | None = None,
) -> AIOutputContract:
    """Validate-and-build the final typed response (raises ValidationError if
    any pipeline stage produced a contract-inconsistent value)."""
    return AIOutputContract(
        conversation_id=payload.conversation_id,
        text=text,
        language=language,
        emotion=emotion,
        action=action,
        safety=safety,
        audio=audio,
        memory_writes=list(memory_writes or []),
    )
