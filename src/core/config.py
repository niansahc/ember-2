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


def get_ember_model() -> str:
    """
    Returns the Ollama model name to use for Ember-2.

    Reads EMBER_MODEL from .env. Defaults to "llama3.1:8b" if not set.

    To change the model, set EMBER_MODEL in your .env file:
      EMBER_MODEL=mistral-nemo
      EMBER_MODEL=qwen3:8b
      EMBER_MODEL=llama3.1:8b
    """
    return os.getenv("EMBER_MODEL", "llama3.1:8b")
