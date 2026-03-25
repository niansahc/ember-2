"""
scripts/set_api_key.py

Stores the Ember-2 API key in Windows Credential Manager.
Run this once after setup, or again to rotate the key.

The API key is never written to .env or any plaintext file.
The stored credential is DPAPI-encrypted and tied to your Windows login.

Usage:
    python scripts/set_api_key.py                  Interactive (default)
    python scripts/set_api_key.py --check           Exit 0 if key exists, 1 if not
    python scripts/set_api_key.py --non-interactive Generate key without prompting
"""

import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import keyring

SERVICE = "ember-2"
USERNAME = "api_key"


def check_key():
    """Exit 0 if a key exists, 1 if not. Print the status."""
    existing = keyring.get_password(SERVICE, USERNAME)
    if existing:
        print("API key is configured.")
        sys.exit(0)
    else:
        print("No API key found.")
        sys.exit(1)


def generate_key_non_interactive():
    """Generate and store a key without prompting. Skip if one already exists."""
    existing = keyring.get_password(SERVICE, USERNAME)
    if existing:
        print("API key already configured. No changes made.")
        print(f"Key: {existing}")
        return

    new_key = secrets.token_urlsafe(32)
    keyring.set_password(SERVICE, USERNAME, new_key)
    print(f"API key stored in Windows Credential Manager.")
    print(f"Key: {new_key}")


def main():
    if "--check" in sys.argv:
        check_key()
        return

    if "--non-interactive" in sys.argv:
        generate_key_non_interactive()
        return

    # Interactive mode (original behavior)
    existing = keyring.get_password(SERVICE, USERNAME)

    if existing:
        print(f"An API key is already stored.")
        answer = input("Rotate it with a new key? [y/N]: ").strip().lower()
        if answer != "y":
            print("No changes made.")
            return

    new_key = secrets.token_urlsafe(32)
    keyring.set_password(SERVICE, USERNAME, new_key)
    print(f"\nAPI key stored in Windows Credential Manager.")
    print(f"Key: {new_key}")
    print("\nCopy this key into Open WebUI (Settings -> Connections -> API Key).")
    print("It will not be shown again from this script — retrieve it from Credential Manager if needed.")


if __name__ == "__main__":
    main()
