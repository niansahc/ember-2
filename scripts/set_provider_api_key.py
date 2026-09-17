"""
scripts/set_provider_api_key.py

Stores a cloud provider API key (Anthropic, OpenAI) in the system
credential store (Windows Credential Manager, macOS Keychain, or Linux
Secret Service via the keyring library), matching get_provider_api_key()'s
keyring-then-env-fallback resolution in src/core/config.py and the
service/username shape the /provider-key HTTP endpoints already use
(keyring.set_password(f"ember-2-{provider}", "api_key", ...)).

Mirrors scripts/set_api_key.py's CLI shape and rotate flow, with one
necessary difference: that script GENERATES Ember's own self-issued auth
token. A provider key is the user's real, externally-obtained secret, so
this script PROMPTS FOR and stores it (via getpass, never echoed) rather
than generating one.

The key is never written to .env or any plaintext file, and is never
printed by this script.

Usage:
    python scripts/set_provider_api_key.py anthropic          Interactive (default)
    python scripts/set_provider_api_key.py anthropic --check  Exit 0 if key exists, 1 if not
    python scripts/set_provider_api_key.py openai
"""

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import keyring

# Matches src/api/main.py's allowed_providers exactly -- one definition,
# not a second copy that can drift.
ALLOWED_PROVIDERS = {"anthropic", "openai"}


def _service_for(provider: str) -> str:
    return f"ember-2-{provider}"


def check_key(provider: str) -> None:
    """Exit 0 if a key exists for `provider`, 1 if not. Print status only,
    never the key."""
    existing = keyring.get_password(_service_for(provider), "api_key")
    if existing:
        print(f"{provider} API key is configured.")
        sys.exit(0)
    else:
        print(f"No {provider} API key found.")
        sys.exit(1)


def set_key_interactive(provider: str) -> None:
    service = _service_for(provider)
    existing = keyring.get_password(service, "api_key")

    if existing:
        print(f"A {provider} API key is already stored.")
        answer = input("Rotate it with a new key? [y/N]: ").strip().lower()
        if answer != "y":
            print("No changes made.")
            return

    new_key = getpass.getpass(f"Enter your {provider} API key (input hidden): ").strip()
    if not new_key:
        print("No key entered. No changes made.")
        return

    keyring.set_password(service, "api_key", new_key)
    print(f"\n{provider} API key stored in system credential store.")
    print("It will not be shown again from this script -- retrieve it from "
          "your system's credential manager if needed.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "provider",
        choices=sorted(ALLOWED_PROVIDERS),
        help="Which provider's key to set (anthropic or openai).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 0 if a key exists, 1 if not. Does not prompt.",
    )
    args = parser.parse_args()

    if args.check:
        check_key(args.provider)
        return

    set_key_interactive(args.provider)


if __name__ == "__main__":
    main()
