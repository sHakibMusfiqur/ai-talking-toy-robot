from contract.schemas import ContextBlock

from .base import ContextBundle


class RequestContext:
    def run(self, context: ContextBlock) -> ContextBundle:
        return ContextBundle(
            recent_turns=list(context.recent_turns),
            memories=list(context.memories),
        )
