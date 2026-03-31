"""
src/security/pin_service.py

PIN and recovery passphrase management for UI session lock (ADR-012 Phase 2).

Hashes are stored in the OS credential store via keyring. PINs and
passphrases are NEVER stored in plaintext anywhere -- not in files,
not in logs, not in preferences.json.

Uses bcrypt with work factor 12.
"""

from __future__ import annotations

import logging
import time

import bcrypt

logger = logging.getLogger("ember.pin_service")

# Keyring service names
_PIN_SERVICE = "ember-2-pin"
_RECOVERY_SERVICE = "ember-2-recovery"
_KEYRING_USERNAME = "hash"

# Bcrypt work factor
_BCRYPT_ROUNDS = 12

# Rate limiting: track failed attempts per IP (module-level, resets on restart)
_failed_attempts: dict[str, list[float]] = {}
_MAX_ATTEMPTS = 5
_LOCKOUT_SECONDS = 300  # 5 minutes


def _hash(value: str) -> str:
    """Hash a PIN or passphrase with bcrypt. Never log the input."""
    return bcrypt.hashpw(value.encode("utf-8"), bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)).decode("utf-8")


def _verify(value: str, stored_hash: str) -> bool:
    """Verify a PIN or passphrase against a stored bcrypt hash."""
    try:
        return bcrypt.checkpw(value.encode("utf-8"), stored_hash.encode("utf-8"))
    except Exception:
        return False


def _get_keyring(service: str) -> str | None:
    """Read a hash from the OS credential store."""
    try:
        import keyring
        return keyring.get_password(service, _KEYRING_USERNAME)
    except Exception as exc:
        logger.warning("[PIN_SERVICE] Failed to read from keyring: %s", exc)
        return None


def _set_keyring(service: str, value: str) -> None:
    """Write a hash to the OS credential store."""
    try:
        import keyring
        keyring.set_password(service, _KEYRING_USERNAME, value)
    except Exception as exc:
        logger.warning("[PIN_SERVICE] Failed to write to keyring: %s", exc)
        raise


def _delete_keyring(service: str) -> None:
    """Delete a hash from the OS credential store."""
    try:
        import keyring
        keyring.delete_password(service, _KEYRING_USERNAME)
    except Exception:
        pass  # Not found is fine


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

def check_rate_limit(client_ip: str) -> bool:
    """
    Check if a client is rate-limited. Returns True if allowed, False if locked out.
    """
    now = time.time()
    attempts = _failed_attempts.get(client_ip, [])
    # Remove attempts older than lockout window
    attempts = [t for t in attempts if now - t < _LOCKOUT_SECONDS]
    _failed_attempts[client_ip] = attempts
    return len(attempts) < _MAX_ATTEMPTS


def record_failed_attempt(client_ip: str) -> int:
    """Record a failed attempt. Returns remaining attempts before lockout."""
    now = time.time()
    attempts = _failed_attempts.get(client_ip, [])
    attempts = [t for t in attempts if now - t < _LOCKOUT_SECONDS]
    attempts.append(now)
    _failed_attempts[client_ip] = attempts
    return max(0, _MAX_ATTEMPTS - len(attempts))


def get_remaining_attempts(client_ip: str) -> int:
    """Get remaining attempts before lockout."""
    now = time.time()
    attempts = _failed_attempts.get(client_ip, [])
    attempts = [t for t in attempts if now - t < _LOCKOUT_SECONDS]
    return max(0, _MAX_ATTEMPTS - len(attempts))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def pin_is_set() -> bool:
    """Check if a PIN has been configured."""
    return _get_keyring(_PIN_SERVICE) is not None


def set_pin(pin: str) -> None:
    """Hash and store a PIN. Never stores plaintext."""
    hashed = _hash(pin)
    _set_keyring(_PIN_SERVICE, hashed)
    logger.info("[PIN_SERVICE] PIN set successfully")


def verify_pin(pin: str) -> bool:
    """Verify a PIN against the stored hash. Never logs the PIN value."""
    stored = _get_keyring(_PIN_SERVICE)
    if not stored:
        return False
    return _verify(pin, stored)


def set_recovery_passphrase(passphrase: str) -> None:
    """Hash and store a recovery passphrase. Never stores plaintext."""
    hashed = _hash(passphrase)
    _set_keyring(_RECOVERY_SERVICE, hashed)
    logger.info("[PIN_SERVICE] Recovery passphrase set successfully")


def verify_recovery_passphrase(passphrase: str) -> bool:
    """Verify a recovery passphrase against the stored hash."""
    stored = _get_keyring(_RECOVERY_SERVICE)
    if not stored:
        return False
    return _verify(passphrase, stored)


def clear_pin() -> None:
    """Remove both PIN and recovery passphrase from keyring."""
    _delete_keyring(_PIN_SERVICE)
    _delete_keyring(_RECOVERY_SERVICE)
    logger.info("[PIN_SERVICE] PIN and recovery passphrase cleared")
