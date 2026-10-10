"""
src/retrieval/markers.py

Text markers that identify meta content: prompt scaffolding, transcript
headers and serialized context payloads rather than anything the user
said or wrote.

One list, used on both sides of the index (ultrareview #280):
  - write_memory.should_skip_memory() refuses to store or index text
    containing a marker.
  - semantic_search.should_exclude_result() drops a retrieved record
    containing a marker.
Two copies of the list could drift, so a marker added on one side would
let the other side keep writing or returning the content it targets.

Markers are lowercase; callers match them against normalized (lowercased)
text. The context layer's echo filter (ContextService._is_echo_or_meta_memory)
keeps its own, deliberately different list.

No imports: write_memory and semantic_search both depend on this module,
and it must not depend on either.
"""

META_MARKERS: tuple[str, ...] = (
    "user asked:",
    "ember responded:",
    "assistant responded:",
    "assistant said:",
    "### task:",
    "generate 1-3 broad tags",
    '"user_message":',
    '"memory_items":',
    '"reflection_items":',
    '"conversation_id":',
    '"chunk_id":',
)
