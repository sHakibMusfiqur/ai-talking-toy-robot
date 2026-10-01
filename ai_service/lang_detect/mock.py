from typing import Optional

from contract.enums import Language

from .interface import LanguageDetectResult

_BENGALI_RANGE = range(0x0980, 0x0A00)


class MockLangDetect:
    def run(self, text: str, language_hint: Optional[str]) -> LanguageDetectResult:
        if language_hint in ("en", "bn"):
            return LanguageDetectResult(language=Language(language_hint), confidence=1.0)
        has_bengali = any(ord(char) in _BENGALI_RANGE for char in text)
        language = Language.BN if has_bengali else Language.EN
        return LanguageDetectResult(language=language, confidence=0.9)
