from typing import Optional

from .base import SttResult

MOCK_TRANSCRIPT = "Hello"
MOCK_CONFIDENCE = 0.99


class MockStt:
    def run(self, audio: bytes, language_hint: Optional[str]) -> SttResult:
       
        _ = audio  # accepted, never sent anywhere
        return SttResult(text=MOCK_TRANSCRIPT, language="en", confidence=MOCK_CONFIDENCE)
