from src.context.models import ContextItem, ContextPacket
from src.state.models import StateItem
from src.tasks.models import TaskItem


class ContextFormatter:
    def format(
        self,
        user_message: str,
        memory_items: list[ContextItem],
        reflection_items: list[ContextItem],
        state_items: list[StateItem] | None = None,
        task_items: list[TaskItem] | None = None,
        web_items: list[dict] | None = None,
        image_data: list[str] | None = None,
    ) -> ContextPacket:
        """
        Build a ContextPacket from retrieved and ranked items.
        """
        return ContextPacket(
            user_message=user_message,
            memory_items=memory_items,
            reflection_items=reflection_items,
            state_items=state_items or [],
            task_items=task_items or [],
            web_items=web_items or [],
            image_data=image_data or [],
            summary=None,
        )
