"""
tests/test_hook_shell_scan.py

The shell scanner shared by env_guard.py and secret_dump_guard.py
(.claude/hooks/hook_utils.py): quote state with backslash escapes, command
substitution, quoted spans and heredocs. Each guard test file covers the
rules built on it; these pin the scanner itself, every case with a control
that makes the opposite reading occur.
"""

from __future__ import annotations

import pytest

from tests.conftest import load_hook

utils = load_hook("hook_utils.py")


@pytest.mark.parametrize(
    "text, quote, size, after",
    [
        (r"\'x", "", 2, ""),       # an escaped quote is one piece and opens nothing
        (r'\"x', "", 2, ""),
        (r'\"x', '"', 2, '"'),     # ... and does not close a double-quoted span
        (r"\$x", '"', 2, '"'),
        ("'x", "", 1, "'"),        # control: a real quote opens
        ("'x", '"', 1, '"'),       # a single quote inside double quotes is text
        (r"\'x", "'", 1, "'"),     # no escapes inside single quotes
        (r"\nx", "", 1, ""),       # control: a backslash before a letter is a path separator
    ],
)
def test_shell_step(text, quote, size, after):
    assert utils.shell_step(text, 0, quote) == (size, after)


def test_close_paren_skips_quoted_and_nested_parens():
    text = "a (b) 'c)' \"d)\" e) tail"
    assert text[utils.close_paren(text, 0)] == ")"
    assert utils.close_paren(text, 0) == text.index("e)") + 1
    assert utils.close_paren("never closes", 0) == len("never closes")


@pytest.mark.parametrize(
    "text, bodies",
    [
        ("echo $(cat a)", ["cat a"]),
        ('echo "$(cat a)"', ["cat a"]),
        ("echo `cat a`", ["cat a"]),
        ("cat <(cat a)", ["cat a"]),
        ("echo $(echo $(cat a))", ["echo $(cat a)"]),
        ("echo '$(cat a)'", []),         # literal in single quotes
        (r"echo \$(cat a)", []),         # an escaped dollar starts nothing
        ('echo "<(cat a)"', []),         # process substitution is not active in quotes
        ("echo plain", []),
    ],
)
def test_extract_substitutions(text, bodies):
    masked, found = utils.extract_substitutions(text)
    assert found == bodies
    assert ("\x01" in masked) is bool(bodies)


def test_shell_spans_reads_an_escaped_quote_as_part_of_the_run():
    assert utils.shell_spans(r"cat \' .* \'") == [r"cat \' .* \'"]
    # Control: real quotes make a quoted span.
    assert utils.shell_spans("cat ' .* '") == ["cat ", "' .* '"]
    assert utils.shell_spans('a "b \\" c" d') == ["a ", '"b \\" c"', " d"]
    assert utils.shell_spans("it's") == ["it", "'", "s"]  # a lone quote stands alone


HEREDOCS = [
    # (command, body dropped?)
    ("cat <<'EOF'\nbody\nEOF\n", True),
    ("cat > f <<'EOF'\nbody\nEOF\n", True),
    ("tee f <<'EOF'\nbody\nEOF\n", True),
    ("git commit -F - <<'EOF'\nbody\nEOF\n", True),
    ("git commit -F- <<'EOF'\nbody\nEOF\n", True),
    ("gh pr create --body-file=- <<'EOF'\nbody\nEOF\n", True),
    ('git commit -m "$(cat <<\'EOF\'\nbody\nEOF\n)"', True),
    ("VAR=1 cat <<'EOF'\nbody\nEOF\n", True),
    ("bash <<'EOF'\nbody\nEOF\n", False),
    ("python3.12 <<'EOF'\nbody\nEOF\n", False),
    ("cat <<'EOF' | bash\nbody\nEOF\n", False),
    ("git commit <<'EOF'\nbody\nEOF\n", False),               # no -F -
    ("git commit -F msg.txt <<'EOF'\nbody\nEOF\n", False),    # -F names a file, not stdin
    ("cat $(cat <<'EOF'\nbody\nEOF\n)", False),               # words of another command
    ('echo "$(cat <<\'EOF\'\nbody\nEOF\n)"', False),
    ("cat <<'EOF'\nbody\n", False),                           # unterminated: left alone
    ("cat <<<body\nbody\n", False),                           # a here-string has no body
]


@pytest.mark.parametrize("command, dropped", HEREDOCS, ids=[h[0].replace("\n", "|")[:60] for h in HEREDOCS])
def test_drop_inert_heredocs(command, dropped):
    result = utils.drop_inert_heredocs(command)
    assert (result != command) is dropped
    assert ("\nbody\n" not in result) is dropped


def test_a_bare_delimiter_keeps_only_its_substitutions_when_asked():
    command = "cat <<EOF\nplain $(cat a) text\nEOF\n"
    assert utils.drop_inert_heredocs(command) == command  # literal_only: stays as it is
    reduced = utils.drop_inert_heredocs(command, literal_only=False)
    assert "plain" not in reduced
    assert "$(cat a)" in reduced
    # Control: with no substitution nothing is left of the body.
    assert "text" not in utils.drop_inert_heredocs("cat <<EOF\nplain text\nEOF\n", literal_only=False)
