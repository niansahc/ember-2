"""
scripts/set_github_token.py

Securely store, check, or remove the GitHub personal access token used
by the POST /v1/bug-report endpoint to create issues server-side.

The token is stored in the OS credential manager (Windows Credential
Manager, macOS Keychain, etc.) via the keyring library. It is never
written to .env, the UI build, or any plaintext file.

The token needs repo:public_repo scope (or full repo scope for private
repos). Create one at https://github.com/settings/tokens.

Usage:
    python scripts/set_github_token.py
    python scripts/set_github_token.py --check
    python scripts/set_github_token.py --remove
"""

import argparse
import getpass
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SERVICE = "ember-2-github"
USERNAME = "token"


def main():
    parser = argparse.ArgumentParser(
        description="Store, check, or remove the GitHub token for bug reports.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check if a token is configured. Exits 0 if yes, 1 if no.",
    )
    parser.add_argument(
        "--remove",
        action="store_true",
        help="Remove the stored token.",
    )
    args = parser.parse_args()

    try:
        import keyring
    except ImportError:
        print("Error: keyring package not installed.")
        print("Install with: pip install keyring")
        sys.exit(1)

    if args.check:
        token = None
        try:
            token = keyring.get_password(SERVICE, USERNAME)
        except Exception:
            pass
        if token:
            print("GitHub token is configured.")
            sys.exit(0)
        else:
            print("GitHub token is NOT configured.")
            sys.exit(1)

    if args.remove:
        try:
            keyring.delete_password(SERVICE, USERNAME)
            print("GitHub token removed.")
        except keyring.errors.PasswordDeleteError:
            print("No GitHub token was stored.")
        except Exception as exc:
            print(f"Error removing token: {exc}")
            sys.exit(1)
        return

    # Store mode
    print("Enter your GitHub personal access token.")
    print("Required scope: repo:public_repo (or full repo for private repos).")
    print("Create one at: https://github.com/settings/tokens")
    print("The token will be stored securely in your OS credential manager.")
    print()

    token = getpass.getpass("GitHub token: ")
    if not token.strip():
        print("No token entered. Cancelled.")
        sys.exit(1)

    try:
        keyring.set_password(SERVICE, USERNAME, token.strip())
        print("GitHub token stored securely.")
    except Exception as exc:
        print(f"Error storing token: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
