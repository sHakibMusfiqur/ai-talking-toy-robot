import base64

from action.mock import MockAction
from contract.enums import Action, Emotion, SafetyAction
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
from errors import ProviderError
from lang_detect.mock import MockLangDetect
from llm.mock import MockLlm
from retry import RetryPolicy, run_with_retry
from safety.mock import MockSafety
from stt.mock import MockStt
from tts.mock import MockTts
from understanding.mock import MockUnderstanding

INSTANT = RetryPolicy(timeout_seconds=5.0, retry_count=1, backoff_seconds=0.0)


def _base_dict(**overrides) -> dict:
    payload = {
        "conversation_id": "conv-fail",
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


def _audio_payload() -> AIInputContract:
    payload = _base_dict()
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


class FailingStt:
    def run(self, audio, language_hint):
        raise ProviderError("stt", "simulated outage")


class FailingLlm:
    def run(self, text, intent, context, mode):
        raise ProviderError("llm", "simulated outage")


class FailingTts:
    def run(self, text, language, emotion):
        raise ProviderError("tts", "simulated outage")


class FailingPreSafety:
    def pre_check(self, text, intent):
        raise ProviderError("safety", "simulated outage")

    def post_check(self, text):
        return MockSafety().post_check(text)


class FlaggingPostSafety:
    def pre_check(self, text, intent):
        return MockSafety().pre_check(text, intent)

    def post_check(self, text):
        from safety.interface import SafetyResult

        return SafetyResult(
            flagged=True,
            category="UNSAFE_TOPIC",
            action_taken=SafetyAction.BLOCKED,
        )


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


# --- retry mechanism (no network) ----------------------------------------


def _counter(failures, result="ok"):
    state = {"attempts": 0, "sleeps": []}

    def op():
        state["attempts"] += 1
        if state["attempts"] <= failures:
            raise ProviderError("llm", "transient")
        return result

    return state, op


def test_retry_succeeds_after_transient_failure():
    state, op = _counter(failures=1)
    sleeps = []
    out = run_with_retry(op, RetryPolicy(retry_count=1, backoff_seconds=0.25), sleep=sleeps.append)
    assert out == "ok"
    assert state["attempts"] == 2
    assert sleeps == [0.25]


def test_retry_backoff_schedule_is_exponential():
    state, op = _counter(failures=5)
    sleeps = []
    try:
        run_with_retry(op, RetryPolicy(retry_count=2, backoff_seconds=0.5), sleep=sleeps.append)
    except ProviderError:
        pass
    assert sleeps == [0.5, 1.0]


def test_retry_exhaustion_reraises_provider_error():
    state, op = _counter(failures=99)
    sleeps = []
    try:
        run_with_retry(op, RetryPolicy(retry_count=1, backoff_seconds=0.1), sleep=sleeps.append)
    except ProviderError:
        raised = True
    else:
        raised = False
    assert raised
    assert state["attempts"] == 2  # first attempt + one retry (Section 16)


def test_retry_count_zero_never_retries():
    state, op = _counter(failures=1)
    sleeps = []
    try:
        run_with_retry(op, RetryPolicy(retry_count=0, backoff_seconds=0.1), sleep=sleeps.append)
    except ProviderError:
        raised = True
    else:
        raised = False
    assert raised
    assert state["attempts"] == 1
    assert sleeps == []


def test_timeout_error_is_retryable():
    attempts = {"n": 0}

    def op():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise TimeoutError("simulated slow provider")
        return "recovered"

    assert run_with_retry(op, INSTANT, sleep=lambda _: None) == "recovered"
    assert attempts["n"] == 2


def test_unrelated_errors_are_not_retried():
    attempts = {"n": 0}

    def op():
        attempts["n"] += 1
        raise ValueError("programming error")

    try:
        run_with_retry(op, INSTANT, sleep=lambda _: None)
    except ValueError:
        raised = True
    else:
        raised = False
    assert raised
    assert attempts["n"] == 1


def test_default_retry_policy_matches_source_of_truth():
    # docs/01 Section 16: retried once with backoff.
    from config import load_provider_settings

    assert load_provider_settings("llm", env={}).retry_count == 1
    policy = load_provider_settings("llm", env={}).retry_policy()
    assert policy.retry_count == 1
    assert policy.backoff_seconds > 0


# --- stage failure fallbacks (contract-respecting) ------------------------


def test_stt_provider_failure_falls_back_to_retry_prompt():
    out = run_pipeline(_audio_payload(), _stages(stt=FailingStt()))
    assert isinstance(out, AIOutputContract)
    assert out.text == STT_FALLBACK_TEXT
    assert out.action == Action.LISTENING_RETRY
    assert out.emotion == Emotion.NEUTRAL
    assert out.safety is None


def test_llm_provider_failure_returns_generic_safe_fallback():
    out = run_pipeline(_payload(), _stages(llm=FailingLlm()))
    assert isinstance(out, AIOutputContract)
    assert out.text == LLM_FALLBACK_TEXT
    assert out.emotion == Emotion.NEUTRAL
    assert out.action == Action.IDLE
    assert out.safety.flagged is False
    assert out.safety.category is None
    assert out.safety.action_taken == SafetyAction.ALLOWED
    assert set(out.model_dump()) == {
        "conversation_id",
        "text",
        "language",
        "emotion",
        "action",
        "safety",
        "audio",
        "memory_writes",
    }


def test_tts_provider_failure_degrades_to_null_audio():
    out = run_pipeline(_payload(), _stages(tts=FailingTts()))
    assert isinstance(out, AIOutputContract)
    assert out.audio is None
    assert out.text == "Hello! I am happy to talk with you."
    assert out.emotion == Emotion.HAPPY
    assert out.action == Action.WAVE


def test_safety_provider_failure_fails_closed_before_llm():
    llm = SpyLlm()
    out = run_pipeline(_payload(), _stages(safety=FailingPreSafety(), llm=llm))
    assert llm.calls == 0, "fail-closed safety must never reach the LLM"
    assert out.text == SAFETY_FALLBACK_TEXT
    assert out.safety.flagged is True
    assert out.safety.action_taken == SafetyAction.BLOCKED
    assert out.safety.category is None
    assert out.emotion == Emotion.NEUTRAL
    assert out.action == Action.IDLE


def test_failure_fallbacks_run_without_network(block_network):
    for stages in (
        _stages(stt=FailingStt()),
        _stages(llm=FailingLlm()),
        _stages(tts=FailingTts()),
        _stages(safety=FailingPreSafety()),
    ):
        payload = _audio_payload() if stages.stt.__class__ is FailingStt else _payload()
        out = run_pipeline(payload, stages)
        assert isinstance(out, AIOutputContract)


# --- safety gates vs TTS -------------------------------------------------


def test_unsafe_llm_output_never_reaches_tts():
    tts = RecordingTts()
    out = run_pipeline(_payload(), _stages(safety=FlaggingPostSafety(), tts=tts))
    assert out.text == SAFETY_FALLBACK_TEXT
    assert tts.received == [SAFETY_FALLBACK_TEXT]
    assert "Hello! I am happy to talk with you." not in tts.received


def test_pre_safety_blocks_llm_and_tts_gets_only_fallback():
    llm = SpyLlm()
    tts = RecordingTts()

    class FlaggingPreSafety:
        def pre_check(self, text, intent):
            from safety.interface import SafetyResult

            return SafetyResult(
                flagged=True,
                category="UNSAFE_TOPIC",
                action_taken=SafetyAction.REDIRECTED,
            )

        def post_check(self, text):
            return MockSafety().post_check(text)

    out = run_pipeline(_payload(), _stages(safety=FlaggingPreSafety(), llm=llm, tts=tts))
    assert llm.calls == 0
    assert tts.received == [SAFETY_FALLBACK_TEXT]
    assert out.text == SAFETY_FALLBACK_TEXT
    assert out.safety.flagged is True
