"""
tests/test_hook_env_parser.py

hook_utils.env_file_value reads a key from a dotenv-style file. It uses
python-dotenv when importable and a minimal fallback parser when not. The
vault guards resolve PRIVATE_VAULT_PATH through it, so the fallback must
read every file the way dotenv does (or raise, which the guards turn into a
deny): a different value points the guards at the wrong directory.

Files live in tmp_path only. The fallback is forced by hiding the dotenv
import. Each parity case compares the fallback to dotenv_values on the same
file; test_comparison_detects_a_difference is the positive control showing
that comparison fails when the two disagree.
"""

from __future__ import annotations

import json
import sys

import pytest

from tests.conftest import load_hook

dotenv = pytest.importorskip("dotenv")
hook_utils = load_hook("hook_utils.py")

KEY = "VAULT_KEY"

# (id, file text, expected value)
PARITY = [
    ("plain", f"{KEY}=/data/vault\n", "/data/vault"),
    ("double-quoted", f'{KEY}="/data/vault"\n', "/data/vault"),
    ("single-quoted", f"{KEY}='/data/vault'\n", "/data/vault"),
    ("spaces-around-equals", f"{KEY} = /data/vault\n", "/data/vault"),
    ("empty", f"{KEY}=\n", None),
    ("missing-key", "OTHER=/data/vault\n", None),
    ("bom", f"﻿{KEY}=/data/vault\n", "/data/vault"),
    ("export-prefix", f"export {KEY}=/data/vault\n", "/data/vault"),
    ("inline-comment", f"{KEY}=/data/vault # note\n", "/data/vault"),
    ("comment-after-quoted", f'{KEY}="/data/vault"  # note\n', "/data/vault"),
    ("hash-without-space", f"{KEY}=/data/va#ult\n", "/data/va#ult"),
    ("comment-lines", f"# {KEY}=/wrong\n\n{KEY}=/data/vault\n", "/data/vault"),
    ("escaped-double-quote", f'{KEY}="/data/va\\"ult"\n', '/data/va"ult'),
    ("escaped-newline-in-double", f'{KEY}="/data/a\\nb"\n', "/data/a\nb"),
    ("escaped-single-quote", f"{KEY}='/data/it\\'s'\n", "/data/it's"),
    ("single-quoted-keeps-backslash-n", f"{KEY}='/data/a\\nb'\n", "/data/a\\nb"),
    ("later-line-wins", f"{KEY}=/first\n{KEY}=/second\n", "/second"),
    ("reference-to-earlier-key", f"BASE=/data\n{KEY}=${{BASE}}/vault\n", "/data/vault"),
    ("reference-in-double-quotes", f'BASE=/data\n{KEY}="${{BASE}}/vault"\n', "/data/vault"),
    ("reference-in-single-quotes", f"BASE=/data\n{KEY}='${{BASE}}/vault'\n", "/data/vault"),
    ("reference-default-used", f"{KEY}=${{NOPE_UNSET_X:-/dflt}}/vault\n", "/dflt/vault"),
    ("reference-default-unused", f"BASE=/data\n{KEY}=${{BASE:-/dflt}}/vault\n", "/data/vault"),
    ("reference-unset-is-empty", f"{KEY}=${{NOPE_UNSET_X}}/vault\n", "/vault"),
    ("reference-to-later-key-is-empty", f"{KEY}=${{LATER}}/vault\nLATER=/data\n", "/vault"),
    ("reference-chain", f"A=/a\nB=${{A}}/b\n{KEY}=${{B}}/c\n", "/a/b/c"),
]


def _both(path, monkeypatch) -> tuple:
    """(dotenv result, fallback result) for KEY in the file at `path`."""
    official = (dotenv.dotenv_values(path).get(KEY) or "").strip() or None
    with monkeypatch.context() as patch:
        patch.setitem(sys.modules, "dotenv", None)  # `from dotenv import ...` raises ImportError
        fallback = hook_utils.env_file_value(KEY, str(path))
    return official, fallback


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    monkeypatch.delenv("NOPE_UNSET_X", raising=False)
    monkeypatch.delenv("LATER", raising=False)
    monkeypatch.delenv(KEY, raising=False)


@pytest.mark.parametrize("text, expected", [case[1:] for case in PARITY], ids=[case[0] for case in PARITY])
def test_fallback_reads_the_file_as_dotenv_does(tmp_path, monkeypatch, text, expected):
    path = tmp_path / "probe.env"
    path.write_text(text, encoding="utf-8")
    official, fallback = _both(path, monkeypatch)
    assert official == expected
    assert fallback == official


def test_fallback_reference_to_a_process_variable(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_HOME_DIR", "/home/probe")
    path = tmp_path / "probe.env"
    path.write_text(f"{KEY}=${{PROBE_HOME_DIR}}/vault\n", encoding="utf-8")
    official, fallback = _both(path, monkeypatch)
    assert official == fallback == "/home/probe/vault"


def test_file_value_beats_process_variable_in_a_reference(tmp_path, monkeypatch):
    """dotenv_values lets the file's own keys override the environment."""
    monkeypatch.setenv("BASE", "/from-process")
    path = tmp_path / "probe.env"
    path.write_text(f"BASE=/from-file\n{KEY}=${{BASE}}/vault\n", encoding="utf-8")
    official, fallback = _both(path, monkeypatch)
    assert official == fallback == "/from-file/vault"


def test_comparison_detects_a_difference(tmp_path, monkeypatch):
    """Positive control: the old fallback (strip quotes, no references, no
    export) disagrees with dotenv on a reference, so the parity assertion
    above would fail against it."""
    path = tmp_path / "probe.env"
    path.write_text(f"BASE=/data\n{KEY}=${{BASE}}/vault\n", encoding="utf-8")

    def old_fallback() -> str | None:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, val = line.partition("=")
            if name.strip() == KEY:
                return val.strip().strip('"').strip("'") or None
        return None

    official, _ = _both(path, monkeypatch)
    assert old_fallback() == "${BASE}/vault"
    assert old_fallback() != official


@pytest.mark.parametrize(
    "text",
    [
        f"{KEY}=${{A:-${{B}}}}/vault\n",   # nested default: dotenv leaves residue, the fallback refuses
        f'{KEY}="/data/vault\n',            # unclosed quote
        f'{KEY}="/data/vault" trailing\n',  # text after the closing quote
    ],
    ids=["nested-default", "unclosed-quote", "text-after-quote"],
)
def test_fallback_raises_when_it_cannot_read_a_line_like_dotenv(tmp_path, monkeypatch, text):
    path = tmp_path / "probe.env"
    path.write_text(text, encoding="utf-8")
    with monkeypatch.context() as patch:
        patch.setitem(sys.modules, "dotenv", None)
        with pytest.raises(ValueError):
            hook_utils.env_file_value(KEY, str(path))


def test_fallback_ignores_an_unreadable_line_for_another_key(tmp_path, monkeypatch):
    """Control for the raise cases: a line the fallback can read parses, so
    the raise is about the line, not the file."""
    path = tmp_path / "probe.env"
    path.write_text(f"{KEY}=/data/vault\nOTHER=ok\n", encoding="utf-8")
    _, fallback = _both(path, monkeypatch)
    assert fallback == "/data/vault"


def test_missing_file_is_none_on_both_paths(tmp_path, monkeypatch):
    official, fallback = _both(tmp_path / "absent.env", monkeypatch)
    assert official is None
    assert fallback is None


def test_vault_guard_denies_when_the_fallback_cannot_read_the_file(hermetic_hook_project, monkeypatch, capsys):
    """The consumer: a PRIVATE_VAULT_PATH line the fallback cannot read the
    way dotenv does makes vault_guard raise, and its entry point turns that
    into a deny rather than guessing a directory. The tmp project has no
    .claude directory, so no cache file hides the second parse."""
    import io

    vault_guard = load_hook("vault_guard.py")
    (hermetic_hook_project / ".env").write_text('PRIVATE_VAULT_PATH="/data/vault\n', encoding="utf-8")
    payload = json.dumps({"tool_name": "Read", "tool_input": {"file_path": "src/a.py"}})

    # Control: with python-dotenv the same file reads without a raise, no deny.
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    vault_guard.main()
    assert capsys.readouterr().out.strip() == ""

    # Fallback only: the unclosed quote raises, and the guard denies.
    monkeypatch.setitem(sys.modules, "dotenv", None)
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    vault_guard.main()
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecision"] == "deny"
