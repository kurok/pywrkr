"""Per-step response assertions for scenarios.

``assert_status`` alone lets a load test pass while the API returns
well-formed garbage — a classic false green. These rules check the things that
actually say the response was correct: payload shape and values, headers, body
patterns, and a per-request latency bound.

Everything is compiled when the scenario file loads, so a bad regex, an
unsupported JSONPath, or a nonsense duration is a startup error naming the
step rather than a failure repeated once per request. The JSONPath subset is
the one :mod:`pywrkr.templating` already implements, so extraction and
assertions never disagree about what ``$.a.b[0]`` means.
"""

from __future__ import annotations

import functools
import json
import logging
import re
import sys
from dataclasses import dataclass
from typing import Any, Iterator, Mapping

if sys.version_info >= (3, 11):
    import re._parser as _sre_parse
else:  # pragma: no cover - Python 3.10
    import sre_parse as _sre_parse

from pywrkr.templating import ExtractError, parse_json_path, resolve_json_path, stringify

__all__ = [
    "ANY_VALUE",
    "HeaderAssertion",
    "JsonAssertion",
    "StepAssertions",
    "AssertionFailure",
    "evaluate_assertions",
    "parse_duration",
    "parse_step_assertions",
]

#: ``assert_json`` value meaning "this path must exist, any value will do".
ANY_VALUE = "*"

logger = logging.getLogger(__name__)

#: Cap, in characters, on the response text a user-supplied assertion regex
#: may scan. The body and headers come from the server under test, ``re`` has
#: no per-match timeout, and the match runs on the event loop, so an unbounded
#: scan lets the target stall the worker: even a common pattern such as
#: ``\s+$`` is quadratic. Same bound as ``har_import._MATCH_URL_MAX`` on the URL
#: path. Large JSON is better checked with ``assert_json``.
_ASSERT_MATCH_MAX = 8192

_BACKTRACKING_WARNING = (
    "%s %s: pattern %r repeats an unbounded repeat. Patterns with "
    "catastrophic backtracking (e.g. '(a+)+') matched against a server's "
    "response can hang the worker, since the server chooses the text; only the "
    "first %d characters are searched. Prefer a pattern without nested "
    "repetition, or assert_body_contains / assert_json."
)

_DURATION_PATTERN = re.compile(r"\A\s*([0-9]*\.?[0-9]+)\s*(ms|us|s)?\s*\Z")


def parse_duration(raw: object, where: str) -> float:
    """Parse ``500ms`` / ``1.5s`` / ``250us`` into seconds.

    A bare number is read as seconds, matching ``--threshold``, but warns
    through the caller's error message if it looks like a mistake is likely.

    Raises:
        ValueError: The value is not a positive duration.
    """
    if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
        raise ValueError(f"{where} must be a duration like '500ms', got {type(raw).__name__}")
    if isinstance(raw, (int, float)):
        value, unit = float(raw), "s"
    else:
        match = _DURATION_PATTERN.match(raw)
        if not match:
            raise ValueError(
                f"{where} must be a duration like '500ms', '1.5s' or '250us', got {raw!r}"
            )
        value, unit = float(match.group(1)), (match.group(2) or "s")
    if value <= 0:
        raise ValueError(f"{where} must be greater than zero, got {raw!r}")
    if unit == "ms":
        return value / 1000.0
    if unit == "us":
        return value / 1_000_000.0
    return value


@dataclass(frozen=True)
class JsonAssertion:
    """One ``assert_json`` rule: a path plus what is expected there."""

    expr: str
    path: tuple
    expected: Any  # ANY_VALUE, or the value the path must equal


@dataclass(frozen=True)
class HeaderAssertion:
    """One ``assert_header`` rule: exact match, or a regex."""

    name: str
    expected: "str | None" = None
    pattern: "re.Pattern[str] | None" = None

    def describe(self) -> str:
        return f"~ {self.pattern.pattern}" if self.pattern else f"== {self.expected!r}"


@dataclass
class StepAssertions:
    """Every assertion attached to one scenario step."""

    status: "int | None" = None
    body_contains: "str | None" = None
    body_regex: "re.Pattern[str] | None" = None
    json_rules: tuple[JsonAssertion, ...] = ()
    header_rules: tuple[HeaderAssertion, ...] = ()
    max_latency: "float | None" = None

    @property
    def needs_body(self) -> bool:
        """True when a rule has to look at the response body."""
        return bool(self.body_contains or self.body_regex or self.json_rules)

    @property
    def any(self) -> bool:
        """True when the step asserts anything at all."""
        return bool(
            self.status is not None
            or self.needs_body
            or self.header_rules
            or self.max_latency is not None
        )


# ---------------------------------------------------------------------------
# Parsing / validation
# ---------------------------------------------------------------------------


def _subpatterns(value: object) -> Iterator[Any]:
    """Every parsed sub-pattern nested anywhere inside one opcode argument."""
    if isinstance(value, _sre_parse.SubPattern):
        yield value
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from _subpatterns(item)


def _has_nested_repetition(pattern: str) -> bool:
    r"""Whether a regex nests an unbounded repeat inside a repeat, like ``(a+)+``.

    That shape is what drives catastrophic backtracking: the outer repeat must
    be unbounded or large (``{10,}`` and up, as in ``(.*a){20}``) and the inner
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
                for child in _subpatterns(av):
                    if walk(child, False):
                        return True
                continue
            if op in repeat_ops:
                _lo, hi, sub = av
                unbounded = hi == _sre_parse.MAXREPEAT
                if unbounded and outer_repeats:
                    return True
                if walk(sub, outer_repeats or unbounded or hi >= 10):
                    return True
            else:
                for child in _subpatterns(av):
                    if walk(child, outer_repeats):
                        return True
        return False

    try:
        return walk(_sre_parse.parse(pattern), False)
    except Exception:  # pragma: no cover - parser internals vary by version
        return False


@functools.lru_cache(maxsize=256)
def _cut_sensitivity(pattern: str) -> tuple[bool, bool]:
    r"""How a pattern's result can change when the text is cut short.

    Returns:
        ``(lookahead_dependent, end_dependent)``.

        *lookahead_dependent*: a lookahead can read past the match up to the
        cut, so its answer on the cut text says nothing about the whole one --
        a negative lookahead (``(?!.*ERROR)``), or a positive one that tests
        for the end or a word boundary (``(?=[^E]*\Z)``). Lookbehinds read
        only text before the match and do not count.

        *end_dependent*: an end anchor or word boundary (``$``, ``\Z``, ````)
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
            if any(has_end(child) for child in _subpatterns(av)):
                return True
        return False

    def walk(items: Any) -> None:
        nonlocal lookahead_dependent
        for op, av in items:
            if op in (_sre_parse.ASSERT, _sre_parse.ASSERT_NOT):
                direction, sub = av
                if direction == 1 and (op == _sre_parse.ASSERT_NOT or has_end(sub)):
                    lookahead_dependent = True
            for child in _subpatterns(av):
                walk(child)

    try:
        parsed = _sre_parse.parse(pattern)
        walk(parsed)
        return lookahead_dependent, has_end(parsed)
    except Exception:  # pragma: no cover - parser internals vary by version
        return True, True


def _bounded_search(pattern: "re.Pattern[str]", text: str) -> "bool | None":
    """Search at most ``_ASSERT_MATCH_MAX`` characters of server-supplied text.

    Returns:
        True or False when the answer is the same as for the whole text, or
        None when the text was cut and the pattern's result could depend on
        where it ends (see :func:`_cut_sensitivity`).
    """
    if len(text) <= _ASSERT_MATCH_MAX:
        return pattern.search(text) is not None
    lookahead_dependent, end_dependent = _cut_sensitivity(pattern.pattern)
    if lookahead_dependent:
        return None
    prefix = text[:_ASSERT_MATCH_MAX]
    match = pattern.search(prefix)
    if match is None:
        return False
    # "At the cut" includes one character short of it: a plain $ also matches
    # just before a final newline, which the cut text may end with.
    if end_dependent and match.end() >= len(prefix) - 1:
        return None
    return True


def _compile_user_regex(pattern: str, where: str, field: str) -> "re.Pattern[str]":
    """Compile an assertion regex, warning when it is prone to backtracking."""
    compiled = re.compile(pattern)
    if _has_nested_repetition(pattern):
        logger.warning(_BACKTRACKING_WARNING, where, field, pattern, _ASSERT_MATCH_MAX)
    return compiled


def _parse_json_rules(raw: object, where: str) -> tuple[JsonAssertion, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise ValueError(f"{where} 'assert_json' must be an object, got {type(raw).__name__}")
    rules = []
    for expr, expected in raw.items():
        if not isinstance(expr, str):
            raise ValueError(f"{where} 'assert_json' keys must be JSONPath strings")
        if isinstance(expected, (dict, list)):
            raise ValueError(
                f"{where} assert_json {expr!r}: expected value must be a scalar or "
                f'"{ANY_VALUE}", got {type(expected).__name__}'
            )
        try:
            path = parse_json_path(expr)
        except ValueError as exc:
            raise ValueError(f"{where} assert_json {expr!r}: {exc}") from None
        rules.append(JsonAssertion(expr=expr, path=path, expected=expected))
    return tuple(rules)


def _parse_header_rules(raw: object, where: str) -> tuple[HeaderAssertion, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise ValueError(f"{where} 'assert_header' must be an object, got {type(raw).__name__}")
    rules = []
    for name, expected in raw.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{where} 'assert_header' keys must be header names")
        if isinstance(expected, dict):
            unknown = [k for k in expected if k != "regex"]
            if unknown or "regex" not in expected:
                raise ValueError(
                    f"{where} assert_header {name!r}: an object form must be "
                    f'{{"regex": "..."}}, got keys {sorted(expected)}'
                )
            pattern_text = expected["regex"]
            if not isinstance(pattern_text, str):
                raise ValueError(f"{where} assert_header {name!r}: 'regex' must be a string")
            try:
                pattern = _compile_user_regex(pattern_text, where, f"assert_header {name!r}")
            except re.error as exc:
                raise ValueError(
                    f"{where} assert_header {name!r}: invalid regex {pattern_text!r}: {exc}"
                ) from None
            rules.append(HeaderAssertion(name=name, pattern=pattern))
            continue
        if isinstance(expected, (list, bool)) or expected is None:
            raise ValueError(
                f"{where} assert_header {name!r}: expected a string or "
                f'{{"regex": "..."}}, got {type(expected).__name__}'
            )
        rules.append(HeaderAssertion(name=name, expected=str(expected)))
    return tuple(rules)


def parse_step_assertions(step_data: Mapping[str, Any], where: str) -> StepAssertions:
    """Compile every assertion on one scenario step.

    Raises:
        ValueError: Any rule is malformed. Reported here, at load time.
    """
    status = step_data.get("assert_status")
    if status is not None and (isinstance(status, bool) or not isinstance(status, int)):
        raise ValueError(f"{where} 'assert_status' must be an integer, got {type(status).__name__}")

    contains = step_data.get("assert_body_contains")
    if contains is not None and not isinstance(contains, str):
        raise ValueError(
            f"{where} 'assert_body_contains' must be a string, got {type(contains).__name__}"
        )

    body_regex = None
    raw_regex = step_data.get("assert_body_regex")
    if raw_regex is not None:
        if not isinstance(raw_regex, str):
            raise ValueError(
                f"{where} 'assert_body_regex' must be a string, got {type(raw_regex).__name__}"
            )
        try:
            body_regex = _compile_user_regex(raw_regex, where, "'assert_body_regex'")
        except re.error as exc:
            raise ValueError(
                f"{where} 'assert_body_regex': invalid regex {raw_regex!r}: {exc}"
            ) from None

    max_latency = None
    if step_data.get("assert_max_latency") is not None:
        max_latency = parse_duration(
            step_data["assert_max_latency"], f"{where} 'assert_max_latency'"
        )

    return StepAssertions(
        status=status,
        body_contains=contains,
        body_regex=body_regex,
        json_rules=_parse_json_rules(step_data.get("assert_json"), where),
        header_rules=_parse_header_rules(step_data.get("assert_header"), where),
        max_latency=max_latency,
    )


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def _json_matches(actual: Any, expected: Any) -> bool:
    """Compare an extracted JSON value against the expected one.

    Numbers compare numerically (``42`` matches ``42.0``); everything else
    falls back to comparing JSON spellings, so ``"42"`` in the file also
    matches a numeric ``42`` in the payload rather than failing on a type the
    user could not see.
    """
    # In Python ``True == 1``, so a boolean payload value would silently
    # satisfy an expected 1 (and vice versa). JSON treats them as different
    # types and so should the assertion.
    #
    # Not when either side is a string, though. The spelling fallback below is
    # the documented way to write a value YAML would otherwise mangle -- users
    # quote "true" precisely to stop it becoming something else -- and this
    # guard ran first, so a quoted "true" could never match a boolean payload.
    # The rule then failed on every single request with
    # `AssertJson: $.ok != 'true' (was True)`, which reads like the payload is
    # wrong rather than the comparison.
    either_is_str = isinstance(actual, str) or isinstance(expected, str)
    if isinstance(actual, bool) != isinstance(expected, bool) and not either_is_str:
        return False
    if actual == expected:
        return True
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return float(actual) == float(expected)
    return stringify(actual) == stringify(expected)


@dataclass(frozen=True)
class AssertionFailure:
    """One broken rule: a stable key for aggregation, plus the observed detail.

    The key is derived only from the rule, never from what came back. Folding an
    observed latency or payload value into it would mint a fresh key on every
    request and blow through the error-breakdown's key cap within seconds,
    turning the one view that should aggregate into noise. The observation goes
    in *detail*, which is logged rather than counted.
    """

    key: str
    detail: str = ""

    @property
    def message(self) -> str:
        return f"{self.key} ({self.detail})" if self.detail else self.key


def evaluate_assertions(
    rules: StepAssertions,
    status: int,
    body: "bytes | None",
    headers: "Mapping[str, str] | None",
    latency: float,
) -> list[AssertionFailure]:
    """Check one response, returning one entry per failed assertion."""
    failures: list[AssertionFailure] = []

    if rules.status is not None and status != rules.status:
        # Status codes are a small bounded set, so the observed one is safe in
        # the key and genuinely useful there.
        failures.append(AssertionFailure(f"AssertStatus: expected {rules.status}, got {status}"))

    if rules.max_latency is not None and latency > rules.max_latency:
        failures.append(
            AssertionFailure(
                f"AssertMaxLatency: over {rules.max_latency * 1000:.1f}ms",
                f"took {latency * 1000:.1f}ms",
            )
        )

    for header_rule in rules.header_rules:
        value = headers.get(header_rule.name) if headers is not None else None
        if value is None:
            failures.append(AssertionFailure(f"AssertHeader: {header_rule.name} missing"))
            continue
        found = (
            _bounded_search(header_rule.pattern, value)
            if header_rule.pattern
            else value == header_rule.expected
        )
        if found is None:
            failures.append(
                AssertionFailure(
                    f"AssertHeader: {header_rule.name} {header_rule.describe()}"
                    f" (longer than the {_ASSERT_MATCH_MAX}-char scan limit)"
                )
            )
        elif not found:
            failures.append(
                AssertionFailure(
                    f"AssertHeader: {header_rule.name} {header_rule.describe()}",
                    f"was {value!r}",
                )
            )

    if not rules.needs_body:
        return failures

    text: "str | None" = None
    if body is not None:
        if (
            rules.body_contains is None
            and not rules.json_rules
            and len(body) > 4 * (_ASSERT_MATCH_MAX + 1)
        ):
            # Only the regex looks at the body and it scans a bounded prefix,
            # so decoding a large body in full on the event loop is waste. A
            # UTF-8 character is at most 4 bytes, so this prefix still decodes
            # to more than the scan limit and the cut is still detected.
            body = body[: 4 * (_ASSERT_MATCH_MAX + 1)]
        text = body.decode("utf-8", errors="replace")

    if rules.body_contains is not None:
        if text is None or rules.body_contains not in text:
            failures.append(AssertionFailure(f"AssertBody: '{rules.body_contains}' not found"))

    if rules.body_regex is not None:
        found = None if text is None else _bounded_search(rules.body_regex, text)
        if text is not None and found is None:
            failures.append(
                AssertionFailure(
                    f"AssertBodyRegex: {rules.body_regex.pattern!r} undecided: the body is "
                    f"longer than the {_ASSERT_MATCH_MAX}-char scan limit"
                )
            )
        elif not found:
            failures.append(
                AssertionFailure(f"AssertBodyRegex: {rules.body_regex.pattern!r} did not match")
            )

    if rules.json_rules:
        document: Any = None
        parse_error: "str | None" = None
        if text is None:
            parse_error = "response body was not captured"
        else:
            try:
                document = json.loads(text)
            except ValueError:
                parse_error = "response body is not valid JSON"
        for json_rule in rules.json_rules:
            if parse_error is not None:
                failures.append(AssertionFailure(f"AssertJson: {json_rule.expr}: {parse_error}"))
                continue
            try:
                actual = resolve_json_path(document, json_rule.path, json_rule.expr)
            except ExtractError as exc:
                failures.append(AssertionFailure(f"AssertJson: {exc}"))
                continue
            if json_rule.expected == ANY_VALUE:
                continue
            if not _json_matches(actual, json_rule.expected):
                failures.append(
                    AssertionFailure(
                        f"AssertJson: {json_rule.expr} != {json_rule.expected!r}",
                        f"was {actual!r}",
                    )
                )

    return failures
