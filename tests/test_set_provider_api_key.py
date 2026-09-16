"""
tests/test_set_provider_api_key.py

Tests for scripts/set_provider_api_key.py (issue #189).

All keyring interaction is mocked -- no test ever touches the real
"ember-2-anthropic"/"ember-2-openai" credential store entries, and every
fixture value is an obviously-synthetic dummy string, never a real
secret shape or a real key.
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from scripts.set_provider_api_key import (
    ALLOWED_PROVIDERS,
    _service_for,
    check_key,
    set_key_interactive,
)


class FakeKeyring:
    """In-memory stand-in for the keyring module's get/set_password."""

    def __init__(self):
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, value):
        self.store[(service, username)] = value


@pytest.fixture
def fake_keyring():
    fk = FakeKeyring()
    with patch("scripts.set_provider_api_key.keyring", fk):
        yield fk


def test_service_naming_matches_get_provider_api_key_and_endpoints():
    """One convention for the keyring service name -- must match
    src/core/config.py::get_provider_api_key and the /provider-key
    endpoints in src/api/main.py exactly."""
    assert _service_for("anthropic") == "ember-2-anthropic"
    assert _service_for("openai") == "ember-2-openai"


def test_allowed_providers_matches_main_py():
    """Must stay identical to main.py's allowed_providers set -- not a
    second, driftable copy."""
    assert ALLOWED_PROVIDERS == {"anthropic", "openai"}


class TestCheckKey:
    def test_check_exits_0_when_configured(self, fake_keyring, capsys):
        fake_keyring.set_password("ember-2-anthropic", "api_key", "sk-ant-DUMMY-TEST-VALUE")
        with pytest.raises(SystemExit) as exc:
            check_key("anthropic")
        assert exc.value.code == 0

    def test_check_exits_1_when_not_configured(self, fake_keyring):
        with pytest.raises(SystemExit) as exc:
            check_key("anthropic")
        assert exc.value.code == 1

    def test_check_never_prints_the_key(self, fake_keyring, capsys):
        fake_keyring.set_password("ember-2-openai", "api_key", "sk-DUMMY-SECRET-SHOULD-NOT-PRINT")
        with pytest.raises(SystemExit):
            check_key("openai")
        captured = capsys.readouterr()
        assert "sk-DUMMY-SECRET-SHOULD-NOT-PRINT" not in captured.out


class TestSetKeyInteractive:
    def test_stores_new_key_when_none_exists(self, fake_keyring):
        with patch("scripts.set_provider_api_key.getpass.getpass", return_value="dummy-new-key-1"):
            set_key_interactive("anthropic")
        assert fake_keyring.get_password("ember-2-anthropic", "api_key") == "dummy-new-key-1"

    def test_rotate_confirmed_overwrites_existing(self, fake_keyring):
        fake_keyring.set_password("ember-2-anthropic", "api_key", "dummy-old-key")
        with patch("builtins.input", return_value="y"), \
             patch("scripts.set_provider_api_key.getpass.getpass", return_value="dummy-new-key-2"):
            set_key_interactive("anthropic")
        assert fake_keyring.get_password("ember-2-anthropic", "api_key") == "dummy-new-key-2"

    def test_rotate_declined_makes_no_change(self, fake_keyring):
        fake_keyring.set_password("ember-2-anthropic", "api_key", "dummy-old-key")
        with patch("builtins.input", return_value="n"), \
             patch("scripts.set_provider_api_key.getpass.getpass") as mock_getpass:
            set_key_interactive("anthropic")
        mock_getpass.assert_not_called()
        assert fake_keyring.get_password("ember-2-anthropic", "api_key") == "dummy-old-key"

    def test_empty_input_makes_no_change(self, fake_keyring):
        with patch("scripts.set_provider_api_key.getpass.getpass", return_value=""):
            set_key_interactive("openai")
        assert fake_keyring.get_password("ember-2-openai", "api_key") is None

    def test_provider_isolated_from_other_provider(self, fake_keyring):
        """Setting anthropic's key must not touch openai's entry."""
        with patch("scripts.set_provider_api_key.getpass.getpass", return_value="dummy-anthropic-key"):
            set_key_interactive("anthropic")
        assert fake_keyring.get_password("ember-2-openai", "api_key") is None


class TestCliRejectsUnknownProvider:
    def test_argparse_rejects_unknown_provider(self):
        import subprocess
        from pathlib import Path

        script = Path(__file__).resolve().parents[1] / "scripts" / "set_provider_api_key.py"
        result = subprocess.run(
            [sys.executable, str(script), "not-a-real-provider", "--check"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode != 0
        assert "invalid choice" in result.stderr
