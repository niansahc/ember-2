"""
scripts/setup_wizard.py

Ember-2 interactive setup wizard.

Walks through all required configuration interactively — no manual file
editing needed. Covers:

  1. Vault location
  2. Model selection (detects installed Ollama models)
  3. Vision model (optional)
  4. API host (local or Tailscale)
  5. Creates vault directory
  6. Writes .env
  7. Installs Python dependencies

After running this script, continue with:
  - python scripts/set_api_key.py   (store API key in Credential Manager)
  - docker compose up -d            (start SearXNG)
  - start_api.bat                   (start Ember)

For the full manual setup guide, see SETUP.md.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = REPO_ROOT / ".env"
REQUIREMENTS_PATH = REPO_ROOT / "requirements.txt"

DEFAULT_VAULT = r"C:\EmberVault"
DEFAULT_MODEL = "qwen2.5:14b"
DEFAULT_VISION = "llama3.2-vision:11b"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ask(prompt: str, default: str = "") -> str:
    """Prompt the user for input, returning default if they press Enter."""
    if default:
        response = input(f"{prompt} [{default}]: ").strip()
        return response or default
    return input(f"{prompt}: ").strip()


def ask_yes_no(prompt: str, default: bool = False) -> bool:
    hint = "Y/n" if default else "y/N"
    response = input(f"{prompt} [{hint}]: ").strip().lower()
    if not response:
        return default
    return response.startswith("y")


def header(text: str) -> None:
    print(f"\n{'─' * 50}")
    print(f"  {text}")
    print(f"{'─' * 50}")


def success(text: str) -> None:
    print(f"  ✓ {text}")


def info(text: str) -> None:
    print(f"  → {text}")


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def get_ollama_models() -> list[str]:
    """Return list of installed Ollama model names, or empty list on failure."""
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True, text=True, timeout=10,
        )
        lines = result.stdout.strip().splitlines()
        models = []
        for line in lines[1:]:  # skip header row
            parts = line.split()
            if parts:
                models.append(parts[0])
        return models
    except Exception:
        return []


def step_vault() -> str:
    header("Step 1 — Memory Vault Location")
    print("  This is where Ember stores all your memories, journal entries,")
    print("  and reflections. Choose a local drive — not OneDrive or Dropbox.")
    print()
    vault = ask("Vault path", DEFAULT_VAULT)
    vault_path = Path(vault)

    if vault_path.exists():
        success(f"Folder already exists: {vault_path}")
    else:
        vault_path.mkdir(parents=True, exist_ok=True)
        success(f"Created: {vault_path}")

    return vault


def step_model(models: list[str]) -> str:
    header("Step 2 — Primary Model")
    print("  This is the model Ember uses for conversation and reasoning.")
    print()

    if models:
        print("  Installed Ollama models:")
        for i, m in enumerate(models, 1):
            print(f"    {i}. {m}")
        print()
        choice = ask("Enter a number or type a model name", DEFAULT_MODEL)
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(models):
                return models[idx]
        return choice
    else:
        info("Could not detect installed Ollama models — type the model name manually.")
        return ask("Model name", DEFAULT_MODEL)


def step_vision(models: list[str]) -> str | None:
    header("Step 3 — Vision Model (Optional)")
    print("  Enables image analysis in chat. Requires a vision-capable model.")
    print("  Skip this if you don't need image support.")
    print()

    enable = ask_yes_no("Enable vision?", default=False)
    if not enable:
        info("Vision disabled.")
        return None

    if models:
        vision_models = [m for m in models if "vision" in m.lower()]
        if vision_models:
            print("  Vision-capable models detected:")
            for i, m in enumerate(vision_models, 1):
                print(f"    {i}. {m}")
            print()
            choice = ask("Enter a number or type a model name", vision_models[0])
            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(vision_models):
                    return vision_models[idx]
            return choice
        else:
            info("No vision models detected — type the model name manually.")

    return ask("Vision model name", DEFAULT_VISION)


def step_host() -> str:
    header("Step 4 — API Host")
    print("  Controls which network address the Ember API listens on.")
    print()
    print("    127.0.0.1  — local access only (recommended default)")
    print("    Tailscale  — enter your Tailscale IP to allow phone/remote access")
    print()
    use_tailscale = ask_yes_no("Are you using Tailscale for remote access?", default=False)
    if use_tailscale:
        host = ask("Your Tailscale IP (e.g. 100.x.x.x)")
        if not host:
            info("No IP entered — defaulting to 127.0.0.1")
            return "127.0.0.1"
        return host
    return "127.0.0.1"


def step_write_env(vault: str, model: str, vision: str | None, host: str) -> None:
    header("Step 5 — Writing .env")

    # Use forward slashes for cross-platform compatibility
    vault_fwd = vault.replace("\\", "/")

    lines = [
        "# Generated by setup_wizard.py — edit this file to change settings.\n",
        "\n",
        "# ── Vault ─────────────────────────────────────────────────────────\n",
        f"PRIVATE_VAULT_PATH={vault_fwd}\n",
        "\n",
        "# ── API Host ───────────────────────────────────────────────────────\n",
        "# Use 127.0.0.1 for local-only access, or your Tailscale IP for remote.\n",
        f"EMBER_HOST={host}\n",
        "\n",
        "# ── Models ─────────────────────────────────────────────────────────\n",
        f"EMBER_MODEL={model}\n",
    ]

    if vision:
        lines.append(f"EMBER_VISION_MODEL={vision}\n")
    else:
        lines.append("# EMBER_VISION_MODEL=  (vision disabled — uncomment and set to enable)\n")

    lines += [
        "\n",
        "# ── API Key ────────────────────────────────────────────────────────\n",
        "# API key is stored in Windows Credential Manager — not here.\n",
        "# Run: python scripts/set_api_key.py\n",
    ]

    ENV_PATH.write_text("".join(lines), encoding="utf-8")
    success(f"Written: {ENV_PATH}")


def step_install_deps() -> None:
    header("Step 6 — Installing Dependencies")
    print("  This may take a few minutes — sentence-transformers and torch are large.")
    print()

    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS_PATH)],
        cwd=REPO_ROOT,
    )

    if result.returncode == 0:
        success("Dependencies installed.")
    else:
        print("\n  ✗ pip install failed. Check the output above for errors.")
        print("    You can retry manually: pip install -r requirements.txt")


def print_next_steps(host: str) -> None:
    header("Setup Complete")
    print()
    print("  Next steps:")
    print()
    print("  1. Store your API key:")
    print("       python scripts/set_api_key.py")
    print()
    print("  2. Start SearXNG (web search):")
    print("       docker compose up -d")
    print()
    print("  3. Start Ember:")
    print("       start_api.bat")
    print()
    print("  4. Open Open WebUI in your browser:")
    print(f"       http://{host}:3000")
    print()
    print("  5. Connect Open WebUI to Ember:")
    print(f"       Settings → Connections → OpenAI API Base URL: http://{host}:8000/v1")
    print("       API Key: (the key from step 1)")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print()
    print("  ╔══════════════════════════════════════╗")
    print("  ║        Ember-2 Setup Wizard          ║")
    print("  ╚══════════════════════════════════════╝")
    print()
    print("  This wizard configures Ember-2 for first-time use.")
    print("  Press Enter to accept the default shown in [brackets].")
    print()
    print("  For the full manual setup guide, see SETUP.md.")

    models = get_ollama_models()

    vault = step_vault()
    model = step_model(models)
    vision = step_vision(models)
    host = step_host()
    step_write_env(vault, model, vision, host)
    step_install_deps()
    print_next_steps(host)


if __name__ == "__main__":
    main()
