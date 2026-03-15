def write_session_summary(memory_writer, recent_messages):
    """
    Store a short summary of the current session so Ember
    can recall what was worked on recently.
    """

    if not recent_messages:
        return

    # simple summary: last user message
    summary = f"Session summary: {recent_messages[-1]}"

    memory_writer.write_memory(
        text=summary,
        memory_type="journal",
        source="session_summary",
        tags=["session"]
    )