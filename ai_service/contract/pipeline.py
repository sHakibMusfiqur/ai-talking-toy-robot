import base64
from dataclasses import dataclass

from action.base import ActionStage
from config import load_provider_settings
from context.base import ContextStage
from contract.assembler import assemble
from contract.enums import Action, Emotion, Language, LanguageHint, SafetyAction
from contract.schemas import AIInputContract, AIOutputContract, SafetyFlag
from emotion.base import EmotionStage
from errors import ProviderError
from lang_detect.interface import LangDetectStage
from lang_detect.mock import MockLangDetect
from llm.interface import LlmStage
from retry import DEFAULT_RETRY_POLICY, RetryPolicy, run_with_retry
from safety.interface import SafetyStage
from stt.interface import SttStage
from tts.interface import TtsStage
from understanding.base import UnderstandingStage


STT_MIN_CONFIDENCE = 0.5
STT_FALLBACK_TEXT = "I didn't quite catch that, can you say it again?"


SAFETY_FALLBACK_TEXT = "Let's talk about something else!"


LLM_FALLBACK_TEXT = "I'm having trouble thinking right now, ask me again in a bit."


@dataclass(frozen=True)
class PipelineStages:
    stt: SttStage
    lang_detect: LangDetectStage
    understanding: UnderstandingStage
    context: ContextStage
    safety: SafetyStage
    llm: LlmStage
    emotion: EmotionStage
    action: ActionStage
    tts: TtsStage
    stt_retry: RetryPolicy = DEFAULT_RETRY_POLICY
    llm_retry: RetryPolicy = DEFAULT_RETRY_POLICY
    tts_retry: RetryPolicy = DEFAULT_RETRY_POLICY


def default_stages() -> PipelineStages:
    """Phase 10: deterministic rule-based safety (Sections 14.2/17.1);
    every other stage is still a deterministic mock behind its
    interface. Real adapters replace these here (or via configuration
    once approved) - the pipeline and API stay unchanged."""
    from action.mock import MockAction
    from context.stub import RequestContext
    from emotion.mock import MockEmotion
    from llm.mock import MockLlm
    from safety.rules import RuleBasedSafety
    from stt.mock import MockStt
    from tts.mock import MockTts
    from understanding.mock import MockUnderstanding

    return PipelineStages(
        stt=MockStt(),
        lang_detect=MockLangDetect(),
        understanding=MockUnderstanding(),
        context=RequestContext(),
        safety=RuleBasedSafety(),
        llm=MockLlm(),
        emotion=MockEmotion(),
        action=MockAction(),
        tts=MockTts(),
        stt_retry=load_provider_settings("stt").retry_policy(),
        llm_retry=load_provider_settings("llm").retry_policy(),
        tts_retry=load_provider_settings("tts").retry_policy(),
    )


def _safety_flag(result) -> SafetyFlag:
    return SafetyFlag(
        flagged=result.flagged,
        category=result.category,
        action_taken=result.action_taken,
    )


def _unflagged_safety() -> SafetyFlag:
   
    return SafetyFlag(flagged=False, category=None, action_taken=SafetyAction.ALLOWED)


def _fail_closed_safety() -> SafetyFlag:
 
    return SafetyFlag(flagged=True, category=None, action_taken=SafetyAction.BLOCKED)


def _text_path_language(detected: Language | None) -> Language:
    
    return detected if detected is not None else Language.EN


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
    # A TTS provider failure degrades to text-only (audio: null) - Section 16.
    try:
        audio_ref = run_with_retry(
            lambda: stages.tts.run(text, language.value, emotion),
            stages.tts_retry,
        )
    except (ProviderError, TimeoutError):
        audio_ref = None
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
    detected: Language | None = None
    if payload.audio is not None:
        audio_bytes = base64.b64decode(payload.audio)
        hint = payload.language_hint.value if payload.language_hint else None
        try:
            stt_result = run_with_retry(lambda: st.stt.run(audio_bytes, hint), st.stt_retry)
        except (ProviderError, TimeoutError):
            # STT failure -> controlled retry/fallback (Section 15.1 path).
            return _finalize(
                payload,
                text=STT_FALLBACK_TEXT,
                language=(
                    Language.BN
                    if payload.language_hint == LanguageHint.BN
                    else Language.EN
                ),
                emotion=Emotion.NEUTRAL,
                action=Action.LISTENING_RETRY,
                safety=None,
                stages=st,
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
        # Text turns: per-utterance language decision (AR-12), deterministic
        # implementation for now; adapter swap lives behind the interface.
        detected = st.lang_detect.run(
            transcript, payload.language_hint.value if payload.language_hint else None
        ).language

    # --- understanding + request-provided context -------------------------
    intent = st.understanding.run(transcript, payload.mode.value)
    bundle = st.context.run(payload.context)

    # --- safety stage 1: pre-LLM (Section 14.2) ---------------------------
    try:
        pre = st.safety.pre_check(transcript, intent.intent)
    except (ProviderError, TimeoutError):
        # Safety failure -> fail closed, LLM never runs.
        pre = None
    if pre is None:
        return _finalize(
            payload,
            text=SAFETY_FALLBACK_TEXT,
            language=_text_path_language(detected),
            emotion=Emotion.NEUTRAL,
            action=Action.IDLE,
            safety=_fail_closed_safety(),
            stages=st,
        )
    if pre.flagged:
        return _finalize(
            payload,
            text=SAFETY_FALLBACK_TEXT,
            language=_text_path_language(detected),
            emotion=Emotion.NEUTRAL,
            action=Action.IDLE,
            safety=_safety_flag(pre),
            stages=st,
        )

    # --- LLM (retried once with backoff per Section 16) -------------------
    try:
        draft = run_with_retry(
            lambda: st.llm.run(transcript, intent.intent, bundle, payload.mode.value),
            st.llm_retry,
        )
    except (ProviderError, TimeoutError):
        # LLM failure -> generic safe fallback (Section 16), contract-shaped.
        return _finalize(
            payload,
            text=LLM_FALLBACK_TEXT,
            language=_text_path_language(detected),
            emotion=Emotion.NEUTRAL,
            action=Action.IDLE,
            safety=_unflagged_safety(),
            stages=st,
        )

    # --- safety stage 2: post-LLM last gate (Section 14.2) ----------------
    try:
        post = st.safety.post_check(draft.text)
    except (ProviderError, TimeoutError):
        return _finalize(
            payload,
            text=SAFETY_FALLBACK_TEXT,
            language=_text_path_language(detected),
            emotion=Emotion.NEUTRAL,
            action=Action.IDLE,
            safety=_fail_closed_safety(),
            stages=st,
        )
    if post.flagged:
        # Flagged LLM output is replaced here - it never reaches TTS.
        return _finalize(
            payload,
            text=SAFETY_FALLBACK_TEXT,
            language=_text_path_language(detected),
            emotion=Emotion.NEUTRAL,
            action=Action.IDLE,
            safety=_safety_flag(post),
            stages=st,
        )

    # --- finalize: emotion -> action -> TTS -> contract --------------------
    emotion = st.emotion.run(draft.text, intent.intent)
    action = st.action.run(draft.text, intent.intent)
    language = detected if detected is not None else Language(draft.language)
    return _finalize(
        payload,
        text=draft.text,
        language=language,
        emotion=emotion,
        action=action,
        safety=_safety_flag(post),
        stages=st,
    )
