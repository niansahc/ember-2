"""
scripts/set_api_key.py

Stores the Ember-2 API key in Windows Credential Manager.
Run this once after setup, or again to rotate the key.

The API key is never written to .env or any plaintext file.
The stored credential is DPAPI-encrypted and tied to your Windows login.

Usage:
    python scripts/set_api_key.py
"""

import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import keyring

SERVICE = "ember-2"
USERNAME = "api_key"


def main():
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
