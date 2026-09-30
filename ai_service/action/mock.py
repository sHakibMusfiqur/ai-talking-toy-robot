from contract.enums import Action

MOCK_ACTION = Action.WAVE


class MockAction:
    def run(self, text: str, intent: str) -> Action:
        _ = text, intent
        return MOCK_ACTION
