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
import os
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
    ("bom", f"\ufeff{KEY}=/data/vault\n", "/data/vault"),
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


def _hide_dotenv(patch) -> None:
    """Make `from dotenv... import ...` raise ImportError. The submodule entry
    too: an already imported dotenv.main would otherwise still resolve."""
    patch.setitem(sys.modules, "dotenv", None)
    patch.setitem(sys.modules, "dotenv.main", None)


def _app_value(path) -> str | None:
    """KEY as the app loads it: src/core/config.py calls load_dotenv(path)
    with the default override=False, so the process environment wins."""
    before = dict(os.environ)
    os.environ.pop(KEY, None)
    try:
        dotenv.load_dotenv(str(path))
        return (os.environ.get(KEY) or "").strip() or None
    finally:  # load_dotenv sets every key of the file: put the environment back
        os.environ.clear()
        os.environ.update(before)


def _both(path, monkeypatch) -> tuple:
    """(the app's result, fallback result) for KEY in the file at `path`."""
    official = _app_value(path)
    with monkeypatch.context() as patch:
        _hide_dotenv(patch)
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


def test_process_variable_beats_file_value_in_a_reference(tmp_path, monkeypatch):
    """The app loads the file with load_dotenv's default override=False, so a
    `${BASE}` resolves from the process environment before the file's own
    BASE. Both hook paths (python-dotenv and the fallback) must agree with
    the app, with the two BASE values different."""
    monkeypatch.setenv("BASE", "/from-process")
    path = tmp_path / "probe.env"
    path.write_text(f"BASE=/from-file\n{KEY}=${{BASE}}/vault\n", encoding="utf-8")
    official, fallback = _both(path, monkeypatch)
    assert official == "/from-process/vault"
    assert fallback == official
    assert hook_utils.env_file_value(KEY, str(path)) == official  # python-dotenv path


def test_the_old_precedence_differs_from_the_app(tmp_path, monkeypatch):
    """Positive control: dotenv_values (override=True, what the hooks used)
    resolves the same file to the file's BASE, so the test above fails
    against it."""
    monkeypatch.setenv("BASE", "/from-process")
    path = tmp_path / "probe.env"
    path.write_text(f"BASE=/from-file\n{KEY}=${{BASE}}/vault\n", encoding="utf-8")
    assert dotenv.dotenv_values(path).get(KEY) == "/from-file/vault"
    assert dotenv.dotenv_values(path).get(KEY) != _app_value(path)


def test_the_reference_resolves_from_the_file_when_the_process_has_no_such_variable(tmp_path, monkeypatch):
    """Control: without a process BASE the file's BASE is used on every path."""
    monkeypatch.delenv("BASE", raising=False)
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
        _hide_dotenv(patch)
        with pytest.raises(ValueError):
            hook_utils.env_file_value(KEY, str(path))


def test_the_bom_case_really_starts_with_a_bom():
    """The BOM is written as an escape; an editor that strips an invisible
    character would otherwise turn the case into a plain-file test."""
    text = next(case[1] for case in PARITY if case[0] == "bom")
    assert text.startswith("\ufeff")


# A line the fallback cannot read, for a key nobody asked about.
UNRELATED_UNREADABLE = [
    'OTHER="unclosed\n{key}=/data/vault\n',
    "OTHER='/a'b\n{key}=/data/vault\n",
    'OTHER="a\nb"\n{key}=/data/vault\n',
    "OTHER=${{NOPE_UNSET_X:-${{NOPE_UNSET_Y}}}}\n{key}=/data/vault\n",
]


@pytest.mark.parametrize("text", UNRELATED_UNREADABLE, ids=["unclosed", "text-after-quote", "multi-line", "nested-default"])
def test_fallback_skips_an_unreadable_line_of_another_key(tmp_path, monkeypatch, text):
    path = tmp_path / "probe.env"
    path.write_text(text.format(key=KEY), encoding="utf-8")
    official, fallback = _both(path, monkeypatch)
    assert official == "/data/vault"
    assert fallback == official


@pytest.mark.parametrize(
    "text",
    [
        '{key}="/data/vault\n',  # the key's own line
        "OTHER='/a'b\n{key}=${{OTHER}}/vault\n",  # the key's value needs the unreadable one
        "{key}='/a'b\n",
    ],
    ids=["own-line", "references-unreadable", "own-line-text-after-quote"],
)
def test_fallback_raises_when_the_requested_key_is_affected(tmp_path, monkeypatch, text):
    """Control for the skip cases above: the same kind of line still raises
    when it is the requested key's, or one its value reads."""
    path = tmp_path / "probe.env"
    path.write_text(text.format(key=KEY), encoding="utf-8")
    with monkeypatch.context() as patch:
        _hide_dotenv(patch)
        with pytest.raises(ValueError):
            hook_utils.env_file_value(KEY, str(path))


def test_a_later_good_line_replaces_an_unreadable_one(tmp_path, monkeypatch):
    path = tmp_path / "probe.env"
    path.write_text(f"{KEY}='/a'b\n{KEY}=/data/vault\n", encoding="utf-8")
    official, fallback = _both(path, monkeypatch)
    assert official == "/data/vault"
    assert fallback == official


def test_strict_read_raises_when_the_file_cannot_be_opened(tmp_path, monkeypatch):
    """`strict` lets a caller that caches the answer tell a failed read from
    an absent key. Control: the default still reads it as no key."""
    path = tmp_path / "probe.env"
    path.write_text(f"{KEY}=/data/vault\n", encoding="utf-8")
    real_open = open

    def locked(file, *args, **kwargs):
        if str(file) == str(path):
            raise PermissionError("locked")
        return real_open(file, *args, **kwargs)

    with monkeypatch.context() as patch:
        _hide_dotenv(patch)
        patch.setattr("builtins.open", locked)
        with pytest.raises(PermissionError):
            hook_utils.env_file_value(KEY, str(path), strict=True)
        assert hook_utils.env_file_value(KEY, str(path)) is None
    assert hook_utils.env_file_value(KEY, str(path), strict=True) == "/data/vault"


def test_references_digest_hides_values_and_follows_them(tmp_path, monkeypatch):
    path = tmp_path / "probe.env"
    path.write_text("A=${PROBE_REF_ONE}/x\nB=plain\n", encoding="utf-8")
    monkeypatch.setenv("PROBE_REF_ONE", "first-value")
    first = hook_utils.env_references(str(path))
    assert len(first) == 64 and "first-value" not in first
    monkeypatch.setenv("PROBE_REF_ONE", "second-value")
    assert hook_utils.env_references(str(path)) != first
    # Control: a file with no reference has no digest, whatever the environment.
    plain = tmp_path / "plain.env"
    plain.write_text("A=1\n", encoding="utf-8")
    assert hook_utils.env_references(str(plain)) == ""


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
    with monkeypatch.context() as patch:
        _hide_dotenv(patch)
        patch.setattr(sys, "stdin", io.StringIO(payload))
        vault_guard.main()
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecision"] == "deny"
