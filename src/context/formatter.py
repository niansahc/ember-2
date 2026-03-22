from src.context.models import ContextItem, ContextPacket
from src.state.models import StateItem


class ContextFormatter:
    def format(
        self,
        user_message: str,
        memory_items: list[ContextItem],
        reflection_items: list[ContextItem],
        state_items: list[StateItem] | None = None,
    ) -> ContextPacket:
        """
        Build a ContextPacket from retrieved and ranked items.
        """
        return ContextPacket(
            user_message=user_message,
            memory_items=memory_items,
            reflection_items=reflection_items,
            state_items=state_items or [],
            summary=None,
        )
