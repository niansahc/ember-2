"""
src/api/asgi.py

API process entrypoint. Uvicorn serves `src.api.asgi:app` (start_api.bat,
start_api.sh, scripts/watchdog.py).

This module loads .env and only then imports the app. src.api.main itself
reads no .env, so importing it -- as the test suite does -- leaves the
process environment alone.
"""

import logging

from src.core.config import load_env_file

# Before the app import: the LLMAdapter singletons resolve the model and the
# vault from the environment when src.api.main is imported.
_env_file = load_env_file()

from src.api.main import app  # noqa: E402

__all__ = ["app"]

# Logged after the app import because importing src.api.main configures
# logging; an INFO record emitted before that has no handler.
logging.getLogger("ember.config").info(
    "[CONFIG] API entrypoint loaded .env: %s",
    _env_file if _env_file is not None else "none found",
)
