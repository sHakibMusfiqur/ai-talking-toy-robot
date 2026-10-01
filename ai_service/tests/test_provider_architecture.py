import importlib.util
from pathlib import Path

from action.mock import MockAction
from contract.enums import Action, Emotion, Language, SafetyAction
from contract.pipeline import PipelineStages, default_stages, run_pipeline
from contract.schemas import AIInputContract, AIOutputContract
from context.base import ContextBundle
from context.stub import RequestContext
from emotion.mock import MockEmotion
from lang_detect.interface import LanguageDetectResult
from lang_detect.mock import MockLangDetect
from llm.interface import LlmResult
from llm.mock import MockLlm
from safety.interface import SafetyResult
from safety.mock import MockSafety
from stt.interface import SttResult
from stt.mock import MockStt
from tts.mock import MockTts
from understanding.base import IntentResult
from understanding.mock import MockUnderstanding

from config import (
    COMPONENTS,
    DEFAULT_PROVIDER,
    DEFAULT_RETRY_BACKOFF_SECONDS,
    DEFAULT_RETRY_COUNT,
    DEFAULT_TIMEOUT_SECONDS,
    load_provider_settings,
)
from retry import DEFAULT_RETRY_POLICY

AI_SERVICE_ROOT = Path(__file__).resolve().parents[1]

PROVIDER_SDK_MODULES = (
    "openai",
    "anthropic",
    "groq",
    "elevenlabs",
    "coqui",
    "deepgram",
)


def _payload(**overrides) -> AIInputContract:
    payload = {
        "conversation_id": "conv-arch",
        "child_profile_id": "child-1",
        "robot_id": "robot-1",
        "text": "Hello",
        "language_hint": "en",
        "context": {"recent_turns": [], "memories": []},
    }
    payload.update(overrides)
    return AIInputContract(**payload)


# --- mock stage contract types -------------------------------------------


def test_mock_stages_return_expected_contract_types():
    from context.base import ContextBlock

    stt = MockStt().run(b"\x01", "en")
    assert isinstance(stt, SttResult)
    llm = MockLlm().run("hi", "small_talk", ContextBundle([], []), "conversation")
    assert isinstance(llm, LlmResult)
    safety = MockSafety().pre_check("hi", "small_talk")
    assert isinstance(safety, SafetyResult)
    detected = MockLangDetect().run("hello", None)
    assert isinstance(detected, LanguageDetectResult)
    assert isinstance(MockUnderstanding().run("hi", "conversation"), IntentResult)
    assert isinstance(MockEmotion().run("hi", "small_talk"), Emotion)
    assert isinstance(MockAction().run("hi", "small_talk"), Action)
    assert MockTts().run("hi", "en", Emotion.HAPPY) is None


# --- injected provider implementations -----------------------------------


class InjectedStt:
    def run(self, audio, language_hint):
        return SttResult(text="injected transcript", language="bn", confidence=0.98)


class InjectedLangDetect:
    def run(self, text, language_hint):
        return LanguageDetectResult(language=Language.BN, confidence=1.0)


class InjectedUnderstanding:
    def run(self, text, mode):
        return IntentResult(intent="injected_intent")


class InjectedContext:
    def run(self, context):
        return ContextBundle(recent_turns=list(context.recent_turns), memories=list(context.memories))


class InjectedSafety:
    def pre_check(self, text, intent):
        return SafetyResult(flagged=False, category=None, action_taken=SafetyAction.ALLOWED)

    def post_check(self, text):
        return SafetyResult(flagged=False, category=None, action_taken=SafetyAction.ALLOWED)


class InjectedLlm:
    def run(self, text, intent, context, mode):
        return LlmResult(text="injected reply", language="bn")


class InjectedEmotion:
    def run(self, text, intent):
        return Emotion.THINKING


class InjectedAction:
    def run(self, text, intent):
        return Action.NOD


class RecordingTts:
    def __init__(self):
        self.calls = []

    def run(self, text, language, emotion):
        self.calls.append((text, language, emotion))
        return "aW5qZWN0ZWQtYXVkaW8="


def test_pipeline_accepts_fully_injected_implementations():
    tts = RecordingTts()
    stages = PipelineStages(
        stt=InjectedStt(),
        lang_detect=InjectedLangDetect(),
        understanding=InjectedUnderstanding(),
        context=InjectedContext(),
        safety=InjectedSafety(),
        llm=InjectedLlm(),
        emotion=InjectedEmotion(),
        action=InjectedAction(),
        tts=tts,
    )
    out = run_pipeline(_payload(), stages)
    assert isinstance(out, AIOutputContract)
    assert out.text == "injected reply"
    assert out.emotion == Emotion.THINKING
    assert out.action == Action.NOD
    assert out.language == Language.BN
    assert out.audio == "aW5qZWN0ZWQtYXVkaW8="
    assert tts.calls == [("injected reply", "bn", Emotion.THINKING)]
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


def test_default_stages_runs_fully_offline(block_network):
    out = run_pipeline(_payload(), default_stages())
    assert isinstance(out, AIOutputContract)


# --- lang_detect interface ------------------------------------------------


def test_lang_detect_hints_win():
    assert MockLangDetect().run("hello", "en").language == Language.EN
    assert MockLangDetect().run("hello", "bn").language == Language.BN


def test_lang_detect_script_detection_when_hint_auto_or_missing():
    bengali = "\u0995\u09c7\u09ae\u09cd\u09ac\u09be"
    assert MockLangDetect().run(bengali, "auto").language == Language.BN
    assert MockLangDetect().run(bengali, None).language == Language.BN
    assert MockLangDetect().run("just english", "auto").language == Language.EN
    assert MockLangDetect().run("", None).language == Language.EN


def test_lang_detect_result_shape():
    result = MockLangDetect().run("hello", None)
    assert set(result.model_dump()) == {"language", "confidence"}
    assert 0.0 <= result.confidence <= 1.0


def test_lang_detect_language_flows_into_text_turn_output():
    stages = PipelineStages(
        stt=MockStt(),
        lang_detect=MockLangDetect(),
        understanding=MockUnderstanding(),
        context=RequestContext(),
        safety=MockSafety(),
        llm=MockLlm(),
        emotion=MockEmotion(),
        action=MockAction(),
        tts=MockTts(),
    )
    out_en = run_pipeline(_payload(language_hint="en"), stages)
    out_bn = run_pipeline(_payload(language_hint="bn"), stages)
    assert out_en.language == Language.EN
    assert out_bn.language == Language.BN


# --- provider-neutral configuration --------------------------------------


def test_configuration_defaults_with_no_env():
    settings = load_provider_settings("stt", env={})
    assert settings.provider == DEFAULT_PROVIDER == "mock"
    assert settings.model is None
    assert settings.endpoint is None
    assert settings.timeout_seconds == DEFAULT_TIMEOUT_SECONDS
    assert settings.retry_count == DEFAULT_RETRY_COUNT
    assert settings.retry_backoff_seconds == DEFAULT_RETRY_BACKOFF_SECONDS
    assert settings.retry_policy().retry_count == DEFAULT_RETRY_COUNT


def test_configuration_reads_all_neutral_keys_without_real_values():
    env = {
        "AI_LLM_PROVIDER": "mock",
        "AI_LLM_MODEL": "local-model-name",
        "AI_LLM_ENDPOINT": "http://localhost:9999/internal",
        "AI_LLM_TIMEOUT_SECONDS": "12.5",
        "AI_LLM_RETRY_COUNT": "2",
        "AI_LLM_RETRY_BACKOFF_SECONDS": "0.25",
    }
    settings = load_provider_settings("llm", env=env)
    assert settings.provider == "mock"
    assert settings.model == "local-model-name"
    assert settings.endpoint == "http://localhost:9999/internal"
    assert settings.timeout_seconds == 12.5
    assert settings.retry_count == 2
    assert settings.retry_backoff_seconds == 0.25
    policy = settings.retry_policy()
    assert (policy.timeout_seconds, policy.retry_count, policy.backoff_seconds) == (12.5, 2, 0.25)


def test_configuration_empty_strings_fall_back_to_defaults():
    env = {"AI_TTS_PROVIDER": "", "AI_TTS_TIMEOUT_SECONDS": "", "AI_TTS_RETRY_COUNT": ""}
    settings = load_provider_settings("tts", env=env)
    assert settings.provider == "mock"
    assert settings.timeout_seconds == DEFAULT_TIMEOUT_SECONDS
    assert settings.retry_count == DEFAULT_RETRY_COUNT


def test_configuration_malformed_number_fails_safely():
    try:
        load_provider_settings("stt", env={"AI_STT_TIMEOUT_SECONDS": "soon"})
    except ValueError as exc:
        assert "AI_STT_TIMEOUT_SECONDS" in str(exc)
    else:
        raise AssertionError("malformed timeout must raise ValueError")


def test_configuration_rejects_unknown_component():
    try:
        load_provider_settings("safety", env={})
    except ValueError as exc:
        assert "safety" in str(exc)
    else:
        raise AssertionError("unknown component must raise ValueError")


def test_configuration_covers_only_provider_capable_components():
    assert COMPONENTS == ("stt", "llm", "tts")


# --- no SDK / no credentials ---------------------------------------------


def test_no_provider_sdk_is_installed():
    for name in PROVIDER_SDK_MODULES:
        assert importlib.util.find_spec(name) is None, f"unexpected provider SDK installed: {name}"


def test_requirements_contain_only_runtime_neutral_packages():
    lines = [
        line.strip()
        for line in (AI_SERVICE_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert all(line.split("==")[0] in {"fastapi", "uvicorn"} for line in lines)


def test_default_pipeline_requires_no_credentials(block_network, monkeypatch):
    for key in list(__import__("os").environ):
        if key.startswith(("AI_STT_", "AI_LLM_", "AI_TTS_")):
            monkeypatch.delenv(key, raising=False)
    out = run_pipeline(_payload(), default_stages())
    assert out.text
    assert out.audio is None
