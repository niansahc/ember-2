from src.context.models import ContextPacket, ContextItem


class ContextFormatter:
    def format(
        self,
        user_message: str,
        memory_items: list[ContextItem],
        reflection_items: list[ContextItem],
    ) -> ContextPacket:
        """
        Build a ContextPacket from retrieved and ranked items.
        """
        return ContextPacket(
            user_message=user_message,
            memory_items=memory_items,
            reflection_items=reflection_items,
            summary=None,
        )
    