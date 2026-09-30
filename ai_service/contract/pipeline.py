import base64
from dataclasses import dataclass

from action.base import ActionStage
from context.base import ContextStage
from contract.assembler import assemble
from contract.enums import Action, Emotion, Language
from contract.schemas import AIInputContract, AIOutputContract, SafetyFlag
from emotion.base import EmotionStage
from llm.base import LlmStage
from safety.base import SafetyResult, SafetyStage
from stt.base import SttStage
from tts.base import TtsStage
from understanding.base import UnderstandingStage

# Section 15.1: low-confidence transcription -> fallback, no LLM generation.
STT_MIN_CONFIDENCE = 0.5
STT_FALLBACK_TEXT = "I didn't quite catch that, can you say it again?"

# Section 14.2: flagged requests/responses get a pre-approved redirect
# (placeholder text pending product sign-off; never the raw content).
SAFETY_FALLBACK_TEXT = "Let's talk about something else!"


@dataclass(frozen=True)
class PipelineStages:
    stt: SttStage
    understanding: UnderstandingStage
    context: ContextStage
    safety: SafetyStage
    llm: LlmStage
    emotion: EmotionStage
    action: ActionStage
    tts: TtsStage


def default_stages() -> PipelineStages:
    """Phase 9: every stage is a deterministic stub. Phase 10 swaps the
    implementations here - the pipeline and API stay unchanged."""
    from action.mock import MockAction
    from context.stub import RequestContext
    from emotion.mock import MockEmotion
    from llm.mock import MockLlm
    from safety.mock import MockSafety
    from stt.mock import MockStt
    from tts.mock import MockTts
    from understanding.mock import MockUnderstanding

    return PipelineStages(
        stt=MockStt(),
        understanding=MockUnderstanding(),
        context=RequestContext(),
        safety=MockSafety(),
        llm=MockLlm(),
        emotion=MockEmotion(),
        action=MockAction(),
        tts=MockTts(),
    )


def _safety_flag(result: SafetyResult) -> SafetyFlag:
    return SafetyFlag(
        flagged=result.flagged,
        category=result.category,
        action_taken=result.action_taken,
    )


def _finalize(
    payload: AIInputContract,
    text: str,
    language: Language,
    emotion: Emotion,
    action: Action,
    safety: SafetyFlag | None,
    stages: PipelineStages,
) -> AIOutputContract:
    # TTS runs only on the finalized, safety-approved text (Section 18).
    audio_ref = stages.tts.run(text, language.value, emotion)
    return assemble(
        payload,
        text=text,
        language=language,
        emotion=emotion,
        action=action,
        safety=safety,
        audio=audio_ref,
    )


def run_pipeline(payload: AIInputContract, stages: PipelineStages | None = None) -> AIOutputContract:
    st = stages or default_stages()

    # --- transcript acquisition ------------------------------------------
    if payload.audio is not None:
        stt_result = st.stt.run(
            base64.b64decode(payload.audio),
            payload.language_hint.value if payload.language_hint else None,
        )
        if stt_result.confidence < STT_MIN_CONFIDENCE:
            # Section 15.1: no LLM on low-confidence input.
            return _finalize(
                payload,
                text=STT_FALLBACK_TEXT,
                language=Language(stt_result.language),
                emotion=Emotion.NEUTRAL,
                action=Action.LISTENING_RETRY,
                safety=None,
                stages=st,
            )
        transcript = stt_result.text
    else:
        transcript = payload.text or ""

    # --- understanding + request-provided context -------------------------
    intent = st.understanding.run(transcript, payload.mode.value)
    bundle = st.context.run(payload.context)

    # --- safety stage 1: pre-LLM (Section 14.2) ---------------------------
    pre = st.safety.pre_check(transcript, intent.intent)
    if pre.flagged:
        return _finalize(
            payload,
            text=SAFETY_FALLBACK_TEXT,
            language=Language.EN,
            emotion=Emotion.NEUTRAL,
            action=Action.IDLE,
            safety=_safety_flag(pre),
            stages=st,
        )

    # --- LLM ---------------------------------------------------------------
    draft = st.llm.run(transcript, intent.intent, bundle, payload.mode.value)

    # --- safety stage 2: post-LLM last gate (Section 14.2) ----------------
    post = st.safety.post_check(draft.text)
    if post.flagged:
        return _finalize(
            payload,
            text=SAFETY_FALLBACK_TEXT,
            language=Language.EN,
            emotion=Emotion.NEUTRAL,
            action=Action.IDLE,
            safety=_safety_flag(post),
            stages=st,
        )

    # --- finalize: emotion -> action -> TTS -> contract --------------------
    emotion = st.emotion.run(draft.text, intent.intent)
    action = st.action.run(draft.text, intent.intent)
    return _finalize(
        payload,
        text=draft.text,
        language=Language(draft.language),
        emotion=emotion,
        action=action,
        safety=_safety_flag(post),
        stages=st,
    )
