"""Bounded matching of user-supplied regexes against server-supplied text.

Scenario assertions (``assert_body_regex``, the ``assert_header`` regex form)
and ``extract`` rules of source ``regex`` match a pattern the operator wrote
against a response the server under test chose. Python's ``re`` has no
per-match timeout and the match runs on the event loop, so an unbounded scan
lets the target stall the worker (#306, #314). Both paths go through
:func:`bounded_search`: at most :data:`SCAN_MAX` characters are searched, and
when the text was longer and the answer could depend on where it was cut, the
result is :data:`UNDECIDED` rather than a guess. :func:`has_nested_repetition`
backs a load-time warning for the ``(a+)+`` shape the cap cannot help with.
"""

from __future__ import annotations

import functools
import re
import sys
from typing import Any, Final, Iterator

if sys.version_info >= (3, 11):
    import re._parser as _sre_parse
else:  # pragma: no cover - Python 3.10
    import sre_parse as _sre_parse

__all__ = [
    "SCAN_MAX",
    "UNDECIDED",
    "Undecided",
    "bounded_search",
    "cut_sensitivity",
    "has_nested_repetition",
]

#: Cap, in characters, on the server-supplied text a user regex may scan. Even
#: a common pattern such as ``\s+$`` is quadratic, so the cap is what bounds
#: it. Same bound as ``har_import._MATCH_URL_MAX`` on the URL path.
SCAN_MAX: Final = 8192


class Undecided:
    """The text was cut and the pattern's answer could depend on where."""

    _instance: "Undecided | None" = None

    def __new__(cls) -> "Undecided":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNDECIDED"

    def __bool__(self) -> bool:
        raise TypeError("UNDECIDED has no truth value; compare with `is UNDECIDED`")


#: Returned by :func:`bounded_search` when the cut text cannot decide.
UNDECIDED: Final = Undecided()


def subpatterns(value: object) -> Iterator[Any]:
    """Every parsed sub-pattern nested anywhere inside one opcode argument."""
    if isinstance(value, _sre_parse.SubPattern):
        yield value
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from subpatterns(item)


def has_nested_repetition(pattern: str) -> bool:
    r"""Whether a regex nests an unbounded repeat inside a repeat, like ``(a+)+``.

    That shape is what drives catastrophic backtracking: the outer repeat must
    repeat more than once (``+``, ``*``, or ``{n}`` with n >= 2, as in ``(.*,){3}``) and the inner
    one unbounded. Bounded shapes such as ``(?:\d{1,3}\.){3}`` are not
    flagged, nor is a repeat of an atomic group or possessive repeat, which is
    never re-split (``(?>a+)+``, ``(a++)+``); a nested pair *inside* one still
    is. It is a heuristic for a warning, not a proof of safety: overlapping
    alternation such as ``(a|ab)*`` and adjacent repeats such as ``\s*\s*$``
    are not detected.
    """
    repeat_ops = {_sre_parse.MAX_REPEAT, _sre_parse.MIN_REPEAT}
    no_backtrack_ops = {
        getattr(_sre_parse, name)
        for name in ("ATOMIC_GROUP", "POSSESSIVE_REPEAT")
        if hasattr(_sre_parse, name)
    }

    def walk(items: Any, outer_repeats: bool) -> bool:
        for op, av in items:
            if op in no_backtrack_ops:
                # Once an atomic group or possessive repeat is left it is never
                # re-entered to try another split, so it ends the outer
                # context -- but backtracking still happens inside it, so a
                # nested pair within it, as in (?>(a+)+b), still counts.
                for child in subpatterns(av):
                    if walk(child, False):
                        return True
                continue
            if op in repeat_ops:
                _lo, hi, sub = av
                unbounded = hi == _sre_parse.MAXREPEAT
                if unbounded and outer_repeats:
                    return True
                if walk(sub, outer_repeats or unbounded or hi >= 2):
                    return True
            else:
                for child in subpatterns(av):
                    if walk(child, outer_repeats):
                        return True
        return False

    try:
        return walk(_sre_parse.parse(pattern), False)
    except Exception:  # pragma: no cover - parser internals vary by version
        return False


@functools.lru_cache(maxsize=256)
def cut_sensitivity(pattern: str) -> tuple[bool, bool]:
    r"""How a pattern's result can change when the text is cut short.

    Returns:
        ``(lookahead_dependent, end_dependent)``.

        *lookahead_dependent*: the answer on the cut text says nothing about
        the whole one, wherever the match ends. A lookahead can read past the
        match up to the cut -- a negative one (``(?!.*ERROR)``), or a positive
        one testing the end or a word boundary (``(?=[^E]*\Z)``). A
        backreference, conditional, atomic group or possessive repeat can
        settle on a different reading of the cut text. So can any lookahead
        inside a lookbehind, which may read past the match -- and inside a
        negative lookbehind its polarity flips. A plain lookbehind reads only
        text before the match and does not count.

        *end_dependent*: an end anchor or word boundary (``$``, ``\Z``, ``\b``)
        outside such a lookahead can match at the cut, where the whole text
        continues.
    """
    end_codes = {
        getattr(_sre_parse, name)
        for name in (
            "AT_END",
            "AT_END_LINE",
            "AT_END_STRING",
            "AT_BOUNDARY",
            "AT_NON_BOUNDARY",
            "AT_UNI_BOUNDARY",
            "AT_UNI_NON_BOUNDARY",
            "AT_LOC_BOUNDARY",
            "AT_LOC_NON_BOUNDARY",
        )
        if hasattr(_sre_parse, name)
    }
    lookahead_dependent = False

    def has_end(items: Any) -> bool:
        for op, av in items:
            if op == _sre_parse.AT and av in end_codes:
                return True
            if any(has_end(child) for child in subpatterns(av)):
                return True
        return False

    # Constructs that settle on what they first read and never give it back
    # (atomic groups, possessive repeats), or that test what an earlier part
    # captured (backreferences, conditionals). Cut short, they can settle on
    # a different reading and so reach a different answer than on the whole
    # text, wherever the match ends.
    committing = {
        getattr(_sre_parse, name)
        for name in ("GROUPREF", "GROUPREF_EXISTS", "ATOMIC_GROUP", "POSSESSIVE_REPEAT")
        if hasattr(_sre_parse, name)
    }

    def walk(items: Any, in_lookbehind: bool) -> None:
        nonlocal lookahead_dependent
        for op, av in items:
            if op in committing:
                lookahead_dependent = True
            if op in (_sre_parse.ASSERT, _sre_parse.ASSERT_NOT):
                direction, sub = av
                if direction == 1 and (
                    op == _sre_parse.ASSERT_NOT or has_end(sub) or in_lookbehind
                ):
                    lookahead_dependent = True
                if direction == -1:
                    for child in subpatterns(av):
                        walk(child, True)
                    continue
            for child in subpatterns(av):
                walk(child, in_lookbehind)

    try:
        parsed = _sre_parse.parse(pattern)
        walk(parsed, False)
        return lookahead_dependent, has_end(parsed)
    except Exception:  # pragma: no cover - parser internals vary by version
        return True, True


def bounded_search(
    pattern: "re.Pattern[str]", text: str, *, whole_match: bool = False
) -> "re.Match[str] | None | Undecided":
    """Search at most :data:`SCAN_MAX` characters of server-supplied text.

    Args:
        pattern: The compiled user pattern.
        text: The server-supplied text.
        whole_match: The caller uses the matched text (an extract rule reads
            a capture group), not just whether there is a match. A match that
            runs up to the cut may then be a truncated value, so it is
            undecided too.

    Returns:
        The match, or None, when the answer is the same as for the whole text
        within the scanned window; :data:`UNDECIDED` when the text was cut and
        the answer could depend on where (see :func:`cut_sensitivity`). A match
        that starts past the window is not found.
    """
    if len(text) <= SCAN_MAX:
        return pattern.search(text)
    lookahead_dependent, end_dependent = cut_sensitivity(pattern.pattern)
    if lookahead_dependent:
        return UNDECIDED
    prefix = text[:SCAN_MAX]
    match = pattern.search(prefix)
    if match is None:
        return None
    # "At the cut" includes one character short of it: a plain $ also matches
    # just before a final newline, which the cut text may end with.
    if end_dependent and match.end() >= len(prefix) - 1:
        return UNDECIDED
    if whole_match and match.end() >= len(prefix):
        return UNDECIDED
    return match
