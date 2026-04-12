"""
src/api/main.py

FastAPI application entry point for Ember-2. Registers all API routes,
middleware (auth, rate limiting, CORS, audit logging), and the nightly
tiering scheduler. Serves the built Ember UI from ui/ as a static
fallback after all API routes.

This is the largest file in the backend. Route handlers are defined
inline rather than split into route modules — acceptable for now but
a refactor candidate if the file exceeds ~1500 lines.
"""

import json
import logging
import secrets
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import ollama
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from src.api.chat import router as chat_router
from src.api.limiter import limiter
from src.api.openai_adapter import router as openai_adapter_router, llm_adapter
from src.api.routes.ingest import router as ingest_router
from src.context.service import ContextService
from src.core.config import get_ember_api_key, get_cloud_models
from src.memory.service import MemoryService
from src.memory.session import (
    list_sessions,
    get_session,
    get_turns,
    update_session,
    delete_session,
    list_sessions_by_project,
)
from src.memory.project import (
    list_projects,
    get_project,
    create_project,
    update_project,
    delete_project,
)
from src.reflection.generate_reflection import generate_reflection
from src.retrieval.semantic_search import semantic_search
from src.state.models import VALID_STATE_CATEGORIES
from src.state.state_resolver import StateResolver
from src.state.state_service import StateService
from src.tasks.models import VALID_TASK_STATUSES
from src.tasks.task_service import TaskService

logger = logging.getLogger("ember.auth")

_AUDIT_LOG_DIR = Path(__file__).resolve().parents[2] / "logs" / "audit"
_AUDIT_LOG_DIR.mkdir(parents=True, exist_ok=True)


def _write_audit_log(method: str, path: str, client_ip: str, status: int, ms: int,
                     intent_class: str | None = None) -> None:
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "method": method,
        "path": path,
        "ip": client_ip,
        "status": status,
        "ms": ms,
    }
    if intent_class:
        record["intent"] = intent_class
    entry = json.dumps(record)
    log_file = _AUDIT_LOG_DIR / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.log"
    with log_file.open("a", encoding="utf-8") as f:
        f.write(entry + "\n")


from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)


@app.middleware("http")
async def api_key_auth(request: Request, call_next):
    # Only require auth on API routes — UI static files are public
    path = request.url.path
    API_PREFIXES = ("/v1/", "/model", "/journal", "/write-", "/read-", "/search-",
                    "/semantic-", "/reflect", "/debug-", "/state", "/ingest/")
    if not any(path.startswith(p) for p in API_PREFIXES):
        return await call_next(request)

    # PIN verify and status endpoints bypass API key auth (they are the UI auth)
    PIN_PUBLIC_PATHS = ("/v1/security/pin/verify", "/v1/security/pin/status")
    if path in PIN_PUBLIC_PATHS:
        return await call_next(request)

    expected_key = get_ember_api_key()
    if not expected_key:
        # No key configured — open access (warn once at startup instead)
        return await call_next(request)

    # Accept Authorization: Bearer <key> (Open WebUI / OpenAI clients)
    # or X-API-Key: <key> (direct access)
    provided_key = ""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        provided_key = auth_header[7:]
    if not provided_key:
        provided_key = request.headers.get("X-API-Key", "")

    if not provided_key or not secrets.compare_digest(provided_key, expected_key):
        logger.warning("[AUTH] Rejected request to %s — invalid or missing API key", request.url.path)
        return JSONResponse(status_code=401, content={"detail": "Invalid or missing API key"})

    return await call_next(request)


@app.middleware("http")
async def audit_log(request: Request, call_next):
    if request.url.path == "/":
        return await call_next(request)
    start = time.perf_counter()
    response = await call_next(request)
    ms = int((time.perf_counter() - start) * 1000)
    intent_class = getattr(request.state, "intent_class", None)
    _write_audit_log(
        method=request.method,
        path=request.url.path,
        client_ip=request.client.host,
        status=response.status_code,
        ms=ms,
        intent_class=intent_class,
    )
    return response


app.include_router(chat_router)
app.include_router(openai_adapter_router)
app.include_router(ingest_router)
memory_service = MemoryService()
context_service = ContextService()
state_service = StateService()
state_resolver = StateResolver(service=state_service)
task_service = TaskService()


class MemoryRequest(BaseModel):
    text: str
    memory_type: str = "journal"


class JournalRequest(BaseModel):
    text: str
    tags: list[str] = []
    mood: str | None = None
    date_override: str | None = None


class StateRequest(BaseModel):
    type: str
    text: str
    source: str = "api"
    tags: list[str] = []
    metadata: dict = {}


class ModelRequest(BaseModel):
    model: str


class ConversationUpdateRequest(BaseModel):
    title: str | None = None
    project_id: str | None = None


class ProjectCreateRequest(BaseModel):
    name: str
    color: str = "#ff8c00"


class ProjectUpdateRequest(BaseModel):
    name: str | None = None
    color: str | None = None


def clean_context_packet(packet_dict: dict) -> dict:
    for section in ["memory_items", "reflection_items", "state_items"]:
        for item in packet_dict.get(section, []):
            metadata = item.get("metadata", {})

            if "embedding" in metadata:
                del metadata["embedding"]

            if "file_path" in metadata:
                del metadata["file_path"]

    return packet_dict


_UI_DIR = Path(__file__).resolve().parents[2] / "ui"

# Cache the index.html content with injected API key. Invalidated
# automatically when ui/index.html is modified (mtime check).
_cached_index_html: str | None = None
_cached_index_mtime: float = 0.0


def _get_index_html() -> str:
    """Read index.html and inject the API key for the UI.

    Caches the result and invalidates when the file's mtime changes,
    so UI rebuilds take effect without an API restart.
    """
    global _cached_index_html, _cached_index_mtime

    index_path = _UI_DIR / "index.html"
    current_mtime = index_path.stat().st_mtime

    if _cached_index_html is not None and current_mtime == _cached_index_mtime:
        return _cached_index_html

    html = index_path.read_text(encoding="utf-8")
    api_key = get_ember_api_key()
    if api_key:
        inject = f'<script>window.__EMBER_API_KEY__="{api_key}";</script>\n  '
        html = html.replace("</head>", inject + "</head>")

    _cached_index_html = html
    _cached_index_mtime = current_mtime
    return _cached_index_html


@app.get("/")
def root():
    # Serve Ember UI if available, otherwise return API health check
    if _UI_DIR.is_dir() and (_UI_DIR / "index.html").is_file():
        return HTMLResponse(_get_index_html())
    return {
        "message": "Ember-2 API is running",
        "model": llm_adapter.model,
    }

@app.get("/api/health")
def health_check():
    """API health check — always returns JSON, even when UI is served at /"""
    import json as _json
    version = "unknown"
    try:
        vf = Path(__file__).resolve().parents[2] / "version.json"
        version = _json.loads(vf.read_text(encoding="utf-8")).get("version", "unknown")
    except Exception:
        pass
    return {
        "message": "Ember-2 API is running",
        "model": llm_adapter.model,
        "version": f"v{version}" if not version.startswith("v") else version,
    }


# ── Conversation session endpoints ─────────────────────────────────────


@app.get("/v1/conversations")
def list_conversations_endpoint(limit: int = 50):
    """List all conversation sessions, newest first."""
    sessions = list_sessions(limit=limit)
    return {"conversations": sessions}


@app.get("/v1/conversations/{session_id}")
def get_conversation_endpoint(session_id: str, limit: int = 200):
    """Get all turns for a conversation session."""
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
    turns = get_turns(session_id, limit=limit)
    return {
        "session": {
            "id": session_id,
            "title": session.get("text", "Untitled"),
            "created_at": session.get("metadata", {}).get("created_at", ""),
        },
        "turns": [
            {
                "id": t.get("id"),
                "role": t.get("metadata", {}).get("role", "unknown"),
                "content": t.get("text", ""),
                "timestamp": t.get("timestamp", ""),
            }
            for t in turns
        ],
    }


@app.patch("/v1/conversations/{session_id}")
def update_conversation_endpoint(session_id: str, body: ConversationUpdateRequest):
    """Update a conversation's title and/or project assignment. Append-only."""
    if body.title is None and body.project_id is None:
        raise HTTPException(status_code=400, detail="Provide at least one of: title, project_id")
    result = update_session(session_id, title=body.title, project_id=body.project_id if body.project_id is not None else "__unset__")
    if result is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
    return {"status": "updated", "session_id": session_id, "title": body.title, "project_id": body.project_id}


@app.delete("/v1/conversations/{session_id}")
def delete_conversation_endpoint(session_id: str):
    """Soft-delete a conversation session. Append-only: writes a new record with deleted: true.
    Triggers session reflection if buffer has 3+ turns (ADR-009)."""
    # Auto-trigger session reflection before delete (non-fatal)
    try:
        buffer = llm_adapter.prompt_builder.conversation_buffer.get_recent()
        if buffer and len(buffer) >= 3:
            import threading
            from src.reflection.session_reflection import generate_session_reflection
            threading.Thread(
                target=generate_session_reflection,
                args=(buffer, session_id),
                daemon=True,
            ).start()
    except Exception as exc:
        logger.warning("[SESSION_REFLECT] Auto-trigger on delete failed (non-fatal): %s", exc)

    result = delete_session(session_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
    return {"status": "deleted", "session_id": session_id}


# ── Project endpoints ──────────────────────────────────────────────────


@app.get("/v1/projects")
def list_projects_endpoint():
    """List all projects with conversation counts."""
    projects = list_projects()
    # Add conversation_count per project
    for proj in projects:
        convos = list_sessions_by_project(proj["id"], limit=9999)
        proj["conversation_count"] = len(convos)
    return {"projects": projects}


@app.post("/v1/projects")
def create_project_endpoint(body: ProjectCreateRequest):
    """Create a new project."""
    result = create_project(body.name, body.color)
    return {"status": "created", **result}


@app.patch("/v1/projects/{project_id}")
def update_project_endpoint(project_id: str, body: ProjectUpdateRequest):
    """Rename or recolor a project. Append-only."""
    if body.name is None and body.color is None:
        raise HTTPException(status_code=400, detail="Provide at least one of: name, color")
    result = update_project(project_id, name=body.name, color=body.color)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    return {"status": "updated", "project_id": project_id, "name": body.name, "color": body.color}


@app.delete("/v1/projects/{project_id}")
def delete_project_endpoint(project_id: str):
    """Soft-delete a project. Append-only."""
    result = delete_project(project_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    return {"status": "deleted", "project_id": project_id}


@app.get("/v1/projects/{project_id}/conversations")
def list_project_conversations_endpoint(project_id: str, limit: int = 50):
    """List conversations belonging to a project."""
    proj = get_project(project_id)
    if proj is None:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    sessions = list_sessions_by_project(project_id, limit=limit)
    return {"project_id": project_id, "conversations": sessions}


# ── Task endpoints ────────────────────────────────────────────────────


class TaskCreateRequest(BaseModel):
    title: str
    status: str = "active"
    project_id: str | None = None
    text: str | None = None
    tags: list[str] = []


class TaskUpdateRequest(BaseModel):
    status: str


@app.post("/v1/tasks")
@limiter.limit("30/minute")
def create_task_endpoint(request: Request, body: TaskCreateRequest):
    """Create a new task. Defaults to active status."""
    if body.status not in VALID_TASK_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{body.status}'. Valid: {sorted(VALID_TASK_STATUSES)}",
        )
    record = TaskService.make_record(
        title=body.title,
        status=body.status,
        source="user_input",
        project_id=body.project_id,
        text=body.text,
        tags=body.tags,
    )
    path = task_service.write(record)
    return {
        "status": "created",
        "id": record.id,
        "title": record.title,
        "task_status": record.status,
        "path": str(path),
    }


@app.get("/v1/tasks")
def list_tasks_endpoint(status: str | None = None, project_id: str | None = None):
    """List tasks with optional status and project filters."""
    if status and status not in VALID_TASK_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{status}'. Valid: {sorted(VALID_TASK_STATUSES)}",
        )

    if status and project_id:
        records = [r for r in task_service.read_by_project(project_id) if r.status == status]
    elif status:
        records = task_service.read_by_status(status)
    elif project_id:
        records = task_service.read_by_project(project_id)
    else:
        records = task_service.read_all()

    return {
        "tasks": [
            {
                "id": r.id,
                "title": r.title,
                "status": r.status,
                "text": r.text,
                "source": r.source,
                "project_id": r.project_id,
                "tags": r.tags,
                "timestamp": r.timestamp,
                "metadata": r.metadata,
            }
            for r in records
        ]
    }


@app.get("/v1/tasks/{task_id}")
def get_task_endpoint(task_id: str):
    """Get a single task by ID (returns the most recent record for that ID)."""
    record = task_service.read_by_id(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
    return {
        "id": record.id,
        "title": record.title,
        "status": record.status,
        "text": record.text,
        "source": record.source,
        "project_id": record.project_id,
        "tags": record.tags,
        "timestamp": record.timestamp,
        "metadata": record.metadata,
    }


@app.patch("/v1/tasks/{task_id}")
def update_task_status_endpoint(task_id: str, body: TaskUpdateRequest):
    """
    Update a task's status. Append-only: writes a new record with the
    updated status and a new timestamp, preserving the original task ID.
    """
    if body.status not in VALID_TASK_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{body.status}'. Valid: {sorted(VALID_TASK_STATUSES)}",
        )

    existing = task_service.read_by_id(task_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")

    # Write a new record with the same task ID but new timestamp and updated status.
    # next_timestamp() spins on same-microsecond collisions so the new record
    # cannot share a filename with the original (which would silently drop the
    # update — the root cause of the test_update_status flake).
    from src.tasks.task_service import next_timestamp
    new_timestamp = next_timestamp()

    from src.tasks.models import TaskRecord
    updated = TaskRecord(
        id=task_id,
        timestamp=new_timestamp,
        type="task",
        title=existing.title,
        status=body.status,
        text=existing.text,
        source=existing.source,
        project_id=existing.project_id,
        tags=existing.tags,
        metadata={**existing.metadata, "previous_status": existing.status},
    )
    path = task_service.write(updated)
    return {
        "status": "updated",
        "id": task_id,
        "task_status": body.status,
        "previous_status": existing.status,
        "path": str(path),
    }


@app.delete("/v1/tasks/{task_id}")
def delete_task_endpoint(task_id: str):
    """
    Soft-delete a task by setting status to 'cancelled'.
    Append-only: writes a new record, does not remove the original.
    """
    existing = task_service.read_by_id(task_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")

    if existing.status == "cancelled":
        return {"status": "already_cancelled", "id": task_id}

    from datetime import datetime
    new_timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S-%f")

    from src.tasks.models import TaskRecord
    cancelled = TaskRecord(
        id=task_id,
        timestamp=new_timestamp,
        type="task",
        title=existing.title,
        status="cancelled",
        text=existing.text,
        source=existing.source,
        project_id=existing.project_id,
        tags=existing.tags,
        metadata={**existing.metadata, "previous_status": existing.status},
    )
    task_service.write(cancelled)
    return {"status": "cancelled", "id": task_id}


# ── Deviation endpoints ────────────────────────────────────────────────


class DeviationUpdateRequest(BaseModel):
    confirmed: bool | None = None
    reason: str | None = None
    value_aligned: bool | None = None
    user_note: str | None = None
    flagged_as_noise: bool | None = None


def _read_deviation_records(
    confirmed: bool | None = None,
    pattern_class: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Read deviation records from vault with optional filters."""
    from src.core.config import get_private_vault_path
    from src.memory.storage import MemoryStorage

    vault = get_private_vault_path()
    storage = MemoryStorage()
    dev_dir = storage.get_memory_dir(vault, "deviation")

    records = []
    for f in sorted(dev_dir.glob("*.json"), reverse=True):
        try:
            data = storage.read_json(f)
            if data.get("type") != "deviation":
                continue

            meta = data.get("metadata", {})
            if confirmed is not None:
                if meta.get("confirmed") != confirmed:
                    continue
            if pattern_class is not None:
                if meta.get("pattern_class") != pattern_class:
                    continue

            records.append(data)
            if len(records) >= limit:
                break
        except Exception:
            continue

    return records


def _update_deviation_record(record_id: str, updates: dict) -> dict | None:
    """Update a deviation record in-place."""
    from src.core.config import get_private_vault_path
    from src.memory.storage import MemoryStorage

    vault = get_private_vault_path()
    storage = MemoryStorage()
    dev_dir = storage.get_memory_dir(vault, "deviation")

    for f in dev_dir.glob("*.json"):
        try:
            data = storage.read_json(f)
            if data.get("id") != record_id:
                continue

            meta = data.get("metadata", {})
            if "confirmed" in updates:
                meta["confirmed"] = bool(updates["confirmed"])
            if "reason" in updates:
                meta["reason"] = updates["reason"]
            if "value_aligned" in updates:
                meta["value_aligned"] = bool(updates["value_aligned"])
            if "user_note" in updates:
                meta["user_note"] = updates["user_note"]
            if "flagged_as_noise" in updates:
                meta["flagged_as_noise"] = bool(updates["flagged_as_noise"])
            if "user_edited" not in meta:
                meta["user_edited"] = False
            if any(k in updates for k in ("reason", "user_note", "confirmed", "value_aligned")):
                meta["user_edited"] = True

            data["metadata"] = meta
            storage.write_json(f, data)
            return data
        except Exception:
            continue

    return None


@app.get("/v1/deviations")
def get_deviations(
    confirmed: bool | None = None,
    pattern_class: str | None = None,
    limit: int = 20,
):
    """Return deviation records with optional filters."""
    records = _read_deviation_records(
        confirmed=confirmed,
        pattern_class=pattern_class,
        limit=min(limit, 100),
    )
    return {"records": records, "count": len(records)}


@app.patch("/v1/deviations/{record_id}")
def update_deviation(record_id: str, body: DeviationUpdateRequest):
    """Update a deviation record (confirm, add reason, flag as noise)."""
    updates = {}
    if body.confirmed is not None:
        updates["confirmed"] = body.confirmed
    if body.reason is not None:
        updates["reason"] = body.reason
    if body.value_aligned is not None:
        updates["value_aligned"] = body.value_aligned
    if body.user_note is not None:
        updates["user_note"] = body.user_note
    if body.flagged_as_noise is not None:
        updates["flagged_as_noise"] = body.flagged_as_noise

    if not updates:
        raise HTTPException(status_code=400, detail="No updates provided")

    result = _update_deviation_record(record_id, updates)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Deviation record '{record_id}' not found")

    return {"status": "updated", "record": result}


# ── Lodestone endpoints ────────────────────────────────────────────────

from src.memory import lodestone_service


class LodestoneCreateRequest(BaseModel):
    value: str
    taxonomy_category: str
    source: str = "conversation"
    question_context: str | None = None


class LodestoneUpdateRequest(BaseModel):
    confirmed: bool | None = None
    user_note: str | None = None
    flagged_as_noise: bool | None = None


@app.get("/v1/lodestone")
def get_lodestone():
    """Return all lodestone records (confirmed + proposed)."""
    records = lodestone_service.read_all()
    return {"records": records, "count": len(records)}


def _extract_lodestone_value(raw_answer: str, question_context: str | None = None) -> str | None:
    """
    Use LLM to extract a value statement from a raw answer.

    Returns the inferred value, or None on failure.
    Uses system/user message split — single-message prompts cause qwen3:8b
    to consume all tokens in thinking mode and return empty.
    """
    try:
        from src.core.config import get_ember_model

        user_msg = raw_answer
        if question_context:
            user_msg = f"Question: {question_context}\nAnswer: {raw_answer}"

        result = ollama.chat(
            model=get_ember_model(),
            messages=[
                {"role": "system", "content": (
                    "You extract values from answers. "
                    "Write exactly one sentence: what does this person care about? "
                    "Do not summarize — identify the underlying value. "
                    "No preamble, just the value statement."
                )},
                {"role": "user", "content": user_msg},
            ],
            options={"temperature": 0, "num_predict": 100},
            think=False,
        )
        inferred = result["message"]["content"].strip()
        if inferred:
            logger.info("[LODESTONE] Inferred value: %s", inferred[:80])
            return inferred
        logger.warning("[LODESTONE] Inference returned empty")
        return None
    except Exception as exc:
        logger.warning("[LODESTONE] Value inference failed: %s", exc)
        return None


@app.post("/v1/lodestone")
def create_lodestone(body: LodestoneCreateRequest):
    """
    Create an explicit lodestone record (Path 1 acquisition).
    Infers a value statement from the raw answer via LLM.
    Starts as confirmed: true.
    """
    valid_categories = {"character", "relational", "directional", "ground", "beyond"}
    if body.taxonomy_category not in valid_categories:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid taxonomy_category. Must be one of: {sorted(valid_categories)}",
        )

    inferred_value = _extract_lodestone_value(body.value, body.question_context)

    if inferred_value is None:
        raise HTTPException(
            status_code=503,
            detail="Inference unavailable, try again",
        )

    try:
        record = lodestone_service.write(
            value=inferred_value,
            taxonomy_category=body.taxonomy_category,
            acquisition_path="explicit",
            source=body.source,
            supporting_evidence=body.value,
            confirmed=True,
        )
        return {"status": "created", "record": record}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.patch("/v1/lodestone/{record_id}")
def update_lodestone(record_id: str, body: LodestoneUpdateRequest):
    """Confirm, dismiss, or annotate a lodestone record."""
    updates = {}
    if body.confirmed is not None:
        updates["confirmed"] = body.confirmed
    if body.user_note is not None:
        updates["user_note"] = body.user_note
    if body.flagged_as_noise is not None:
        updates["flagged_as_noise"] = body.flagged_as_noise

    if not updates:
        raise HTTPException(status_code=400, detail="No updates provided")

    try:
        result = lodestone_service.update(record_id, updates)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if result is None:
        raise HTTPException(status_code=404, detail=f"Lodestone record '{record_id}' not found")

    return {"status": "updated", "record": result}


# ── Security / PIN endpoints ───────────────────────────────────────────


class PinSetRequest(BaseModel):
    pin: str
    recovery_passphrase: str


class PinVerifyRequest(BaseModel):
    pin: str


class PinRecoverRequest(BaseModel):
    recovery_passphrase: str
    new_pin: str


class PinChangeRequest(BaseModel):
    current_pin: str
    new_pin: str


@app.get("/v1/security/pin/status")
def pin_status_endpoint():
    """Check if a PIN has been configured. No auth required.
    Defensively wrapped — this is called on every page load and must never 500."""
    try:
        from src.security.pin_service import pin_is_set
        return {"pin_set": pin_is_set()}
    except Exception:
        return {"pin_set": False}


@app.post("/v1/security/pin/set")
def pin_set_endpoint(body: PinSetRequest):
    """Set PIN and recovery passphrase. Requires API key auth."""
    try:
        from src.security.pin_service import set_pin, set_recovery_passphrase
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"PIN service unavailable: {exc}")
    if len(body.pin) < 4:
        raise HTTPException(status_code=400, detail="PIN must be at least 4 characters")
    if len(body.recovery_passphrase) < 20:
        raise HTTPException(status_code=400, detail="Recovery passphrase must be at least 20 characters")
    set_pin(body.pin)
    set_recovery_passphrase(body.recovery_passphrase)
    return {"status": "set"}


@app.post("/v1/security/pin/verify")
@limiter.limit("5/minute")
def pin_verify_endpoint(request: Request, body: PinVerifyRequest):
    """Verify a PIN. No API key auth — this IS the UI auth. Rate-limited."""
    try:
        from src.security.pin_service import verify_pin, check_rate_limit, record_failed_attempt, get_remaining_attempts
    except Exception:
        return {"valid": False, "error": "PIN service unavailable"}
    client_ip = request.client.host
    if not check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Too many attempts. Try again in 5 minutes.")
    if verify_pin(body.pin):
        return {"valid": True}
    remaining = record_failed_attempt(client_ip)
    return {"valid": False, "remaining_attempts": remaining}


@app.post("/v1/security/pin/recover")
@limiter.limit("5/minute")
def pin_recover_endpoint(request: Request, body: PinRecoverRequest):
    """Recover access with passphrase and set new PIN. Rate-limited."""
    try:
        from src.security.pin_service import verify_recovery_passphrase, set_pin, check_rate_limit, record_failed_attempt
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"PIN service unavailable: {exc}")
    client_ip = request.client.host
    if not check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Too many attempts. Try again in 5 minutes.")
    if not verify_recovery_passphrase(body.recovery_passphrase):
        record_failed_attempt(client_ip)
        raise HTTPException(status_code=403, detail="Invalid recovery passphrase")
    if len(body.new_pin) < 4:
        raise HTTPException(status_code=400, detail="PIN must be at least 4 characters")
    set_pin(body.new_pin)
    return {"status": "recovered"}


@app.post("/v1/security/pin/change")
@limiter.limit("5/minute")
def pin_change_endpoint(request: Request, body: PinChangeRequest):
    """Change the PIN. Requires the current PIN for verification.

    Rate-limited and API-key authenticated (routine rotation by a user
    who is already signed in). Intentionally decoupled from the recovery
    passphrase — this endpoint is for users who know their current PIN.
    Users who have forgotten their PIN must use /v1/security/pin/recover
    instead.
    """
    try:
        from src.security.pin_service import (
            change_pin,
            check_rate_limit,
            record_failed_attempt,
            get_remaining_attempts,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"PIN service unavailable: {exc}")

    client_ip = request.client.host
    if not check_rate_limit(client_ip):
        raise HTTPException(
            status_code=429,
            detail="Too many attempts. Try again in 5 minutes.",
        )

    if len(body.new_pin) < 4:
        raise HTTPException(
            status_code=400,
            detail="PIN must be at least 4 characters",
        )

    if not change_pin(body.current_pin, body.new_pin):
        remaining = record_failed_attempt(client_ip)
        raise HTTPException(
            status_code=403,
            detail={
                "error": "Invalid current PIN",
                "remaining_attempts": remaining,
            },
        )

    return {"status": "changed"}


# ── System endpoints ──────────────────────────────────────────────────


@app.get("/v1/system/disk-encryption")
def disk_encryption_status_endpoint():
    """Check whether full-disk encryption is enabled on the host OS.

    Detects BitLocker (Windows), FileVault (macOS), or LUKS (Linux).
    Returns the status, platform name, and a human-readable
    recommendation. Non-destructive read-only check — safe to poll.
    """
    from src.security.disk_encryption import detect
    return detect()


# ── Developer endpoints ───────────────────────────────────────────────


class VaultSwapRequest(BaseModel):
    vault_label: str


@app.post("/v1/developer/vault/swap")
def vault_swap_endpoint(body: VaultSwapRequest):
    """Swap the active vault at runtime (developer mode only).

    Runtime-only — updates an in-memory override, never touches .env.
    Reverts to the .env vault path on API restart. Clears all in-memory
    vector indexes so they lazy-load from the new vault on next query.

    Requires EMBER_DEV_MODE=true in the environment.
    Known vault labels are read from .env at startup:
      VAULT_PATH_LIVE, VAULT_PATH_DEMO, VAULT_PATH_TEST
    """
    from src.core.config import (
        is_dev_mode,
        get_known_vault_paths,
        set_vault_path_override,
        get_vault_label,
    )

    if not is_dev_mode():
        raise HTTPException(
            status_code=403,
            detail="Vault swap requires EMBER_DEV_MODE=true in environment.",
        )

    known = get_known_vault_paths()
    label = body.vault_label.lower()

    if label not in known:
        available = ", ".join(sorted(known.keys())) if known else "none configured"
        raise HTTPException(
            status_code=400,
            detail=f"Unknown vault label '{label}'. Available: {available}.",
        )

    vault_path = known[label]
    resolved = Path(vault_path).resolve()
    if not resolved.is_dir():
        raise HTTPException(
            status_code=400,
            detail=f"Vault path does not exist or is not a directory: {resolved}",
        )

    set_vault_path_override(str(resolved), label)

    # Clear all in-memory vector indexes — they belong to the previous vault.
    from src.retrieval.vector_index import clear_index_cache
    clear_index_cache()

    return {
        "active_vault": str(resolved),
        "label": label,
        "note": "indexes cleared, will rebuild on first query",
    }


@app.get("/v1/developer/vault/status")
def vault_status_endpoint():
    """Return the currently active vault path and label."""
    from src.core.config import get_private_vault_path, get_vault_label
    return {
        "active_vault": str(get_private_vault_path()),
        "label": get_vault_label(),
    }


# ── Preferences endpoints ──────────────────────────────────────────────


@app.get("/v1/preferences")
def get_preferences_endpoint():
    """Return current user preferences."""
    from src.core.preferences import read as read_prefs
    return read_prefs()


@app.patch("/v1/preferences")
def update_preferences_endpoint(request: Request, body: dict):
    """Update user preferences. Accepts {key: value} pairs."""
    from src.core.preferences import update as update_prefs
    update_prefs(body)
    from src.core.preferences import read as read_prefs
    return read_prefs()


# ── Memory endpoints ───────────────────────────────────────────────────


@app.post("/journal")
def write_journal_endpoint(request: JournalRequest):
    from src.memory.write_memory import write_memory

    metadata: dict = {}
    if request.mood:
        metadata["mood"] = request.mood
    if request.date_override:
        metadata["date_override"] = request.date_override

    tags = list(request.tags)
    if request.mood and request.mood not in tags:
        tags = [request.mood] + tags

    path = write_memory(
        text=request.text,
        memory_type="journal",
        source="api",
        tags=tags,
        metadata=metadata,
    )

    if path is None:
        return {"status": "skipped", "reason": "content filtered"}

    return {"status": "written", "path": str(path)}


@app.post("/write-memory")
def write_memory_endpoint(request: MemoryRequest):
    memory_service.write(request.text, request.memory_type, metadata={})
    return {"status": "memory written"}


@app.get("/read-memories")
def read_memories_endpoint(memory_type: str = "journal", limit: int = 5):
    return {"memories": memory_service.read(memory_type, limit)}


@app.get("/search-memories")
def search_memories_endpoint(query: str, memory_type: str = "journal", limit: int = 5):
    return {"results": memory_service.search(query, memory_type, limit)}


@app.get("/semantic-search")
def semantic_search_endpoint(
    query: str,
    limit: int = 5,
    memory_type: str | None = None,
    min_score: float | None = None,
):
    return {"results": semantic_search(query, limit, memory_type, min_score)}

@app.post("/reflect")
@limiter.limit("10/minute")
def reflect_endpoint(request: Request, memory_type: str = "journal", limit: int = 5):
    return generate_reflection(memory_types=[memory_type], limit=limit)


class SessionReflectRequest(BaseModel):
    session_id: str | None = None


@app.post("/reflect/session")
@limiter.limit("10/minute")
def reflect_session_endpoint(request: Request, body: SessionReflectRequest = SessionReflectRequest()):
    """Generate a narrative session reflection from the current conversation buffer."""
    from src.reflection.session_reflection import generate_session_reflection

    buffer = llm_adapter.prompt_builder.conversation_buffer.get_recent()
    if not buffer or len(buffer) < 3:
        return {"status": "skipped", "reason": f"Not enough turns ({len(buffer)}). Minimum 3."}

    reflection = generate_session_reflection(buffer, session_id=body.session_id)
    if reflection:
        return {"status": "ok", "reflection": reflection[:200]}
    return {"status": "error", "reason": "Reflection generation failed."}


@app.post("/reflect/monthly")
@limiter.limit("5/minute")
def reflect_monthly_endpoint(request: Request):
    """Generate a monthly synthesis reflection using LLM-driven analysis."""
    from src.reflection.run_monthly_reflection import run_monthly_reflection
    result = run_monthly_reflection()
    return result


@app.get("/debug-context")
def debug_context_endpoint(message: str):
    context_packet = context_service.build_context(message)
    return clean_context_packet(asdict(context_packet))


# ── State endpoints ────────────────────────────────────────────────────


@app.get("/state")
def get_state_endpoint():
    items = state_resolver.get_current_state()
    return {"state": [{"category": i.category, "text": i.text, "timestamp": i.timestamp, "priority": i.priority} for i in items]}


@app.get("/state/{category}")
def get_state_by_category_endpoint(category: str):
    if category not in VALID_STATE_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"Invalid category '{category}'. Valid: {sorted(VALID_STATE_CATEGORIES)}")
    item = state_resolver.get_current_by_category(category)
    if not item:
        return {"state": None}
    return {"state": {"category": item.category, "text": item.text, "timestamp": item.timestamp, "priority": item.priority}}


@app.post("/write-state")
def write_state_endpoint(request: StateRequest):
    if request.type not in VALID_STATE_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"Invalid type '{request.type}'. Valid: {sorted(VALID_STATE_CATEGORIES)}")
    record = StateService.make_record(
        state_type=request.type,
        text=request.text,
        source=request.source,
        tags=request.tags,
        metadata=request.metadata,
    )
    path = state_service.write(record)
    return {"status": "state written", "type": record.type, "text": record.text, "path": str(path)}


# ── Model endpoints ────────────────────────────────────────────────────


@app.get("/model")
def get_model_endpoint():
    try:
        all_models = [m["model"] for m in ollama.list()["models"]]
        # Filter out embedding models — not chat models, should not appear in selector
        available = [m for m in all_models if not any(p in m.lower() for p in ("embed", "embedding"))]
    except Exception:
        available = []
    cloud = get_cloud_models()
    return {"model": llm_adapter.model, "available": available, "cloud": cloud}


@app.post("/model")
def set_model_endpoint(request: ModelRequest):
    from src.core.config import set_ember_model_override
    llm_adapter.set_model(request.model)
    llm_adapter.prompt_builder.conversation_buffer.set_context_window(request.model)
    set_ember_model_override(request.model)
    return {"model": llm_adapter.model}


class ProviderKeyRequest(BaseModel):
    provider: str
    api_key: str


@app.post("/provider-key")
def store_provider_key(body: ProviderKeyRequest):
    """Store a cloud provider API key in the credential manager."""
    allowed_providers = {"anthropic", "openai"}
    if body.provider not in allowed_providers:
        raise HTTPException(status_code=400, detail=f"Unknown provider '{body.provider}'. Allowed: {sorted(allowed_providers)}")
    try:
        import keyring
        keyring.set_password(f"ember-2-{body.provider}", "api_key", body.api_key)
        return {"status": "stored", "provider": body.provider}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to store key: {exc}")


@app.get("/provider-key/{provider}")
def check_provider_key(provider: str):
    """Check if a cloud provider API key is configured. Never returns the actual key."""
    try:
        import keyring
        key = keyring.get_password(f"ember-2-{provider}", "api_key")
        return {"provider": provider, "configured": bool(key)}
    except Exception:
        return {"provider": provider, "configured": False}


@app.delete("/provider-key/{provider}")
def remove_provider_key(provider: str):
    """Remove a cloud provider API key from the credential store."""
    try:
        import keyring
        keyring.delete_password(f"ember-2-{provider}", "api_key")
        return {"status": "removed", "provider": provider}
    except Exception:
        return {"status": "not_found", "provider": provider}


# ── Tiering ───────────────────────────────────────────────────────────


@app.post("/tiering/run")
@limiter.limit("10/minute")
def run_tiering(request: Request):
    """Manual trigger for memory tiering (ADR-015). Returns transition counts."""
    from src.tiering.tiering_service import TieringService
    transitions = TieringService().run()
    return {"status": "complete", "transitions": transitions}


# ── Nightly tiering scheduler ─────────────────────────────────────────
# Daemon thread fires TieringService.run() once at 00:05 daily.
# Simple sleep loop — no new dependencies.

import threading
import time as _time


def _nightly_tiering_loop():
    """Sleep until 00:05, run tiering (and monthly reflection on day 1), repeat."""
    while True:
        now = datetime.now()
        # Next 00:05
        tomorrow = now.replace(hour=0, minute=5, second=0, microsecond=0)
        if tomorrow <= now:
            tomorrow = tomorrow.replace(day=tomorrow.day + 1)
        sleep_seconds = (tomorrow - now).total_seconds()
        _time.sleep(sleep_seconds)

        try:
            from src.tiering.tiering_service import TieringService
            TieringService().run()
        except Exception as exc:
            logging.getLogger("ember.tiering").warning(
                "[TIERING] Nightly run failed: %s", exc
            )

        # Monthly reflection fires on the 1st of each month
        if datetime.now().day == 1:
            try:
                from src.reflection.run_monthly_reflection import run_monthly_reflection
                run_monthly_reflection()
                logging.getLogger("ember.reflection").info(
                    "[REFLECTION] Monthly reflection generated"
                )
            except Exception as exc:
                logging.getLogger("ember.reflection").warning(
                    "[REFLECTION] Monthly reflection failed: %s", exc
                )


_tiering_thread = threading.Thread(target=_nightly_tiering_loop, daemon=True)
_tiering_thread.start()


# ── UI static file serving ─────────────────────────────────────────────
# Serves the built Ember UI from ui/ if it exists.
# Must be registered AFTER all API routes — acts as a fallback.
# If ui/ doesn't exist, the API runs in headless mode (API only).

if _UI_DIR.is_dir():
    # Serve static assets (js, css, images)
    app.mount("/assets", StaticFiles(directory=_UI_DIR / "assets"), name="ui-assets")

    # SPA catch-all: any non-API route returns index.html
    @app.get("/{path:path}")
    def serve_ui(path: str):
        # If the file exists in ui/, serve it directly (favicon, etc.)
        file_path = _UI_DIR / path
        if file_path.is_file():
            return FileResponse(file_path)
        # Otherwise serve index.html (with injected API key) for SPA routing
        return HTMLResponse(_get_index_html())
