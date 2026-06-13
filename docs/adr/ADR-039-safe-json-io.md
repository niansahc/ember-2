# ADR-039: Safe Atomic JSON I/O for the Persistence Layer

**Status:** Accepted
**Date:** 2026-06-13
**Target:** v0.18.1

## Context

The persistence layer read and wrote JSON with bare `open()` + `json.load`/`json.dump`. Two failure modes followed:

- **Crash on read.** `read_memories` called `storage.read_json` with no guard, so a single corrupt or half-written memory file raised `JSONDecodeError` out of the whole read path and failed the chat request. The state and task collection readers already skipped-and-logged bad files; memory, preferences, config, lodestone, and the ingest sites did not, so the behavior was inconsistent across the codebase.
- **Half-written files on write.** A direct `open("w")` + `json.dump` that is interrupted (crash, full disk, power loss) leaves a truncated, unparseable file in place. The next read then hits the crash path above.

A naive "wrap everything and return `{}`" fix would trade a crash for a worse bug: a corrupt canonical record silently read as empty is data loss, and a corrupt ChatGPT export read as `{}` looks like a successful import of zero conversations. Corruption must stay visible.

## Decision

Add `src/core/jsonio.py` with two helpers; route canonical and ingest JSON file I/O through them.

**`safe_read_json(path, *, default=_RAISE)`**
- Returns the parsed object on success.
- `FileNotFoundError` is treated as normal (e.g. first run): return `default` silently, or raise `JsonIoError` if no default was given. It is never logged as corruption.
- `JSONDecodeError` / other `OSError`: log the path and exception **type** only (never file content -- vault privacy rule), then raise `JsonIoError` if `default` was omitted, else return `default`.
- Callers choose the policy per semantics:
  - **Collection reads** (`read_memories`, `search_memories`) catch `JsonIoError` (or pass `default=None` and skip) so one bad record is skipped, not fatal.
  - **Degradable single-file reads** (preferences, config, gdrive sync index) pass `default={}` and fall back.
  - **Import reads** (ChatGPT export) omit `default` so a corrupt export fails loudly instead of importing zero records.

**`safe_write_json(path, data)`**
- Writes to a uniquely-named temp file in the target's directory (same filesystem keeps the rename atomic; the unique name prevents concurrent background-thread writers from clobbering each other), then `os.replace()` onto the target. A reader always sees the old file or the new one, never a half-written one.
- On `OSError` (including a failing `mkdir`/`mkstemp`): clean up the temp file, log, and raise `JsonIoError`. A failed canonical write must be visible, not silent.
- No `fsync`: the goal is atomicity (no corruption), not power-loss durability, which is not worth the per-write cost for a local single-user app.

### Scope

Routed now (canonical-data-first): `memory/storage.py` R/W, `core/preferences.py` R/W, `core/config.py` model-override R/W, `tasks/task_service.py` R/W, `memory/lodestone_service.py` read, `ingest/importers/chatgpt.py` export read, `ingest/importers/gdrive_sync.py` R/W.

Deferred (filed as **B-IO-001** in KNOWN_ISSUES): `retrieval/vector_index.py` R/W (derived/rebuildable), `ingest/writers.py` chunk write, and `state_service.write()` atomicity (entangled with the open A2 PR #87; lands after it merges). Out of scope entirely: append-only `.jsonl` log writers (different semantics), `json.loads` of LLM/SSE strings (not file I/O), and `main.py` cascade soft-delete (an in-place-mutation / append-only concern, already `try/except`-wrapped).

## Consequences

- **One corrupt file no longer crashes a read.** Collection reads degrade (skip + log); single-file reads fall back to a default; imports fail loudly. Corruption is always surfaced (logged or raised), never silent.
- **Writes are atomic.** An interrupted write can no longer leave a half-written canonical record.
- **One definition of the policy.** Future persistence code uses these helpers instead of re-deriving error handling per call site. New canonical/derived vault JSON I/O must route through them.
- **Privacy-safe logging.** Failures log path + exception type only; file content never reaches logs.
- **Partial coverage by design.** B-IO-001 tracks the deferred sites so the gap is visible rather than implied-complete.

## References

- `src/core/jsonio.py` -- the helpers.
- `docs/KNOWN_ISSUES.md` -- B-IO-001 (deferred unguarded sites).
- `src/memory/storage.py`, `src/memory/read_memory.py`, `src/memory/search_memory.py`, `src/core/preferences.py`, `src/core/config.py`, `src/tasks/task_service.py`, `src/memory/lodestone_service.py`, `src/ingest/importers/chatgpt.py`, `src/ingest/importers/gdrive_sync.py` -- routed call sites.
- CLAUDE.md -- append-only memory, rebuildable derived artifacts, and vault privacy rules.
