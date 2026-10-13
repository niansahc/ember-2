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

## Amendment (2026-10-12, exclusive create for append-only records)

`StateService.write()` and `TaskService.write()` guarded append-only with `if file_path.exists(): warn; return file_path` before `safe_write_json`. On a taken name the new record was dropped while the caller got a path back and believed it was written, and `warnings.warn` never reached the API log. The check was also separate from the publish: two writers (threads, or the API and a CLI script with their own timestamp sources) could both pass it, and the second `os.replace` overwrote the first.

**`safe_create_json(path, data)`** is added for records that must never replace an existing file:
- Same temp file as `safe_write_json`, then `os.link(tmp, path)` and the temp file is removed. A hard link fails with `FileExistsError` when the target exists, so the existence check and the publish are one atomic step on NTFS and POSIX filesystems.
- A taken name raises `RecordExistsError` (a `FileExistsError` subclass); nothing is written and the existing file is untouched. Other failures raise `JsonIoError` as before.
- Where hard links are unsupported (FAT/exFAT, some network shares) it falls back to an `O_EXCL` create, logged once per process at WARNING: still exclusive, but a reader can briefly see a partly written file.

`StateService.write()` and `TaskService.write()` route through it, log the collision at ERROR at the fix point (the task log names the timestamp only, since the filename carries a slug of the title), and re-raise. Callers decide what a collision means: the API maps `RecordExistsError` to 409 with one app-level exception handler; background state extraction loses only the colliding record and keeps writing the rest of the turn; onboarding logs and continues (the step is asked again); `scripts/add_state.py` prints a one-line error and exits 1. The remaining record writers (`write_memory`, session, project, lodestone) still use `safe_write_json` and move to `safe_create_json` in a follow-up.

## Amendment (2026-10-13, exclusive create for every append-only record writer)

The follow-up above. `MemoryStorage.create_json()` wraps `safe_create_json`, logs a collision at ERROR naming only the type folder and the timestamp filename, and re-raises. `write_canonical_record()` (and so `write_memory()` and the exchange recorder), `create_session`/`update_session`/`delete_session`, `create_project`/`update_project`/`delete_project`, and lodestone `write`/`update` all publish through it. `MemoryStorage.write_json()` keeps its replace semantics for the callers that still rewrite a file in place.

Each writer picks its own name from a per-process `UniqueTimestampSource`, so a collision needs a second process on the same vault in the same microsecond. Policy is the same as for state and task records: raise, never retry inside the writer. API routes return 409 through the existing handler; the exchange recorder already maps any `OSError` (which `RecordExistsError` is) to `storage_failed`. Callers where a raise would lose more than the one record handle it: `POST /ingest/json` reports the record in `errors` and imports the rest; session reflection and the session compression summary (both can run in background threads) log and drop the record, and compression still injects its summary turn; onboarding logs and asks the same step again instead of returning a 409 to a streaming client; `scripts/journal.py` exits 1 with a message; `scripts/seed_identity_template.py` counts a failure and seeds the rest.

## References

- `src/core/jsonio.py` -- the helpers.
- `docs/KNOWN_ISSUES.md` -- B-IO-001 (deferred unguarded sites).
- `src/memory/storage.py`, `src/memory/read_memory.py`, `src/memory/search_memory.py`, `src/core/preferences.py`, `src/core/config.py`, `src/tasks/task_service.py`, `src/memory/lodestone_service.py`, `src/memory/write_memory.py`, `src/memory/session.py`, `src/memory/project.py`, `src/ingest/importers/chatgpt.py`, `src/ingest/importers/gdrive_sync.py` -- routed call sites.
- CLAUDE.md -- append-only memory, rebuildable derived artifacts, and vault privacy rules.
