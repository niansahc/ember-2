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


def _write_audit_log(method: str, path: str, client_ip: str, status: int, ms: int) -> None:
    entry = json.dumps({
        "ts": datetime.now(timezone.utc).isoformat(),
        "method": method,
        "path": path,
        "ip": client_ip,
        "status": status,
        "ms": ms,
    })
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
    _write_audit_log(
        method=request.method,
        path=request.url.path,
        client_ip=request.client.host,
        status=response.status_code,
        ms=ms,
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

# Cache the index.html content with injected API key so we don't
# read the file and inject on every request.
_cached_index_html: str | None = None


def _get_index_html() -> str:
    """Read index.html and inject the API key for the UI."""
    global _cached_index_html
    if _cached_index_html is not None:
        return _cached_index_html

    html = (_UI_DIR / "index.html").read_text(encoding="utf-8")
    api_key = get_ember_api_key()
    if api_key:
        # Inject before </head> so the UI can read window.__EMBER_API_KEY__
        # without needing the key baked in at Vite build time.
        inject = f'<script>window.__EMBER_API_KEY__="{api_key}";</script>\n  '
        html = html.replace("</head>", inject + "</head>")

    _cached_index_html = html
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

    # Write a new record with the same task ID but new timestamp and updated status
    from datetime import datetime
    new_timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S-%f")

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


# ── Security / PIN endpoints ───────────────────────────────────────────


class PinSetRequest(BaseModel):
    pin: str
    recovery_passphrase: str


class PinVerifyRequest(BaseModel):
    pin: str


class PinRecoverRequest(BaseModel):
    recovery_passphrase: str
    new_pin: str


@app.get("/v1/security/pin/status")
def pin_status_endpoint():
    """Check if a PIN has been configured. No auth required."""
    from src.security.pin_service import pin_is_set
    return {"pin_set": pin_is_set()}


@app.post("/v1/security/pin/set")
def pin_set_endpoint(body: PinSetRequest):
    """Set PIN and recovery passphrase. Requires API key auth."""
    from src.security.pin_service import set_pin, set_recovery_passphrase
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
    from src.security.pin_service import verify_pin, check_rate_limit, record_failed_attempt, get_remaining_attempts
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
    from src.security.pin_service import verify_recovery_passphrase, set_pin, check_rate_limit, record_failed_attempt
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
        available = [m["model"] for m in ollama.list()["models"]]
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
