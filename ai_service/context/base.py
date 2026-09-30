from typing import Protocol

from contract.schemas import ContextBlock, MemoryWrite, RecentTurn


class ContextBundle:
    """Request-provided context, ready for the LLM stage."""

    def __init__(self, recent_turns: list[RecentTurn], memories: list[MemoryWrite]):
        self.recent_turns = recent_turns
        self.memories = memories


class ContextStage(Protocol):
    def run(self, context: ContextBlock) -> ContextBundle: ...
