import base64

import pytest

from action.mock import MockAction
from contract.enums import Action, Emotion, Language, SafetyAction
from contract.pipeline import (
    LLM_FALLBACK_TEXT,
    SAFETY_FALLBACK_TEXT,
    STT_FALLBACK_TEXT,
    PipelineStages,
    run_pipeline,
)
from contract.schemas import AIInputContract, AIOutputContract
from context.stub import RequestContext
from emotion.mock import MockEmotion
from lang_detect.mock import MockLangDetect
from llm.mock import MockLlm
from retry import RetryPolicy
from safety.mock import MockSafety
from stt.mock import MockStt
from tts.mock import MockTts
from understanding.mock import MockUnderstanding

INSTANT = RetryPolicy(timeout_seconds=5.0, retry_count=1, backoff_seconds=0.0)
# Deliberately non-default retry_count: proves the *configured* policy,
# not a hard-coded attempt count, drives retries.
CUSTOM = RetryPolicy(timeout_seconds=5.0, retry_count=2, backoff_seconds=0.0)

EXPECTED_KEYS = {
    "conversation_id",
    "text",
    "language",
    "emotion",
    "action",
    "safety",
    "audio",
    "memory_writes",
}


def _base_dict(**overrides) -> dict:
    payload = {
        "conversation_id": "conv-timeout",
        "child_profile_id": "child-1",
        "robot_id": "robot-1",
        "text": "Hello",
        "language_hint": "en",
        "context": {"recent_turns": [], "memories": []},
    }
    payload.update(overrides)
    return payload


def _payload(**overrides) -> AIInputContract:
    return AIInputContract(**_base_dict(**overrides))


def _audio_payload(**overrides) -> AIInputContract:
    payload = _base_dict(**overrides)
    payload.pop("text")
    payload["audio"] = base64.b64encode(b"\x01\x02").decode()
    return AIInputContract(**payload)


def _stages(**overrides) -> PipelineStages:
    stages = dict(
        stt=MockStt(),
        lang_detect=MockLangDetect(),
        understanding=MockUnderstanding(),
        context=RequestContext(),
        safety=MockSafety(),
        llm=MockLlm(),
        emotion=MockEmotion(),
        action=MockAction(),
        tts=MockTts(),
        stt_retry=INSTANT,
        llm_retry=INSTANT,
        tts_retry=INSTANT,
    )
    stages.update(overrides)
    return PipelineStages(**stages)


def _assert_contract_valid(out) -> None:
    assert isinstance(out, AIOutputContract)
    assert set(out.model_dump()) == EXPECTED_KEYS
    AIOutputContract.model_validate(out.model_dump())


class TimeoutStt:
    def __init__(self):
        self.attempts = 0

    def run(self, audio, language_hint):
        self.attempts += 1
        raise TimeoutError("simulated slow STT")


class TimeoutLlm:
    def __init__(self):
        self.attempts = 0

    def run(self, text, intent, context, mode):
        self.attempts += 1
        raise TimeoutError("simulated slow LLM")


class TimeoutTts:
    def __init__(self):
        self.attempts = 0

    def run(self, text, language, emotion):
        self.attempts += 1
        raise TimeoutError("simulated slow TTS")


class TimeoutPreSafety:
    def __init__(self):
        self.pre_attempts = 0
        self.post_attempts = 0

    def pre_check(self, text, intent):
        self.pre_attempts += 1
        raise TimeoutError("simulated slow safety pre-check")

    def post_check(self, text):
        self.post_attempts += 1
        return MockSafety().post_check(text)


class TimeoutPostSafety:
    def __init__(self):
        self.pre_attempts = 0
        self.post_attempts = 0

    def pre_check(self, text, intent):
        self.pre_attempts += 1
        return MockSafety().pre_check(text, intent)

    def post_check(self, text):
        self.post_attempts += 1
        raise TimeoutError("simulated slow safety post-check")


class SpyLlm:
    def __init__(self):
        self.calls = 0

    def run(self, text, intent, context, mode):
        self.calls += 1
        return MockLlm().run(text, intent, context, mode)


class RecordingTts:
    def __init__(self):
        self.received = []

    def run(self, text, language, emotion):
        self.received.append(text)
        return None


# --- A. STT timeout ------------------------------------------------------


def test_stt_timeout_retries_per_policy_then_falls_back(block_network):
    stt, llm, tts = TimeoutStt(), SpyLlm(), RecordingTts()
    out = run_pipeline(
        _audio_payload(), _stages(stt=stt, llm=llm, tts=tts, stt_retry=CUSTOM)
    )
    assert stt.attempts == 3, "configured retry_count=2 must yield 1+2 attempts"
    assert llm.calls == 0, "STT fallback path must never call the LLM"
    _assert_contract_valid(out)
    assert out.text == STT_FALLBACK_TEXT
    assert out.emotion == Emotion.NEUTRAL
    assert out.action == Action.LISTENING_RETRY
    assert out.safety is None
    assert out.language == Language.EN
    assert tts.received == [STT_FALLBACK_TEXT]


def test_stt_timeout_honors_requested_language(block_network):
    out = run_pipeline(
        _audio_payload(language_hint="bn"), _stages(stt=TimeoutStt())
    )
    _assert_contract_valid(out)
    assert out.language == Language.BN, "requested language drives the fallback reply"
    assert out.text == STT_FALLBACK_TEXT
    assert out.action == Action.LISTENING_RETRY


# --- B. LLM timeout ------------------------------------------------------


def test_llm_timeout_retries_then_returns_contract_safe_fallback(block_network):
    llm, tts = TimeoutLlm(), RecordingTts()
    out = run_pipeline(_payload(), _stages(llm=llm, tts=tts))
    assert llm.attempts == 2, "first attempt + configured single retry"
    _assert_contract_valid(out)
    assert out.text == LLM_FALLBACK_TEXT
    assert out.emotion == Emotion.NEUTRAL
    assert out.action == Action.IDLE
    assert out.safety is not None
    assert out.safety.flagged is False
    assert out.safety.category is None
    assert out.safety.action_taken == SafetyAction.ALLOWED
    assert tts.received == [LLM_FALLBACK_TEXT]


# --- C. TTS timeout ------------------------------------------------------


def test_tts_timeout_preserves_content_and_degrades_to_null_audio(block_network):
    tts = TimeoutTts()
    out = run_pipeline(_payload(), _stages(tts=tts))
    assert tts.attempts == 2, "first attempt + configured single retry"
    _assert_contract_valid(out)
    assert out.audio is None
    assert out.text == "Hello! I am happy to talk with you."
    assert out.emotion == Emotion.HAPPY
    assert out.action == Action.WAVE
    assert out.safety is not None
    assert out.safety.flagged is False
    assert out.safety.action_taken == SafetyAction.ALLOWED


# --- D. safety timeouts (existing fail-closed design, unchanged) ---------


def test_safety_pre_check_timeout_fails_closed(block_network):
    safety, llm, tts = TimeoutPreSafety(), SpyLlm(), RecordingTts()
    out = run_pipeline(_payload(), _stages(safety=safety, llm=llm, tts=tts))
    assert safety.pre_attempts == 1, "safety keeps its direct single-call design (no new retry)"
    assert llm.calls == 0, "fail-closed pre-check must never reach the LLM"
    _assert_contract_valid(out)
    assert out.text == SAFETY_FALLBACK_TEXT
    assert out.safety is not None
    assert out.safety.flagged is True
    assert out.safety.action_taken == SafetyAction.BLOCKED
    assert out.safety.category is None
    assert out.emotion == Emotion.NEUTRAL
    assert out.action == Action.IDLE
    assert tts.received == [SAFETY_FALLBACK_TEXT], "only the approved fallback may be spoken"


def test_safety_post_check_timeout_fails_closed(block_network):
    safety, tts = TimeoutPostSafety(), RecordingTts()
    out = run_pipeline(_payload(), _stages(safety=safety, tts=tts))
    assert safety.pre_attempts == 1, "pre-check passed normally"
    assert safety.post_attempts == 1, "safety keeps its direct single-call design (no new retry)"
    _assert_contract_valid(out)
    assert out.text == SAFETY_FALLBACK_TEXT
    assert out.safety is not None
    assert out.safety.flagged is True
    assert out.safety.action_taken == SafetyAction.BLOCKED
    assert out.safety.category is None
    assert out.emotion == Emotion.NEUTRAL
    assert out.action == Action.IDLE
    assert tts.received == [SAFETY_FALLBACK_TEXT], "flagged/fail-closed path never speaks the draft"


@pytest.mark.parametrize("stage_kind", ["stt", "llm", "tts", "safety_pre"])
def test_timeout_fallbacks_never_raise_out_of_the_pipeline(stage_kind):
    """Exhausted TimeoutError must be converted, never propagated."""
    cases = {
        "stt": (_audio_payload(), _stages(stt=TimeoutStt())),
        "llm": (_payload(), _stages(llm=TimeoutLlm())),
        "tts": (_payload(), _stages(tts=TimeoutTts())),
        "safety_pre": (_payload(), _stages(safety=TimeoutPreSafety())),
    }
    payload, stages = cases[stage_kind]
    out = run_pipeline(payload, stages)  # must not raise
    _assert_contract_valid(out)
