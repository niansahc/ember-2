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
      1. Windows Credential Manager (service: ember-2, username: api_key)
         Store with: python scripts/set_api_key.py
      2. EMBER_API_KEY env var / .env (fallback for tests and non-Windows environments)

    All endpoints except GET / require either:
      Authorization: Bearer <key>   (Open WebUI / OpenAI-compatible clients)
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

    Reads EMBER_MODEL from .env. Defaults to "qwen3:8b" if not set.

    Supports both local (Ollama) and cloud (Anthropic) models:
      EMBER_MODEL=qwen3:8b               # local via Ollama
      EMBER_MODEL=claude-sonnet-4-20250514  # cloud via Anthropic API
    """
    return os.getenv("EMBER_MODEL", "qwen3:8b")


# Cloud model providers and their available models.
# Keys are provider names matching keyring service "ember-2-{provider}".
CLOUD_MODELS: dict[str, list[str]] = {
    "anthropic": [
        "claude-sonnet-4-20250514",
        "claude-haiku-4-5-20251001",
    ],
}


def get_cloud_models() -> dict[str, list[str]]:
    """Return the cloud model catalog."""
    return CLOUD_MODELS
