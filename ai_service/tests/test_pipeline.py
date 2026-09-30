import base64
import re
from pathlib import Path

from action.mock import MockAction
from contract.enums import Action, Emotion, SafetyAction
from contract.pipeline import (
    SAFETY_FALLBACK_TEXT,
    STT_FALLBACK_TEXT,
    PipelineStages,
    run_pipeline,
)
from contract.schemas import AIInputContract
from context.stub import RequestContext
from emotion.mock import MockEmotion
from llm.mock import MockLlm
from safety.base import SafetyResult
from safety.mock import MockSafety
from stt.base import SttResult
from stt.mock import MockStt
from tts.mock import MockTts
from understanding.mock import MockUnderstanding

AI_SERVICE_ROOT = Path(__file__).resolve().parents[1]

BANNED_PROVIDER_TOKENS = (
    "openai",
    "anthropic",
    "gemini",
    "groq",
    "openrouter",
    "elevenlabs",
    "azure",
    "whisper",
    "huggingface",
    "langchain",
    "torch",
    "transformers",
    "sk-",
    "https://api.",
)


def _stages(safety, llm=None, understanding=None, stt=None) -> PipelineStages:
    return PipelineStages(
        stt=stt or MockStt(),
        understanding=understanding or MockUnderstanding(),
        context=RequestContext(),
        safety=safety,
        llm=llm or MockLlm(),
        emotion=MockEmotion(),
        action=MockAction(),
        tts=MockTts(),
    )


def _payload(**overrides) -> AIInputContract:
    payload = {
        "conversation_id": "conv-pipeline",
        "child_profile_id": "child-1",
        "robot_id": "robot-1",
        "text": "Hello",
        "context": {"recent_turns": [], "memories": []},
    }
    payload.update(overrides)
    return AIInputContract(**payload)


def _audio_payload() -> AIInputContract:
    return _payload(text=None, audio=base64.b64encode(b"\x01\x02").decode())


class SpyLlm:
    def __init__(self):
        self.calls = 0

    def run(self, text, intent, context, mode):
        self.calls += 1
        return MockLlm().run(text, intent, context, mode)


class SpyUnderstanding:
    def __init__(self):
        self.calls = 0

    def run(self, text, mode):
        self.calls += 1
        return MockUnderstanding().run(text, mode)


class RecordingSafety:
    def __init__(self):
        self.pre_calls = 0
        self.post_calls = 0

    def pre_check(self, text, intent):
        self.pre_calls += 1
        return MockSafety().pre_check(text, intent)

    def post_check(self, text):
        self.post_calls += 1
        return MockSafety().post_check(text)


class FlaggingPreSafety:
    def pre_check(self, text, intent):
        return SafetyResult(
            flagged=True,
            category="UNSAFE_TOPIC",
            action_taken=SafetyAction.REDIRECTED,
        )

    def post_check(self, text):
        return MockSafety().post_check(text)


class FlaggingPostSafety:
    def pre_check(self, text, intent):
        return MockSafety().pre_check(text, intent)

    def post_check(self, text):
        return SafetyResult(
            flagged=True,
            category="PERSONAL_INFO_REQUEST",
            action_taken=SafetyAction.BLOCKED,
        )


class LowConfidenceStt:
    def run(self, audio, language_hint):
        return SttResult(text="", language="en", confidence=0.1)


# Note: the low-confidence STT path intentionally skips safety entirely
# (Section 15.1 fallback, safety=null) - this covers the normal turns.
def test_text_and_audio_turns_route_through_both_safety_stages():
    safety = RecordingSafety()
    stages = _stages(safety)

    run_pipeline(_payload(), stages)
    assert safety.pre_calls == 1
    assert safety.post_calls == 1

    run_pipeline(_audio_payload(), stages)
    assert safety.pre_calls == 2
    assert safety.post_calls == 2


def test_pre_stage_flag_blocks_llm_and_returns_fallback():
    llm = SpyLlm()
    out = run_pipeline(_payload(), _stages(FlaggingPreSafety(), llm=llm))
    assert llm.calls == 0, "flagged pre-LLM request must never reach the LLM"
    assert out.text == SAFETY_FALLBACK_TEXT
    assert out.safety.flagged is True
    assert out.safety.category.value == "UNSAFE_TOPIC"
    assert out.emotion == Emotion.NEUTRAL
    assert out.action == Action.IDLE


def test_post_stage_flag_replaces_llm_output():
    out = run_pipeline(_payload(), _stages(FlaggingPostSafety()))
    assert out.text == SAFETY_FALLBACK_TEXT
    assert out.text != "Hello! I am happy to talk with you."
    assert out.safety.flagged is True
    assert out.safety.category.value == "PERSONAL_INFO_REQUEST"
    assert out.safety.action_taken == SafetyAction.BLOCKED


def test_low_confidence_stt_falls_back_without_llm():
    llm = SpyLlm()
    understanding = SpyUnderstanding()
    out = run_pipeline(
        _audio_payload(),
        _stages(MockSafety(), llm=llm, understanding=understanding, stt=LowConfidenceStt()),
    )
    assert out.text == STT_FALLBACK_TEXT
    assert out.action == Action.LISTENING_RETRY
    assert out.emotion == Emotion.NEUTRAL
    assert out.safety is None
    assert llm.calls == 0
    assert understanding.calls == 0


def test_source_contains_no_real_provider_imports_or_endpoints():
    offenders = []
    for path in AI_SERVICE_ROOT.rglob("*.py"):
        if "tests" in path.parts:
            continue  # the test suite itself names the banned tokens
        source = path.read_text(encoding="utf-8").lower()
        for token in BANNED_PROVIDER_TOKENS:
            if token in source:
                offenders.append(f"{path.name}: {token}")
    assert offenders == []
    requirements = (AI_SERVICE_ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    for token in BANNED_PROVIDER_TOKENS[:11]:
        assert token not in requirements


def test_context_module_has_no_database_access():
    # Forbidden: importing DB/client libraries or embedding connection strings.
    # (Docstrings may *mention* Django — it pre-fetches context for us.)
    forbidden = re.compile(
        r"^\s*(?:import|from)\s+(pymongo|motor|sqlalchemy|django|mysql|redis|asyncpg)\b"
        r"|mongodb://|postgres(?:ql)?://|mysql://|redis://",
        re.M | re.I,
    )
    offenders = [
        path.name
        for path in (AI_SERVICE_ROOT / "context").glob("*.py")
        if forbidden.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
