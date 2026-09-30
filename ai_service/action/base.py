from typing import Protocol

from contract.enums import Action


class ActionStage(Protocol):
    def run(self, text: str, intent: str) -> Action: ...
