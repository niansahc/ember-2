from pathlib import Path
import os

from dotenv import load_dotenv


load_dotenv()


def get_private_vault_path():
    """
    Reads the PRIVATE_VAULT_PATH from the .env file
    and returns the absolute path to the vault.
    """
    vault_path = os.getenv("PRIVATE_VAULT_PATH")

    if not vault_path:
        raise ValueError("PRIVATE_VAULT_PATH not set in environment")

    return Path(vault_path).resolve()


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
