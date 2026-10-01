import re

from contract.enums import SafetyAction, SafetyCategory
from safety.interface import SafetyResult

TAXONOMY_VERSION = "1.0.0"

# Intents understood pre-generation (Section 14.2.1). Deterministic
# understanding emits these; text patterns are the primary signal.
_PII_INTENTS = frozenset({"personal_info_request"})
_UNSAFE_INTENTS = frozenset({"unsafe_request"})

# --- English: personal information (Section 17.1 hard rule) -------------
_PII_EN = (
    re.compile(r"\bwhat(?:'s|\s+is)\s+your\s+(?:home\s+)?address\b", re.I),
    re.compile(r"\byour\s+(?:home\s+)?address\b", re.I),
    re.compile(r"\bwhere\s+do\s+you\s+live\b", re.I),
    re.compile(r"\byou\s+live\s+at\b", re.I),
    re.compile(r"\bwhich\s+school\s+do\s+you\s+go\s+to\b", re.I),
    re.compile(r"\bwhat\s+school\s+do\s+you\s+go\s+to\b", re.I),
    re.compile(r"\b(?:name\s+of\s+your\s+school|your\s+school(?:'s)?\s+name|school\s+name)\b", re.I),
    re.compile(r"\b(?:phone|mobile|cell)\s+number\b", re.I),
    re.compile(r"\b(?:exact|current)\s+(?:location|address)\b", re.I),
    re.compile(r"\bwhere\s+exactly\b", re.I),
)

# --- Bangla: personal information --------------------------------------
_PII_BN = (
    re.compile(r"ঠিকানা"),
    re.compile(r"কোন\s+স্কুল"),
    re.compile(r"স্কুলের\s+নাম"),
    re.compile(r"ফোন\s+নম্বর|মোবাইল\s+নম্বর"),
    re.compile(r"তুমি\s+কোথায়\s+থাক|আপনি\s+কোথায়\s+থাক|কোথায়\s+থাকো"),
)

# --- English: unsafe topics (everything else in Section 17.1) ----------
_UNSAFE_EN = (
    # age-inappropriate / adult content
    re.compile(r"\bdirty\s+(?:joke|word|video|picture|song)s?\b", re.I),
    re.compile(r"\bbad\s+words?\b", re.I),
    re.compile(r"\bswear\s+words?\b", re.I),
    re.compile(r"\b(?:adult|inappropriate)\s+(?:content|joke|jokes|stuff|video|story)\b", re.I),
    re.compile(r"\bfor\s+adults\s+only\b|\bonly\s+for\s+adults\b", re.I),
    re.compile(r"\bporn(?:ography)?\b|\bxxx\b", re.I),
    re.compile(r"\binappropriate\b", re.I),
    # violence
    re.compile(r"\bhurt\s+(?:someone|him|her|anyone|people|a\s+person)\b", re.I),
    re.compile(r"\bkill\s+(?:him|her|someone|anyone|people|a\s+person|them)\b", re.I),
    re.compile(r"\bhit\s+(?:him|her|someone|anyone|them|a\s+person)\b", re.I),
    re.compile(r"\bpunch\s+(?:him|her|someone|anyone|them)\b", re.I),
    re.compile(r"\b(?:how\s+(?:to|do\s+i)\s+)?(?:beat|fight)\s+(?:up\s+)?(?:someone|him|her|anyone)\b", re.I),
    re.compile(r"\bstab\b", re.I),
    # self-harm-adjacent
    re.compile(r"\bkill\s+myself\b|\bhurt\s+myself\b", re.I),
    re.compile(r"\bend\s+my\s+life\b|\bwant\s+to\s+die\b|\btake\s+my\s+own\s+life\b", re.I),
    re.compile(r"\bsuicide\b", re.I),
    # dangerous instructions
    re.compile(r"\bmake\s+a\s+bomb\b", re.I),
    re.compile(r"\bbuild\s+a\s+(?:bomb|gun|weapon)\b", re.I),
    re.compile(r"\blight\s+a\s+fire\b|\bstart\s+a\s+fire\b|\bplay\s+with\s+fire\b", re.I),
    re.compile(r"\bhow\s+to\s+poison\b|\bpoison\s+(?:someone|him|her|a\s+person)\b", re.I),
    re.compile(r"\btouch\s+(?:the\s+)?(?:stove|fire|hot\s+stove)\b", re.I),
    # unsafe challenges
    re.compile(r"\bhold\s+your\s+breath\b|\bstop\s+breathing\b", re.I),
    re.compile(r"don['\u2019]?t[\s-]+breathe|\bchoking\s+challenge\b", re.I),
    # manipulation / grooming patterns
    re.compile(r"\bdon['\u2019]?t\s+tell\s+(?:your\s+)?(?:mom|mum|dad|parents|mother|father|family|anyone)\b", re.I),
    re.compile(r"\bkeep\s+(?:it\s+|this\s+)?(?:as\s+)?a\s+secret\s+(?:from|between)\b", re.I),
    re.compile(r"\bit['\u2019]?\s*s\s+(?:our|a)\s+secret\b", re.I),
    re.compile(r"\b(?:mom|parents)\s+(?:don['\u2019]?t|do\s+not)\s+need\s+to\s+know\b", re.I),
    re.compile(r"\bkeep\s+this\s+between\s+us\b", re.I),
    re.compile(r"\bsend\s+me\s+(?:a\s+|your\s+)?(?:picture|photo|selfie)\b", re.I),
    re.compile(r"\bwhat\s+are\s+you\s+wearing\b", re.I),
    re.compile(r"\bmeet\s+me\s+(?:alone|somewhere)\b", re.I),
)

# --- Bangla: unsafe topics ---------------------------------------------
_UNSAFE_BN = (
    re.compile(r"অশ্লীল|নোংরা"),
    re.compile(r"মানুষকে\s+মারা|মেরে\s+ফেল|লড়াই\s+করা"),
    re.compile(r"আত্মহত্যা|মরে\s+যেতে\s+চাই|মরতে\s+চাই"),
    re.compile(r"কীভাবে\s+বোমা|আগুনে\s+খেলা|কীভাবে\s+ছুরি"),
    re.compile(r"দম\s+ধরে|শ্বাস\s+(?:ধরে|বন্ধ|থামা)"),
    re.compile(r"কাউকে\s+বলো\s+না|কেউকে\s+বলো\s+না|গোপন\s+রাখ|গোপন\s+কথা"),
    re.compile(r"মা-বাবাকে[^।]*বলো\s+না"),
    re.compile(r"ছবি\s+পাঠাও"),
)

# --- external links (Section 17.1: never in any robot response) --------
_URL = (
    re.compile(r"https?://", re.I),
    re.compile(r"\bwww\.", re.I),
    re.compile(r"\b[\w-]+\.(?:com|org|net|io|bd|edu|gov|co|info|xyz)\b", re.I),
    re.compile(r"লিংক"),
)


def _find(pattern_groups, text: str):
    for group in pattern_groups:
        for pattern in group:
            if pattern.search(text):
                return True
    return False


def _evaluate(text: str, intent: str, stage_action: SafetyAction) -> SafetyResult:
    """PII hard rule first, then unsafe topic, then external link."""
    probe = (text or "").strip()
    intent_key = (intent or "").strip().lower()

    if intent_key in _PII_INTENTS or _find((_PII_EN, _PII_BN), probe):
        return SafetyResult(
            flagged=True,
            category=SafetyCategory.PERSONAL_INFO_REQUEST,
            action_taken=stage_action,
        )
    if intent_key in _UNSAFE_INTENTS or _find((_UNSAFE_EN, _UNSAFE_BN), probe):
        return SafetyResult(
            flagged=True,
            category=SafetyCategory.UNSAFE_TOPIC,
            action_taken=stage_action,
        )
    if _find((_URL,), probe):
        return SafetyResult(
            flagged=True,
            category=SafetyCategory.UNSAFE_TOPIC,
            action_taken=stage_action,
        )
    return SafetyResult(flagged=False, category=None, action_taken=SafetyAction.ALLOWED)


class RuleBasedSafety:
    """Deterministic ``SafetyStage`` used as the default implementation."""

    def pre_check(self, text: str, intent: str) -> SafetyResult:
        # Section 14.2.1: flagged input never reaches the LLM; the
        # pipeline answers with the pre-approved redirect.
        return _evaluate(text, intent, SafetyAction.REDIRECTED)

    def post_check(self, text: str) -> SafetyResult:
        # Section 14.2.2: last gate before TTS; flagged draft is
        # replaced, never spoken.
        return _evaluate(text, "", SafetyAction.BLOCKED)
