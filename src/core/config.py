from pathlib import Path
import os

from dotenv import load_dotenv


load_dotenv()


# Runtime vault path override. Set by the developer vault-swap endpoint
# (POST /v1/developer/vault/swap). Takes precedence over PRIVATE_VAULT_PATH
# from .env. Reverts on API restart — never persisted to disk.
_vault_path_override: str | None = None
_vault_label: str | None = None


def get_private_vault_path():
    """
    Reads the PRIVATE_VAULT_PATH from the .env file
    and returns the absolute path to the vault.

    If a runtime override has been set via set_vault_path_override(),
    that takes precedence. The override is memory-only and reverts
    on API restart.
    """
    if _vault_path_override is not None:
        return Path(_vault_path_override).resolve()

    vault_path = os.getenv("PRIVATE_VAULT_PATH")

    if not vault_path:
        raise ValueError("PRIVATE_VAULT_PATH not set in environment")

    return Path(vault_path).resolve()


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

    Per ADR-034, Stage 3 calls qwen3:8b in non-thinking mode; 800ms is
    a conservative cap on target hardware. On timeout the classifier
    falls back to vault_answerable (the behavioral-contract-safe default).
    """
    return int(os.getenv("INTENT_CLASSIFIER_TIMEOUT_MS", "800"))


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
      EMBER_VISION_MODEL=llama3.2-vision:11b
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
    # Check persisted override first
    try:
        import json
        vault = get_private_vault_path()
        override_path = vault / "model_override.json"
        if override_path.exists():
            data = json.loads(override_path.read_text(encoding="utf-8"))
            model = data.get("model")
            if model:
                return model
    except Exception:
        pass
    return os.getenv("EMBER_MODEL", "qwen3:8b")


def set_ember_model_override(model: str) -> None:
    """Persist a model selection so it survives API restarts."""
    import json
    vault = get_private_vault_path()
    override_path = vault / "model_override.json"
    override_path.write_text(
        json.dumps({"model": model}, indent=2),
        encoding="utf-8",
    )


# Cloud model providers and their available models.
# Keys are provider names matching keyring service "ember-2-{provider}".
CLOUD_MODELS: dict[str, list[str]] = {
    "anthropic": [
        "claude-sonnet-4-20250514",
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
