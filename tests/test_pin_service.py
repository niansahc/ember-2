"""
tests/test_pin_service.py

Tests for PIN/passphrase security service (ADR-012 Phase 2).
"""

import pytest
from unittest.mock import patch, MagicMock

from src.security.pin_service import (
    _hash,
    _verify,
    pin_is_set,
    set_pin,
    verify_pin,
    set_recovery_passphrase,
    verify_recovery_passphrase,
    clear_pin,
    change_pin,
    check_rate_limit,
    record_failed_attempt,
    get_remaining_attempts,
    _failed_attempts,
)


class TestHashing:
    """PIN/passphrase hashing — plaintext never stored."""

    def test_hash_produces_bcrypt_output(self):
        hashed = _hash("1234")
        assert hashed.startswith("$2b$")
        assert len(hashed) == 60

    def test_hash_is_not_plaintext(self):
        hashed = _hash("mypin123")
        assert hashed != "mypin123"

    def test_verify_correct(self):
        hashed = _hash("secret")
        assert _verify("secret", hashed) is True

    def test_verify_incorrect(self):
        hashed = _hash("secret")
        assert _verify("wrong", hashed) is False

    def test_verify_invalid_hash(self):
        assert _verify("anything", "not-a-bcrypt-hash") is False


class TestPinOperations:
    """PIN set/verify/clear via mocked keyring."""

    def test_pin_is_set_false_when_empty(self):
        with patch("src.security.pin_service._get_keyring", return_value=None):
            assert pin_is_set() is False

    def test_pin_is_set_true_when_stored(self):
        with patch("src.security.pin_service._get_keyring", return_value="$2b$12$somehash"):
            assert pin_is_set() is True

    def test_set_pin_stores_hash_not_plaintext(self):
        stored = {}
        def mock_set(service, value):
            stored[service] = value

        with patch("src.security.pin_service._set_keyring", side_effect=mock_set):
            set_pin("1234")

        assert "ember-2-pin" in stored
        # Stored value must be a bcrypt hash, not plaintext
        assert stored["ember-2-pin"].startswith("$2b$")
        assert stored["ember-2-pin"] != "1234"

    def test_verify_pin_correct(self):
        hashed = _hash("5678")
        with patch("src.security.pin_service._get_keyring", return_value=hashed):
            assert verify_pin("5678") is True

    def test_verify_pin_incorrect(self):
        hashed = _hash("5678")
        with patch("src.security.pin_service._get_keyring", return_value=hashed):
            assert verify_pin("0000") is False

    def test_verify_pin_no_pin_set(self):
        with patch("src.security.pin_service._get_keyring", return_value=None):
            assert verify_pin("1234") is False


class TestRecoveryPassphrase:
    """Recovery passphrase set/verify."""

    def test_set_recovery_stores_hash(self):
        stored = {}
        def mock_set(service, value):
            stored[service] = value

        with patch("src.security.pin_service._set_keyring", side_effect=mock_set):
            set_recovery_passphrase("my secret recovery phrase here")

        assert "ember-2-recovery" in stored
        assert stored["ember-2-recovery"].startswith("$2b$")
        assert "secret" not in stored["ember-2-recovery"]

    def test_verify_recovery_correct(self):
        hashed = _hash("correct horse battery staple")
        with patch("src.security.pin_service._get_keyring", return_value=hashed):
            assert verify_recovery_passphrase("correct horse battery staple") is True

    def test_verify_recovery_incorrect(self):
        hashed = _hash("correct horse battery staple")
        with patch("src.security.pin_service._get_keyring", return_value=hashed):
            assert verify_recovery_passphrase("wrong phrase") is False


class TestRateLimiting:
    """Rate limiting on failed PIN attempts."""

    def setup_method(self):
        _failed_attempts.clear()

    def test_first_attempt_allowed(self):
        assert check_rate_limit("127.0.0.1") is True

    def test_five_failures_locks_out(self):
        for _ in range(5):
            record_failed_attempt("127.0.0.1")
        assert check_rate_limit("127.0.0.1") is False

    def test_four_failures_still_allowed(self):
        for _ in range(4):
            record_failed_attempt("127.0.0.1")
        assert check_rate_limit("127.0.0.1") is True

    def test_remaining_attempts_decrements(self):
        assert get_remaining_attempts("127.0.0.1") == 5
        record_failed_attempt("127.0.0.1")
        assert get_remaining_attempts("127.0.0.1") == 4
        record_failed_attempt("127.0.0.1")
        assert get_remaining_attempts("127.0.0.1") == 3

    def test_different_ips_independent(self):
        for _ in range(5):
            record_failed_attempt("192.168.1.1")
        assert check_rate_limit("192.168.1.1") is False
        assert check_rate_limit("192.168.1.2") is True


class TestClearPin:
    """Clear PIN removes both pin and recovery from keyring."""

    def test_clear_calls_delete(self):
        deleted = []
        with patch("src.security.pin_service._delete_keyring", side_effect=lambda s: deleted.append(s)):
            clear_pin()
        assert "ember-2-pin" in deleted
        assert "ember-2-recovery" in deleted


class TestChangePin:
    """change_pin verifies the current PIN before rotating to the new one.
    No recovery-phrase coupling — this is the routine-rotation path."""

    def test_change_pin_returns_false_when_no_pin_set(self):
        with patch("src.security.pin_service._get_keyring", return_value=None):
            assert change_pin("1234", "5678") is False

    def test_change_pin_returns_false_on_wrong_current(self):
        hashed = _hash("1234")
        with patch("src.security.pin_service._get_keyring", return_value=hashed):
            with patch("src.security.pin_service._set_keyring") as mock_set:
                assert change_pin("wrong", "5678") is False
                mock_set.assert_not_called()

    def test_change_pin_stores_new_hash_on_correct_current(self):
        hashed = _hash("1234")
        stored: dict = {}

        def fake_get(service):
            return hashed

        def fake_set(service, value):
            stored[service] = value

        with patch("src.security.pin_service._get_keyring", side_effect=fake_get):
            with patch("src.security.pin_service._set_keyring", side_effect=fake_set):
                assert change_pin("1234", "5678") is True

        # New stored value must be a bcrypt hash of the new PIN.
        assert "ember-2-pin" in stored
        assert stored["ember-2-pin"].startswith("$2b$")
        assert _verify("5678", stored["ember-2-pin"])
        # And must not be plaintext or match the old PIN's hash.
        assert stored["ember-2-pin"] != "5678"
        assert stored["ember-2-pin"] != hashed

    def test_change_pin_does_not_touch_recovery_passphrase(self):
        """Routine rotation must not modify or require the recovery
        passphrase — that path is for forgotten PINs only."""
        hashed = _hash("1234")
        touched_services: list[str] = []

        def fake_get(service):
            return hashed if service == "ember-2-pin" else None

        def fake_set(service, value):
            touched_services.append(service)

        with patch("src.security.pin_service._get_keyring", side_effect=fake_get):
            with patch("src.security.pin_service._set_keyring", side_effect=fake_set):
                change_pin("1234", "5678")

        assert "ember-2-recovery" not in touched_services
        assert touched_services == ["ember-2-pin"]

    def test_change_pin_preserves_old_hash_on_failure(self):
        """A failed verification must leave the stored hash untouched."""
        hashed = _hash("1234")

        def fake_get(service):
            return hashed

        with patch("src.security.pin_service._get_keyring", side_effect=fake_get):
            with patch("src.security.pin_service._set_keyring") as mock_set:
                change_pin("wrong", "5678")
                mock_set.assert_not_called()


class TestPinChangeEndpoint:
    """Integration tests for POST /v1/security/pin/change. Verify that
    the endpoint enforces the current-PIN check, rate limits failures,
    and returns the documented response shape."""

    def setup_method(self):
        _failed_attempts.clear()

    def test_change_endpoint_success(self):
        hashed = _hash("1234")
        stored: dict = {"ember-2-pin": hashed}

        def fake_get(service):
            return stored.get(service)

        def fake_set(service, value):
            stored[service] = value

        with patch("src.security.pin_service._get_keyring", side_effect=fake_get), \
             patch("src.security.pin_service._set_keyring", side_effect=fake_set), \
             patch("src.api.main.get_ember_api_key", return_value=None):
            from fastapi.testclient import TestClient
            from src.api.main import app
            resp = TestClient(app).post(
                "/v1/security/pin/change",
                json={"current_pin": "1234", "new_pin": "5678"},
            )
        assert resp.status_code == 200
        assert resp.json() == {"status": "changed"}
        assert _verify("5678", stored["ember-2-pin"])

    def test_change_endpoint_rejects_wrong_current_pin(self):
        hashed = _hash("1234")
        with patch("src.security.pin_service._get_keyring", return_value=hashed), \
             patch("src.security.pin_service._set_keyring") as mock_set, \
             patch("src.api.main.get_ember_api_key", return_value=None):
            from fastapi.testclient import TestClient
            from src.api.main import app
            resp = TestClient(app).post(
                "/v1/security/pin/change",
                json={"current_pin": "wrong", "new_pin": "5678"},
            )
        assert resp.status_code == 403
        mock_set.assert_not_called()

    def test_change_endpoint_rejects_short_new_pin(self):
        hashed = _hash("1234")
        with patch("src.security.pin_service._get_keyring", return_value=hashed), \
             patch("src.security.pin_service._set_keyring") as mock_set, \
             patch("src.api.main.get_ember_api_key", return_value=None):
            from fastapi.testclient import TestClient
            from src.api.main import app
            resp = TestClient(app).post(
                "/v1/security/pin/change",
                json={"current_pin": "1234", "new_pin": "12"},
            )
        assert resp.status_code == 400
        mock_set.assert_not_called()

    def test_change_endpoint_does_not_require_recovery_passphrase(self):
        """Body schema must not include recovery_passphrase — routine
        rotation is decoupled from recovery."""
        from src.api.main import PinChangeRequest
        fields = PinChangeRequest.model_fields
        assert set(fields.keys()) == {"current_pin", "new_pin"}

    def test_failed_change_records_attempt_for_rate_limit(self):
        """A failed current-PIN check must record a failed attempt so
        the application-level rate limiter locks out after 5 failures.
        Tests the service-level interaction directly — slowapi provides
        an additional request-count limiter at the decorator level."""
        hashed = _hash("1234")
        with patch("src.security.pin_service._get_keyring", return_value=hashed), \
             patch("src.security.pin_service._set_keyring"), \
             patch("src.api.main.get_ember_api_key", return_value=None):
            from fastapi.testclient import TestClient
            from src.api.main import app
            client = TestClient(app)
            resp = client.post(
                "/v1/security/pin/change",
                json={"current_pin": "wrong", "new_pin": "5678"},
            )
            assert resp.status_code == 403
            # After the failed attempt, remaining_attempts should have
            # decreased by 1 (from 5 to 4 for the test client IP).
            remaining = get_remaining_attempts("testclient")
            assert remaining == 4
