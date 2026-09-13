"""
tools/eval_helpers.py

Shared helpers for eval tools: test vault isolation and post-run cleanup.

- swap_to_test_vault / restore_vault: switch the running API process to
  the test vault for eval isolation. Calls POST /v1/developer/vault/swap
  on the live API so the swap reaches the API process. Setting only an
  in-process Python global (the prior implementation) did NOT reach the
  API, which meant eval tools silently ran against the live vault. Bug
  fixed 2026-04-30.

- Privacy posture: swap_to_test_vault fails closed via sys.exit(1) on
  any path that would let the eval proceed against the live vault.
  This includes: missing VAULT_PATH_TEST env var, missing test-vault
  directory, and any swap-call failure (connection refused, 403
  dev-mode disabled, 400 unknown label, 5xx). Silent-return-None was
  the original 2026-04-30 leak: callers cannot distinguish "swap
  skipped" from "swap succeeded," so any None path is treated as a
  privacy regression and made fatal.

- run_cleanup: invoke the same logic as cleanup_test_artifacts.py
  --confirm to archive eval artifacts from the active vault silently.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


_DEFAULT_API_BASE = "http://localhost:8000"
_VAULT_SWAP_PATH = "/v1/developer/vault/swap"


def _api_base() -> str:
    """Resolve the eval target API base URL. EMBER_API_BASE overrides
    the localhost default for non-default ports or remote eval setups."""
    return os.getenv("EMBER_API_BASE", _DEFAULT_API_BASE).rstrip("/")


def _swap_headers() -> dict:
    """Build headers for the vault swap POST. Includes Authorization
    when EMBER_API_KEY is configured."""
    from src.core.config import get_ember_api_key

    headers = {"Content-Type": "application/json"}
    api_key = get_ember_api_key()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def swap_to_test_vault() -> str:
    """Switch the running API to the test vault for eval isolation.

    Returns "test" on successful swap. On any failure path - missing
    VAULT_PATH_TEST env var, missing test-vault directory, or a failed
    swap call - prints a fatal message and exits the process. This
    prevents the caller from proceeding against the live vault.

    Missing env / missing dir are configuration errors, not "graceful
    skip" conditions. The PR #37 design intent is that no eval ever
    runs against the live vault by accident; any None return from this
    helper would re-open that hole.
    """
    test_path = os.getenv("VAULT_PATH_TEST")
    if not test_path:
        print(
            "FATAL: VAULT_PATH_TEST is not set. Eval tools require an "
            "explicit test vault to fail closed against the live vault. "
            "Set VAULT_PATH_TEST in .env and export it into the shell "
            "before invoking eval (load_dotenv runs inside the API, not "
            "in eval-tool subprocesses)."
        )
        sys.exit(1)

    resolved = Path(test_path).resolve()
    if not resolved.is_dir():
        print(
            f"FATAL: VAULT_PATH_TEST ({resolved}) does not exist or is "
            "not a directory. Refusing to proceed against the live vault. "
            "Create the test vault directory or correct VAULT_PATH_TEST."
        )
        sys.exit(1)

    url = f"{_api_base()}{_VAULT_SWAP_PATH}"
    try:
        response = httpx.post(
            url,
            json={"vault_label": "test"},
            headers=_swap_headers(),
            timeout=30.0,
        )
        response.raise_for_status()
    except Exception as exc:
        print(f"FATAL: vault swap to test failed: {exc}")
        print("Refusing to proceed against live vault. Aborting.")
        sys.exit(1)

    print(f"Vault swap (API): {response.json()}")
    return "test"


def restore_vault(previous_path: str | None) -> None:
    """Restore the API to the default vault after eval.

    The previous_path argument is retained for caller compatibility but
    is no longer needed: the API endpoint reverts to PRIVATE_VAULT_PATH
    when called with vault_label="default". A None argument indicates
    swap_to_test_vault was skipped (no swap occurred), so no restore
    call is fired.

    Best-effort: prints a WARNING on failure but does not exit. The eval
    has already finished by the time restore runs; manual restore via
    POST /v1/developer/vault/swap {"vault_label": "default"} or an API
    restart is documented in the failure message.
    """
    if previous_path is None:
        return

    url = f"{_api_base()}{_VAULT_SWAP_PATH}"
    try:
        response = httpx.post(
            url,
            json={"vault_label": "default"},
            headers=_swap_headers(),
            timeout=30.0,
        )
        response.raise_for_status()
        print(f"Vault restore (API): {response.json()}")
    except Exception as exc:
        print(
            f"WARNING: vault restore failed: {exc}. "
            "Manual restore: POST /v1/developer/vault/swap "
            "{'vault_label': 'default'} or restart the API."
        )


def run_cleanup() -> None:
    """Run artifact cleanup against the currently active vault.

    Calls the same scan_vault + archive_records logic as
    scripts/cleanup_test_artifacts.py --confirm. Silent: only prints
    if records were archived.
    """
    try:
        scripts_dir = REPO_ROOT / "scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))

        from cleanup_test_artifacts import scan_vault, archive_records
        from src.core.config import get_private_vault_path

        vault = get_private_vault_path()
        matches = scan_vault(vault)
        if matches:
            moved = archive_records(vault, matches)
            print(f"Cleaned up {moved} eval artifact(s) from vault.")
    except Exception as exc:
        print(f"WARNING: Cleanup failed (non-fatal): {exc}")


# ---------------------------------------------------------------------------
# Model pinning (ADR-043)
#
# A sweep must move the generation model without persisting it. POST /model
# with persist=false leaves model_override.json alone, so every call-time role
# -- intent classifier, coaching filter, deviation detector, reflection --
# keeps resolving the reference model. Without this the candidate becomes the
# router that selects its own test path, and the ranking measures routing as
# much as quality.
#
# Note that persist=false still moves llm_adapter.model, so a sweep must
# restore it or the running API keeps generating with the last candidate.
# Nothing on disk changes, so that failure is invisible.
# ---------------------------------------------------------------------------

_MODEL_PATH = "/model"


class ModelPinError(RuntimeError):
    """A pin could not be established or verified. Always fatal to a sweep:
    continuing would score results under an unknown model."""


def read_model_state() -> dict:
    """Return GET /model. Includes reference_model and pinned (ADR-043)."""
    resp = httpx.get(
        f"{_api_base()}{_MODEL_PATH}",
        headers=_swap_headers(),
        timeout=10.0,
    )
    resp.raise_for_status()
    return resp.json()


def _installed_and_cloud_models(state: dict) -> set:
    """Every model name the server will accept, local tags plus cloud ids.

    Beats shelling out to `ollama list`: it is one round trip to the server
    actually being driven, and it includes cloud ids, which a local tag list
    cannot.
    """
    names = set(state.get("available") or [])
    cloud = state.get("cloud") or {}
    if isinstance(cloud, dict):
        for entry in cloud.values():
            if isinstance(entry, (list, tuple, set)):
                names.update(entry)
            elif isinstance(entry, str):
                names.add(entry)
    elif isinstance(cloud, (list, tuple, set)):
        names.update(cloud)
    return names


def pin_model(model: str) -> dict:
    """Pin the generation model to `model` without persisting it.

    Implements both harness obligations from ADR-043:

    Validation before posting -- POST /model accepts any string, and under
    persist=false a typo leaves no trace on disk at all, so a mistyped tag
    would otherwise produce a full sweep scored against a model that was never
    loaded.

    Re-assertion after posting -- the pin is in-memory only, and a --reload
    uvicorn reverts it silently, which would relabel reference results as
    candidate results. Verify rather than assume.

    Returns the verified post-pin state. Raises ModelPinError on any failure.
    """
    state = read_model_state()

    if "pinned" not in state:
        raise ModelPinError(
            "server does not report `pinned` on GET /model, so it predates "
            "ADR-043. A persist=false swap would be silently ignored and the "
            "model would still be written to model_override.json."
        )

    known = _installed_and_cloud_models(state)
    # An empty `available` means Ollama was unreachable (the handler's own
    # except yields []), not that no models exist. Treat that as inconclusive
    # and let the POST decide, rather than rejecting every candidate.
    if known and model not in known:
        raise ModelPinError(
            f"{model!r} is not available on the target server. "
            f"Known: {', '.join(sorted(known))}"
        )

    resp = httpx.post(
        f"{_api_base()}{_MODEL_PATH}",
        json={"model": model, "persist": False},
        headers=_swap_headers(),
        timeout=30.0,
    )
    resp.raise_for_status()

    verified = read_model_state()
    if verified.get("model") != model:
        raise ModelPinError(
            f"pin did not take: asked for {model!r}, server reports "
            f"{verified.get('model')!r}"
        )
    if not verified.get("pinned"):
        raise ModelPinError(
            f"{model!r} is active but not pinned -- it matches the reference "
            f"model {verified.get('reference_model')!r}, so this sweep would "
            f"not be isolating the candidate."
        )
    return verified


def restore_model(model: str) -> None:
    """Put the generation model back, without persisting.

    Best-effort and never raises: this runs in a finally block, where a raise
    would mask the original failure. The override was never written, so the
    worst case is an API left on the last candidate until it restarts.
    """
    try:
        httpx.post(
            f"{_api_base()}{_MODEL_PATH}",
            json={"model": model, "persist": False},
            headers=_swap_headers(),
            timeout=30.0,
        )
    except Exception as exc:
        print(f"WARNING: Could not restore model to {model}: {exc}")
