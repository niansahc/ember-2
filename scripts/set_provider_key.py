"""
scripts/set_provider_key.py

Securely store, check, or remove a cloud provider API key.
Keys are stored in the OS credential manager (Windows Credential Manager,
macOS Keychain, etc.) via the keyring library.

Usage:
    python scripts/set_provider_key.py --provider anthropic
    python scripts/set_provider_key.py --provider openai
    python scripts/set_provider_key.py --provider anthropic --check
    python scripts/set_provider_key.py --provider anthropic --remove
"""

import argparse
import getpass
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

VALID_PROVIDERS = {"anthropic", "openai"}


def main():
    parser = argparse.ArgumentParser(
        description="Store, check, or remove a cloud provider API key.",
    )
    parser.add_argument(
        "--provider",
        required=True,
        choices=sorted(VALID_PROVIDERS),
        help="Cloud provider name (anthropic, openai)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check if a key is configured. Exits 0 if yes, 1 if no.",
    )
    parser.add_argument(
        "--remove",
        action="store_true",
        help="Remove the stored key.",
    )
    args = parser.parse_args()

    service = f"ember-2-{args.provider}"
    username = "api_key"

    try:
        import keyring
    except ImportError:
        print("Error: keyring package not installed.")
        print("Install with: pip install keyring")
        sys.exit(1)

    if args.check:
        key = None
        try:
            key = keyring.get_password(service, username)
        except Exception:
            pass
        if key:
            print(f"{args.provider.capitalize()} API key is configured.")
            sys.exit(0)
        else:
            print(f"{args.provider.capitalize()} API key is NOT configured.")
            sys.exit(1)

    if args.remove:
        try:
            keyring.delete_password(service, username)
            print(f"{args.provider.capitalize()} API key removed.")
        except keyring.errors.PasswordDeleteError:
            print(f"No {args.provider.capitalize()} API key was stored.")
        except Exception as exc:
            print(f"Error removing key: {exc}")
            sys.exit(1)
        return

    # Store mode
    print(f"Enter your {args.provider.capitalize()} API key.")
    print("The key will be stored securely in your OS credential manager.")
    print()

    key = getpass.getpass("API key: ")
    if not key.strip():
        print("No key entered. Cancelled.")
        sys.exit(1)

    try:
        keyring.set_password(service, username, key.strip())
        print(f"{args.provider.capitalize()} API key stored securely.")
    except Exception as exc:
        print(f"Error storing key: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
