from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
import logging
import os
import threading

from dotenv import load_dotenv


load_dotenv()

logger = logging.getLogger("ember.config")


# Runtime vault path override. Set by the developer vault-swap endpoint
# (POST /v1/developer/vault/swap). Takes precedence over PRIVATE_VAULT_PATH
# from .env. Reverts on API restart — never persisted to disk.
_vault_path_override: str | None = None
_vault_label: str | None = None


# Deferred-work vault binding (issue #144). The override above is process
# global and get_private_vault_path() re-resolves on every call, so work that
# is queued under one vault but runs later -- a daemon thread, a streaming
# response's post-stream cleanup -- would otherwise resolve whichever vault is
# active when it finally executes. A swap landing mid-turn could therefore
# route a live-vault turn's write into the test vault, or the reverse.
#
# A binding pins the vault for the duration of a block or a spawned thread and
# is consulted BEFORE the override, so bound work cannot observe a later swap.
#
# ContextVar rather than threading.local(): isolated per thread and per async
# task, and reset(token) restores the prior value exactly, which matters on
# pooled threads that outlive one unit of work.
_vault_binding: ContextVar[str | None] = ContextVar(
    "ember_vault_binding", default=None
)


def get_private_vault_path():
    """
    Reads the PRIVATE_VAULT_PATH from the .env file
    and returns the absolute path to the vault.

    Resolution order:
      1. Deferred-work binding, set by vault_binding() or
         spawn_vault_bound_thread(). Work queued under one vault keeps that
         vault even if a swap lands while it is in flight (issue #144).
      2. Runtime override set via set_vault_path_override() by the vault-swap
         endpoint. Memory-only, reverts on API restart.
      3. PRIVATE_VAULT_PATH from the environment.

    This is the only vault resolution point in src/ -- nothing reads the
    override global or the environment variable directly -- so the binding
    check here covers every caller, including ones not yet written.
    """
    bound = _vault_binding.get()
    if bound is not None:
        return Path(bound).resolve()

    if _vault_path_override is not None:
        return Path(_vault_path_override).resolve()

    vault_path = os.getenv("PRIVATE_VAULT_PATH")

    if not vault_path:
        raise ValueError("PRIVATE_VAULT_PATH not set in environment")

    return Path(vault_path).resolve()


def get_bound_vault() -> str | None:
    """Return the vault bound to the current thread/task, or None when unbound."""
    return _vault_binding.get()


@contextmanager
def vault_binding(path):
    """Pin the vault path for the duration of this block.

    Takes precedence over the swap override, so a swap landing mid-block does
    not move the work that the block performs.

    A None path is a deliberate no-op: call sites that could not resolve a
    vault (PRIVATE_VAULT_PATH unset) then behave exactly as they did before
    the binding existed, rather than failing in a new place.
    """
    if path is None:
        yield
        return

    # Capture the ContextVar object itself rather than resolving the module
    # global again at reset time. Test fixtures reload src.core.config, which
    # rebinds the global to a brand new ContextVar; resetting a token against
    # that new object raises "Token was created by a different ContextVar" and
    # would leave the binding set. Holding the original object means the reset
    # always matches the set that produced the token.
    var = _vault_binding
    token = var.set(str(path))
    try:
        yield
    finally:
        var.reset(token)


def spawn_vault_bound_thread(target, args=(), vault=None, name=None) -> threading.Thread:
    """Start a daemon thread bound to a vault, and return it.

    The captured path travels into the thread as a plain closure value and the
    binding is re-established inside the new thread, so this does not depend on
    implicit ContextVar propagation through any threadpool.

    Pass `vault` explicitly wherever the caller already holds the vault the
    work belongs to (a chat turn captures one at turn start). Omitting it
    captures whatever is effective at spawn time, which closes the
    spawn-to-execution window but not any earlier one.
    """
    if vault is None:
        try:
            vault = get_private_vault_path()
        except ValueError:
            vault = None
    bound = str(vault) if vault is not None else None

    def _run() -> None:
        with vault_binding(bound):
            # Fires only when a swap actually landed between spawn and here --
            # the race this binding exists to close. Silent on every normal
            # turn, so it is signal rather than noise, and it is the runtime
            # confirmation that the bound path executed (issue #144).
            if bound is not None and _vault_path_override is not None:
                if Path(_vault_path_override).resolve() != Path(bound).resolve():
                    logger.warning(
                        "[VAULT_BIND] vault swapped mid-flight; holding deferred "
                        "write to its queue-time vault %s (active is now %s)",
                        bound,
                        _vault_path_override,
                    )
            target(*args)

    thread = threading.Thread(target=_run, daemon=True, name=name)
    thread.start()
    return thread


def set_vault_path_override(path: str, label: str) -> None:
    """Set a runtime vault path override. Memory-only — does not touch .env."""
    global _vault_path_override, _vault_label
    _vault_path_override = path
    _vault_label = label


def clear_vault_path_override() -> None:
    """Clear the runtime vault path override, reverting to .env."""
    global _vault_path_override, _vault_label
    _vault_path_override = None
    _vault_label = None


def get_vault_label() -> str:
    """Return the current vault label ('live', 'demo', 'test', or 'default')."""
    return _vault_label or "default"


def get_vault_override() -> tuple[str | None, str | None]:
    """Return the current (path, label) override, both None when unset.

    Lets a caller capture the override before changing it and put it back
    if the change cannot be verified, without reaching into the globals.
    """
    return _vault_path_override, _vault_label


class VaultWriteBlocked(RuntimeError):
    """Raised when a vault write is refused because the active vault
    could not be verified. See block_vault_writes() below."""


# Fail-closed guard for vault swaps. Set when a swap could not be verified
# to have fully taken effect. While a reason is set, vault write paths
# refuse to write rather than risk writing into the wrong vault. Cleared
# by the next successfully verified swap. Memory-only, like the override
# above, so it resets on API restart.
_vault_write_block_reason: str | None = None


def block_vault_writes(reason: str) -> None:
    """Refuse all vault writes until a verified swap clears the block.

    Called when a vault swap cannot be confirmed. Writing into an
    unverified vault risks landing personal records in the wrong vault,
    which is worse than refusing the write.
    """
    global _vault_write_block_reason
    _vault_write_block_reason = reason


def allow_vault_writes() -> None:
    """Clear the vault write block after a verified swap."""
    global _vault_write_block_reason
    _vault_write_block_reason = None


def vault_writes_blocked() -> str | None:
    """Return the block reason, or None when vault writes are permitted."""
    return _vault_write_block_reason


def get_known_vault_paths() -> dict[str, str]:
    """Read known vault paths from .env (VAULT_PATH_LIVE, etc.).

    Includes the personal vault under the label 'private_vault' so the
    swap endpoint can return to it without requiring an API restart.
    """
    paths: dict[str, str] = {}
    for label in ("live", "demo", "test"):
        env_key = f"VAULT_PATH_{label.upper()}"
        val = os.getenv(env_key)
        if val:
            paths[label] = val
    # The personal vault from PRIVATE_VAULT_PATH is always available as
    # 'private_vault' so the swap endpoint can revert to it.
    private_path = os.getenv("PRIVATE_VAULT_PATH")
    if private_path:
        paths["private_vault"] = private_path
    return paths


def is_dev_mode() -> bool:
    """Return True if EMBER_DEV_MODE=true in environment."""
    return os.getenv("EMBER_DEV_MODE", "").lower() in ("true", "1", "yes")


def get_ember_api_key() -> str | None:
    """
    Returns the API key required to access Ember-2 endpoints, or None if not set.

    Looks in this order:
      1. System credential store via keyring (Windows Credential Manager,
         macOS Keychain, or Linux Secret Service)
         Set with: python scripts/set_api_key.py
      2. EMBER_API_KEY env var / .env (fallback for tests and non-Windows environments)

    All endpoints except GET / require either:
      Authorization: Bearer <key>   (Ember UI / OpenAI-compatible clients)
      X-API-Key: <key>              (direct API access)
    """
    try:
        import keyring
        key = keyring.get_password("ember-2", "api_key")
        if key:
            return key
    except Exception:
        pass
    return os.getenv("EMBER_API_KEY") or None


def get_provider_api_key(provider: str) -> str | None:
    """Return a cloud provider API key (Anthropic, OpenAI, etc.).

    Looks in this order:
      1. System credential store via keyring under service "ember-2-{provider}"
      2. {PROVIDER}_API_KEY env var (e.g. ANTHROPIC_API_KEY, OPENAI_API_KEY)

    Returns None if not found, never raises. Distinct from get_ember_api_key()
    which returns the Ember UI auth key, not provider keys.
    """
    try:
        import keyring
        key = keyring.get_password(f"ember-2-{provider}", "api_key")
        if key:
            return key
    except Exception:
        pass
    return os.getenv(f"{provider.upper()}_API_KEY") or None


def get_ember_embed_model() -> str:
    """
    Returns the Ollama embedding model for vector indexing.
    Set EMBER_EMBED_MODEL in .env to override the default.
      EMBER_EMBED_MODEL=nomic-embed-text
    """
    return os.getenv("EMBER_EMBED_MODEL", "nomic-embed-text")


def get_tier_recency_halflife_days() -> int:
    """Halflife in days for recency decay in tiering (ADR-015)."""
    return int(os.getenv("TIER_RECENCY_HALFLIFE_DAYS", "30"))


def get_tier_access_ceiling() -> int:
    """Retrieval count at which access_score saturates at 1.0 (ADR-015)."""
    return int(os.getenv("TIER_ACCESS_CEILING", "10"))


def get_tier_hot_threshold() -> float:
    """Heat score >= this value = hot tier (ADR-015)."""
    return float(os.getenv("TIER_HOT_THRESHOLD", "0.5"))


def get_tier_warm_threshold() -> float:
    """Heat score >= this value = warm tier; below = cold (ADR-015)."""
    return float(os.getenv("TIER_WARM_THRESHOLD", "0.2"))


def get_state_staleness_days() -> int:
    """Max age in days for next_action and open_loop state records."""
    return int(os.getenv("STATE_STALENESS_DAYS", "7"))


def get_retrieval_min_raw_score() -> float:
    """Minimum raw cosine similarity for default policy relevance gate."""
    return float(os.getenv("RETRIEVAL_MIN_RAW_SCORE", "0.5"))


def get_intent_classifier_timeout_ms() -> int:
    """Hard timeout for Stage 3 of the ADR-034 intent classifier.

    Per ADR-034, Stage 3 calls qwen3:8b in non-thinking mode. The default
    cap is 1500ms on target hardware: the B1 audit (docs/audits/b1_stage2_
    confidence_v018.md) measured 5/5 Stage 3 timeouts at 830-857ms under
    the prior 800ms cap, meaning the safe-default fired in place of any
    real Stage 3 decision. Raising to 1500ms lets the prompt content
    influence outcome on ambiguous queries while preserving the bounded
    worst-case latency contribution.

    On timeout the classifier still falls back to vault_answerable (the
    behavioral-contract-safe default).
    """
    return int(os.getenv("INTENT_CLASSIFIER_TIMEOUT_MS", "1500"))


def get_ember_debug() -> bool:
    """Return True when EMBER_DEBUG is enabled.

    Gates diagnostic logs that may include query, response, or vault
    content. Evaluated at call time so toggling the env var without an
    API restart still takes effect on the next call. Default is False so
    privacy-sensitive diagnostics stay off unless an operator opts in.
    """
    return os.getenv("EMBER_DEBUG", "").lower() in ("1", "true", "yes")


def get_ember_classifier_telemetry() -> bool:
    """Return True when EMBER_CLASSIFIER_TELEMETRY is enabled.

    Gates the per-call intent classification training-pipeline log line
    (ADR-034 Upgrade Path). Separate from EMBER_DEBUG so the SetFit
    training feed can run with scrubbed query telemetry independently of
    full diagnostic logging. Default False.
    """
    return os.getenv("EMBER_CLASSIFIER_TELEMETRY", "").lower() in ("1", "true", "yes")


def get_ember_vision_model() -> str | None:
    """
    Returns the Ollama vision model for image analysis, or None if not configured.
    Set EMBER_VISION_MODEL in .env to enable image analysis.
      EMBER_VISION_MODEL=qwen3-vl:8b
    """
    return os.getenv("EMBER_VISION_MODEL") or None


def get_ember_model() -> str:
    """
    Returns the model name to use for Ember-2.

    Checks (in order):
      1. Persisted model override (vault/model_override.json) — set by UI model switcher
      2. EMBER_MODEL from .env
      3. Default: "qwen3:8b"

    Supports both local (Ollama) and cloud (Anthropic) models.
    """
    # Check persisted override first. safe_read_json returns {} silently for a
    # missing override (the common case) and logs + returns {} on corruption
    # (ADR-039), so a bad override file falls back to the env/default model.
    from src.core.jsonio import safe_read_json
    vault = get_private_vault_path()
    override_path = vault / "model_override.json"
    data = safe_read_json(override_path, default={})
    if isinstance(data, dict):
        model = data.get("model")
        if model:
            return model
    return os.getenv("EMBER_MODEL", "qwen3:8b")


def set_ember_model_override(model: str) -> None:
    """Persist a model selection so it survives API restarts."""
    from src.core.jsonio import safe_write_json
    vault = get_private_vault_path()
    override_path = vault / "model_override.json"
    safe_write_json(override_path, {"model": model})


# Cloud model providers and their available models.
# Keys are provider names matching keyring service "ember-2-{provider}".
CLOUD_MODELS: dict[str, list[str]] = {
    "anthropic": [
        "claude-sonnet-4-5-20250929",
        "claude-haiku-4-5-20251001",
    ],
    "openai": [
        "gpt-4o-mini",
        "gpt-4o",
        "gpt-4-turbo",
        "gpt-3.5-turbo",
    ],
}


def get_cloud_models() -> dict[str, list[str]]:
    """Return the cloud model catalog."""
    return CLOUD_MODELS
