import json
import logging
import secrets
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import ollama
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
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

@app.get("/")
def root():
    # Serve Ember UI if available, otherwise return API health check
    if _UI_DIR.is_dir() and (_UI_DIR / "index.html").is_file():
        return FileResponse(_UI_DIR / "index.html")
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
    """Soft-delete a conversation session. Append-only: writes a new record with deleted: true."""
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
    allowed_providers = {"anthropic"}
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
        # Otherwise serve index.html for SPA client-side routing
        return FileResponse(_UI_DIR / "index.html")
