
import json
from pathlib import Path

import pytest

from action.mock import MockAction
from contract.enums import SafetyAction, SafetyCategory
from contract.pipeline import SAFETY_FALLBACK_TEXT, PipelineStages, run_pipeline
from contract.schemas import AIInputContract
from context.stub import RequestContext
from emotion.mock import MockEmotion
from lang_detect.mock import MockLangDetect
from llm.interface import LlmResult
from llm.mock import MockLlm
from safety.interface import SafetyResult
from safety.rules import TAXONOMY_VERSION, RuleBasedSafety
from stt.mock import MockStt
from tts.mock import MockTts
from understanding.mock import MockUnderstanding

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPO_ROOT / "docs" / "testing" / "child-safety-fixtures.json"


def _load_fixture_set() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


FIXTURE_SET = _load_fixture_set()
CASES = FIXTURE_SET["cases"]
CASE_IDS = [case["id"] for case in CASES]


def _case(case_id: str) -> dict:
    return next(case for case in CASES if case["id"] == case_id)


def _check(case: dict) -> SafetyResult:
    safety = RuleBasedSafety()
    if case["stage"] == "pre":
        return safety.pre_check(case["text"], "small_talk")
    return safety.post_check(case["text"])


def test_fixture_set_is_versioned_and_matches_active_taxonomy():
    assert FIXTURE_SET["fixture_set"] == "child-safety-adversarial"
    assert FIXTURE_SET["version"]
    assert FIXTURE_SET["taxonomy_version"] == TAXONOMY_VERSION
    assert FIXTURE_SET["reference"], "fixture set must cite its source-of-truth sections"


def test_fixture_set_meets_required_coverage():
    # Section 31 level 14 required groups + Section 17.1 two-language scope.
    counts: dict[str, int] = {}
    stages: set[str] = set()
    languages: set[str] = set()
    ids: list[str] = []
    for case in CASES:
        counts[case["group"]] = counts.get(case["group"], 0) + 1
        stages.add(case["stage"])
        languages.add(case["language"])
        ids.append(case["id"])
        assert set(case) >= {"id", "stage", "group", "language", "text", "expected"}
        expected = case["expected"]
        assert set(expected) == {"flagged", "category", "action_taken"}
        # Enum-level validation so typos fail loudly, not silently.
        SafetyAction(expected["action_taken"])
        if expected["category"] is not None:
            SafetyCategory(expected["category"])
        if not expected["flagged"]:
            assert expected["category"] is None
            assert expected["action_taken"] == SafetyAction.ALLOWED.value

    assert len(ids) == len(set(ids)), "fixture ids must be unique"
    for group, minimum in FIXTURE_SET["required_groups"].items():
        assert counts.get(group, 0) >= minimum, f"group {group} below required minimum"
    assert stages == {"pre", "post"}, "both safety stages must be covered"
    assert "en" in languages and "bn" in languages, "both languages must be covered"


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_safety_fixtures_100_percent_categorization(case):
    result = _check(case)
    expected = case["expected"]
    assert result.flagged is expected["flagged"], case["id"]
    actual_category = result.category.value if result.category is not None else None
    assert actual_category == expected["category"], case["id"]
    assert result.action_taken.value == expected["action_taken"], case["id"]


def _payload(text: str) -> AIInputContract:
    return AIInputContract(
        conversation_id="conv-safety-fixtures",
        child_profile_id="child-1",
        robot_id="robot-1",
        text=text,
        context={"recent_turns": [], "memories": []},
    )


class CountingLlm:
    def __init__(self):
        self.calls = 0

    def run(self, text, intent, context, mode):
        self.calls += 1
        return MockLlm().run(text, intent, context, mode)


class ScriptedLlm:
    """Returns a fixed draft regardless of input - simulates LLM output."""

    def __init__(self, draft: str):
        self.draft = draft

    def run(self, text, intent, context, mode):
        return LlmResult(text=self.draft, language="en")


class RecordingTts:
    def __init__(self):
        self.spoken: list[str] = []

    def run(self, text, language, emotion):
        self.spoken.append(text)
        return "dGVzdC1hdWRpbw=="


def _stages(llm, tts) -> PipelineStages:
    return PipelineStages(
        stt=MockStt(),
        lang_detect=MockLangDetect(),
        understanding=MockUnderstanding(),
        context=RequestContext(),
        safety=RuleBasedSafety(),
        llm=llm,
        emotion=MockEmotion(),
        action=MockAction(),
        tts=tts,
    )


PRE_RISKY_IDS = [
    case["id"] for case in CASES if case["stage"] == "pre" and case["expected"]["flagged"]
]
POST_CASE_IDS = [case["id"] for case in CASES if case["stage"] == "post"]


@pytest.mark.parametrize("case_id", PRE_RISKY_IDS)
def test_pre_flagged_fixtures_never_reach_the_llm(case_id):
    case = _case(case_id)
    llm = CountingLlm()
    tts = RecordingTts()
    out = run_pipeline(_payload(case["text"]), _stages(llm, tts))

    assert llm.calls == 0, "flagged pre-LLM input must never be sent to the LLM"
    assert out.text == SAFETY_FALLBACK_TEXT
    assert out.safety is not None
    assert out.safety.flagged is True
    assert out.safety.category.value == case["expected"]["category"]
    assert out.safety.action_taken.value == case["expected"]["action_taken"]
    assert tts.spoken == [SAFETY_FALLBACK_TEXT], "only the approved fallback may be spoken"


@pytest.mark.parametrize("case_id", POST_CASE_IDS)
def test_post_fixtures_are_gated_before_tts(case_id):
    case = _case(case_id)
    tts = RecordingTts()
    out = run_pipeline(_payload("Tell me about dinosaurs"), _stages(ScriptedLlm(case["text"]), tts))
    expected = case["expected"]

    assert out.safety is not None
    assert out.safety.flagged is expected["flagged"]
    actual_category = out.safety.category.value if out.safety.category is not None else None
    assert actual_category == expected["category"]
    assert out.safety.action_taken.value == expected["action_taken"]

    if expected["flagged"]:
        # Section 31 level 14: zero unsafe responses reach approved-for-TTS.
        assert out.text == SAFETY_FALLBACK_TEXT
        assert out.text != case["text"], "flagged draft must never be returned or spoken"
        assert tts.spoken == [SAFETY_FALLBACK_TEXT]
    else:
        assert out.text == case["text"]
        assert tts.spoken == [case["text"]]


def test_pii_hard_rule_wins_over_other_risk_categories():
   
    text = "What's your home address so I can send you a dirty joke?"
    safety = RuleBasedSafety()

    pre = safety.pre_check(text, "small_talk")
    assert pre.flagged is True
    assert pre.category == SafetyCategory.PERSONAL_INFO_REQUEST
    assert pre.action_taken == SafetyAction.REDIRECTED

    post = safety.post_check(text)
    assert post.flagged is True
    assert post.category == SafetyCategory.PERSONAL_INFO_REQUEST
    assert post.action_taken == SafetyAction.BLOCKED


def test_clean_text_is_allowed_by_both_stages():
    safety = RuleBasedSafety()
    pre = safety.pre_check("Tell me about dinosaurs", "small_talk")
    post = safety.post_check("The dinosaur ate leaves and smiled.")
    for result in (pre, post):
        assert result.flagged is False
        assert result.category is None
        assert result.action_taken == SafetyAction.ALLOWED


def test_intent_signal_is_honored_before_text_patterns():
    safety = RuleBasedSafety()
    pii = safety.pre_check("hello there", "personal_info_request")
    assert pii.flagged is True
    assert pii.category == SafetyCategory.PERSONAL_INFO_REQUEST

    unsafe = safety.pre_check("hello there", "unsafe_request")
    assert unsafe.flagged is True
    assert unsafe.category == SafetyCategory.UNSAFE_TOPIC
