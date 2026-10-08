"""
src/core/log_redaction.py

Logging filter that keeps exception text out of the persisted application log.

Exception messages can carry vault text (a prompt fragment in a model-server
error, a record excerpt in a parse failure). The file log is long-lived, so
before a record reaches it the exception text is reduced to the exception type
name:

  - exception objects in record.msg / record.args become type(exc).__name__
  - in exc_info output, each "Type: message" line (and any notes) becomes the
    type alone, for every exception in the __cause__ / __context__ chain;
    traceback frames are kept so the failure is still locatable

The filter returns a redacted copy of the record (Python 3.12+ hands a
returned LogRecord on to the handler in place of the original), so other
handlers on the same logger tree still see the original record.

Text that a call site already baked into the message (e.g. "%s" % str(exc))
cannot be recognised here; call sites log type(exc).__name__ for that reason.
"""

from __future__ import annotations

import copy
import logging
import traceback
from collections.abc import Mapping


def _redact_value(value):
    """Exception object -> its type name; anything else unchanged."""
    if isinstance(value, BaseException):
        return type(value).__name__
    return value


def _redact_args(args):
    """Return (redacted_args, changed) for a LogRecord.args value."""
    if isinstance(args, Mapping):
        if any(isinstance(v, BaseException) for v in args.values()):
            return {k: _redact_value(v) for k, v in args.items()}, True
        return args, False
    if isinstance(args, tuple):
        if any(isinstance(v, BaseException) for v in args):
            return tuple(_redact_value(v) for v in args), True
        return args, False
    return args, False


def _qualified_type_name(te: traceback.TracebackException) -> str:
    """The type label Python itself prints on the final traceback line."""
    type_str = getattr(te, "exc_type_str", None)  # Python 3.13+
    if type_str:
        return type_str
    exc_type = te.exc_type
    if exc_type is None:
        return "None"
    module = exc_type.__module__
    qualname = exc_type.__qualname__
    if module in ("__main__", "builtins"):
        return qualname
    return f"{module}.{qualname}"


def _format_redacted_exception(exc_info) -> str:
    """Format exc_info like Formatter.formatException, minus exception text.

    Each TracebackException in the chain gets an instance-level
    format_exception_only that yields only its type line. format() calls that
    method on every link, so frames and chain separators render as usual.
    """
    te = traceback.TracebackException(*exc_info)
    pending = [te]
    seen: set[int] = set()
    while pending:
        node = pending.pop()
        if node is None or id(node) in seen:
            continue
        seen.add(id(node))
        type_line = _qualified_type_name(node) + "\n"
        node.format_exception_only = lambda _line=type_line: iter([_line])
        pending.append(node.__cause__)
        pending.append(node.__context__)
        pending.extend(getattr(node, "exceptions", None) or [])
    text = "".join(te.format())
    return text[:-1] if text.endswith("\n") else text


class ExceptionTextRedactor(logging.Filter):
    """Reduce exception text to the exception type name. See module docstring."""

    def filter(self, record: logging.LogRecord):
        msg_is_exc = isinstance(record.msg, BaseException)
        new_args, args_changed = _redact_args(record.args)
        exc_info = record.exc_info
        has_exc = bool(exc_info) and exc_info[0] is not None

        if not (msg_is_exc or args_changed or has_exc):
            return True

        redacted = copy.copy(record)
        if msg_is_exc:
            redacted.msg = _redact_value(record.msg)
        if args_changed:
            redacted.args = new_args
        if has_exc:
            # Formatter.format uses a pre-set exc_text instead of re-rendering.
            redacted.exc_text = _format_redacted_exception(exc_info)
        return redacted
