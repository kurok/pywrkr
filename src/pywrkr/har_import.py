"""HAR (HTTP Archive) import for pywrkr.

Converts HAR files (recorded browser traffic) into pywrkr scenario files
or URL lists, dramatically reducing test-authoring time.

HAR spec: http://www.softwareishard.com/blog/har-12-spec/
"""

import base64
import binascii
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import parse_qsl, unquote_plus, urlparse

logger = logging.getLogger(__name__)


@dataclass
class HarEntry:
    """A single request extracted from a HAR file."""

    url: str
    method: str = "GET"
    headers: dict[str, str] = field(default_factory=dict)
    body: str | None = None
    content_type: str | None = None
    status: int = 0
    time_ms: float = 0.0
    started_datetime: str = ""  # ISO 8601 timestamp from HAR startedDateTime


@dataclass
class HarImportConfig:
    """Options controlling HAR-to-pywrkr conversion."""

    include_static: bool = False
    exclude_patterns: list[str] = field(default_factory=list)
    include_patterns: list[str] = field(default_factory=list)
    allowed_domains: list[str] = field(default_factory=list)
    preserve_headers: bool = False
    skip_headers: list[str] = field(
        default_factory=lambda: [
            "accept-encoding",
            "connection",
            "host",
            "user-agent",
            "content-length",
            "sec-ch-ua",
            "sec-ch-ua-mobile",
            "sec-ch-ua-platform",
            "sec-fetch-dest",
            "sec-fetch-mode",
            "sec-fetch-site",
            "sec-fetch-user",
            "upgrade-insecure-requests",
            "referer",
            "origin",
            "cookie",
        ]
    )
    add_think_time: bool = True
    think_time_multiplier: float = 1.0
    assert_status: bool = False


# File extensions considered "static" (images, fonts, stylesheets, scripts)
_STATIC_EXTENSIONS = frozenset(
    {
        ".css",
        ".js",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".svg",
        ".ico",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".otf",
        ".map",
        ".webp",
        ".avif",
        ".mp4",
        ".webm",
        ".mp3",
        ".ogg",
    }
)


def _is_static(url: str) -> bool:
    """Return True if the URL looks like a static asset."""
    path = urlparse(url).path.lower()
    _, ext = os.path.splitext(path)
    return ext in _STATIC_EXTENSIONS


# Cap on URL length passed to user-supplied regex matches. The Python ``re``
# engine offers no per-match timeout, so a pathological pattern combined with
# a very long URL can degrade to catastrophic backtracking. URLs longer than
# this are truncated for matching only — recording purposes are unaffected.
# 8192 is well above the practical upper bound for browser-recorded URLs.
_MATCH_URL_MAX = 8192


def _compile_patterns(patterns: list[str]) -> list[re.Pattern[str]]:
    """Compile a list of regex patterns once.

    Raises ``ValueError`` with a clear message if any pattern fails to compile,
    so the user sees the error at filter setup rather than buried in a per-URL
    match call.
    """
    compiled: list[re.Pattern[str]] = []
    for p in patterns:
        try:
            compiled.append(re.compile(p))
        except re.error as exc:
            raise ValueError(f"Invalid regex pattern {p!r}: {exc}") from exc
    return compiled


def _matches_patterns(url: str, patterns: list[re.Pattern[str]]) -> bool:
    """Return True if the URL matches any precompiled regex pattern.

    Security note: there is no per-match timeout. Patterns with catastrophic
    backtracking (e.g. ``(a+)+``, ``(.*a){20}``) matched against long URLs
    will hang the process. This is self-DoS only — both the pattern and the
    HAR file are supplied by the local operator. Use simple, anchored patterns
    to avoid this risk.
    """
    if len(url) > _MATCH_URL_MAX:
        url = url[:_MATCH_URL_MAX]
    for pattern in patterns:
        if pattern.search(url):
            return True
    return False


def parse_har(path: str) -> list[HarEntry]:
    """Parse a HAR file and return a list of HarEntry objects.

    Raises FileNotFoundError if the file doesn't exist, ValueError if the
    file is not valid HAR JSON.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"HAR file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in HAR file: {e}")

    if not isinstance(data, dict) or "log" not in data:
        raise ValueError("Invalid HAR file: missing 'log' key")

    log = data["log"]
    if not isinstance(log, dict) or "entries" not in log:
        raise ValueError("Invalid HAR file: missing 'log.entries' key")

    if not isinstance(log["entries"], list):
        raise ValueError("Invalid HAR file: 'log.entries' must be a list")

    entries = []
    for entry in log["entries"]:
        # Buggy exporters/proxies sometimes emit non-dict entries; skip them
        # rather than crash with AttributeError on entry.get(...).
        if not isinstance(entry, dict):
            continue
        request = entry.get("request", {})
        response = entry.get("response", {})
        if not isinstance(request, dict):
            continue
        if not isinstance(response, dict):
            response = {}

        url = request.get("url", "")
        if not url:
            continue

        method = request.get("method", "GET").upper()

        # Extract headers. The HAR spec records headers as a list of
        # {"name", "value"} objects; guard against exporters that emit a dict
        # (iterating a dict yields keys, which have no .get) or other shapes.
        headers = {}
        raw_headers = request.get("headers", [])
        if isinstance(raw_headers, list):
            for h in raw_headers:
                if not isinstance(h, dict):
                    continue
                name = h.get("name", "")
                value = h.get("value", "")
                if name:
                    # Preserve the recorded header name casing so
                    # --preserve-headers emits the original-cased names.
                    headers[name] = value

        # Extract body. The HAR spec allows postData.text to be a plain string
        # or, when postData.encoding == "base64", a base64-encoded payload.
        # Decode the latter so the scenario captures the bytes the browser
        # actually sent. If the decoded payload is not valid UTF-8 (i.e. the
        # body is genuinely binary, e.g. a multipart upload), drop the body
        # because pywrkr scenario steps can only carry text or JSON-shaped
        # bodies — silently passing the base64 string through would replay a
        # different payload from the one the browser sent.
        body = None
        content_type = None
        post_data = request.get("postData")
        # A well-formed HAR records postData as an object; some exporters emit
        # a bare string/list. Guard so a non-dict postData is ignored rather
        # than crashing on post_data.get(...).
        if isinstance(post_data, dict):
            text = post_data.get("text", "")
            content_type = post_data.get("mimeType", "")
            if text and post_data.get("encoding") == "base64":
                try:
                    raw = base64.b64decode(text, validate=True)
                    body = raw.decode("utf-8")
                except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
                    logger.warning(
                        "Skipping base64 body for %s %s: %s",
                        method,
                        url,
                        exc,
                    )
            else:
                body = text

        # Response status. Some exporters emit status as a string ("200");
        # coerce to int so HarEntry.status matches its int annotation and
        # downstream numeric comparisons (e.g. --assert-status) never crash.
        raw_status = response.get("status", 0)
        try:
            status = int(raw_status)
        except (TypeError, ValueError):
            status = 0

        # Timing.
        #
        # Coerced for the same reason response.status is coerced just above:
        # exporters emit these as strings, as null, and occasionally as
        # numbers. Stored raw they reached _compute_think_times and
        # _parse_iso_datetime, which died on a type they never expected --
        # AttributeError on an int startedDateTime, TypeError on a null time --
        # and neither is caught by the CLI's error path, so the user got a
        # traceback rather than a message.
        raw_time = entry.get("time", 0.0)
        try:
            time_ms = float(raw_time)
        except (TypeError, ValueError):
            time_ms = 0.0

        raw_started = entry.get("startedDateTime", "")
        started_datetime = raw_started if isinstance(raw_started, str) else ""

        entries.append(
            HarEntry(
                url=url,
                method=method,
                headers=headers,
                body=body if body else None,
                content_type=content_type,
                status=status,
                time_ms=time_ms,
                started_datetime=started_datetime,
            )
        )

    return entries


def filter_entries(
    entries: list[HarEntry],
    config: HarImportConfig,
) -> list[HarEntry]:
    """Apply filters to HAR entries based on HarImportConfig.

    Raises:
        ValueError: If any include or exclude pattern is not a valid regex.
    """
    # Compile user-supplied patterns once up front so an invalid regex fails
    # before we touch any entries, and so each URL is matched against
    # precompiled objects rather than re-parsing on every iteration.
    excludes = _compile_patterns(config.exclude_patterns)
    includes = _compile_patterns(config.include_patterns)

    result = []
    for entry in entries:
        # Drop non-HTTP(S) pseudo-requests (data:, blob:, about:) and
        # relative/host-less URLs. These yield a garbage base_url like
        # "://" or "data://" and are not replayable, so they must not
        # become scenario steps or pollute base_url derivation.
        parsed = urlparse(entry.url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            logger.debug("Skipping non-HTTP(S) HAR URL: %r", entry.url)
            continue

        # Filter static assets
        if not config.include_static and _is_static(entry.url):
            continue

        # Domain filtering
        if config.allowed_domains:
            domain = urlparse(entry.url).hostname or ""
            if not any(domain == d or domain.endswith("." + d) for d in config.allowed_domains):
                continue

        # Exclude patterns
        if excludes and _matches_patterns(entry.url, excludes):
            continue

        # Include patterns (if specified, only matching URLs pass)
        if includes and not _matches_patterns(entry.url, includes):
            continue

        result.append(entry)

    return result


def _build_step_headers(
    entry: HarEntry,
    config: HarImportConfig,
) -> dict[str, str]:
    """Build the headers dict for a scenario step."""
    if not config.preserve_headers:
        # Only keep content-type for requests with a body
        if entry.content_type and entry.body:
            return {"Content-Type": entry.content_type}
        return {}

    skip = set(config.skip_headers)
    return {name: value for name, value in entry.headers.items() if name.lower() not in skip}


def _parse_iso_datetime(s: str) -> float | None:
    """Parse an ISO 8601 datetime string to a Unix timestamp in seconds.

    Returns None if parsing fails.
    """
    if not s:
        return None
    try:
        # Handle common HAR formats: with/without timezone, with/without fractional seconds
        s = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


def _compute_think_times(
    entries: list[HarEntry],
    multiplier: float,
) -> list[float]:
    """Compute inter-request think times from HAR timing data.

    Returns a list of think times (one per entry). The first entry has 0.
    Think times are the gap between when a request starts and when the
    previous request finished (start + duration), i.e.:
        gap = start_time[i] - (start_time[i-1] + duration[i-1])

    Falls back to using entry durations if timestamps are unavailable.
    """
    if len(entries) <= 1:
        return [0.0] * len(entries)

    # Try to use actual startedDateTime timestamps
    parsed_ts = [_parse_iso_datetime(e.started_datetime) for e in entries]
    has_timestamps = all(t is not None for t in parsed_ts)
    timestamps: list[float] = [t for t in parsed_ts if t is not None] if has_timestamps else []

    think_times = [0.0]
    for i in range(1, len(entries)):
        if has_timestamps:
            # gap = when this request started - when previous request finished
            prev_end = timestamps[i - 1] + (entries[i - 1].time_ms / 1000.0)
            gap_sec = timestamps[i] - prev_end
        else:
            # Fallback: use previous entry duration as rough gap estimate
            gap_sec = entries[i - 1].time_ms / 1000.0

        gap_sec = max(0.0, gap_sec) * multiplier
        # Cap think time to something reasonable (max 30s)
        gap_sec = min(gap_sec, 30.0)
        # Round to 2 decimal places
        gap_sec = round(gap_sec, 2)
        think_times.append(gap_sec)

    return think_times


def har_to_scenario(
    entries: list[HarEntry],
    config: HarImportConfig,
    name: str = "HAR Import",
) -> dict:
    """Convert filtered HAR entries to a pywrkr scenario dict.

    The returned dict can be serialized to JSON/YAML and used with
    ``pywrkr --scenario``.
    """
    if not entries:
        raise ValueError("No HAR entries to convert (all filtered out?)")

    # The scenario format derives a single base_url (scheme + host) and emits
    # bare per-step paths that the runner concatenates onto it. If the HAR spans
    # more than one origin, every non-first-origin step would silently replay
    # against the first one. Key on scheme://host (not host alone) so an
    # http/https mismatch on the same host is caught too. Refuse to produce that
    # silent-wrong-result scenario; surface the mismatch so the user can
    # disambiguate (e.g. --domain filtering).
    origins = []
    for entry in entries:
        parsed = urlparse(entry.url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if parsed.netloc and origin not in origins:
            origins.append(origin)
    if len(origins) > 1:
        raise ValueError(
            "HAR spans multiple hosts "
            f"({', '.join(origins)}); the scenario format uses a single "
            "base_url and would replay all steps against the first host. "
            "Filter to one host with --domain, or use --format url-file "
            "which preserves absolute URLs."
        )

    # Compute think times from recorded timing
    think_times = (
        _compute_think_times(entries, config.think_time_multiplier)
        if config.add_think_time
        else [0.0] * len(entries)
    )

    steps = []
    for i, entry in enumerate(entries):
        parsed = urlparse(entry.url)
        # Use path + query as the step path (scenario is relative to base URL)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query

        step: dict = {
            "name": f"{entry.method} {path}",
            "path": path,
            "method": entry.method,
        }

        # Headers
        headers = _build_step_headers(entry, config)
        if headers:
            step["headers"] = headers

        # Body
        if entry.body:
            # Try to parse as JSON for cleaner output, but only adopt the
            # parsed value when it is structured (dict/list). A plain-text
            # body that happens to be a JSON scalar ("12345", "true", "null",
            # a quoted string) must stay a string: coercing it to int/bool/None
            # makes the scenario serializer emit a raw scalar that aiohttp
            # rejects at request time, silently dropping the recorded body.
            parsed_body = _parse_json_body(entry.body)
            step["body"] = parsed_body if isinstance(parsed_body, (dict, list)) else entry.body

        # Status assertion
        if config.assert_status and isinstance(entry.status, int) and 200 <= entry.status < 400:
            step["assert_status"] = entry.status

        # Think time
        if think_times[i] > 0:
            step["think_time"] = think_times[i]

        steps.append(step)

    # Derive base_url from the first entry's scheme + host
    first_parsed = urlparse(entries[0].url)
    base_url = f"{first_parsed.scheme}://{first_parsed.netloc}"

    return {
        "name": name,
        "base_url": base_url,
        "think_time": 0.0,
        "steps": steps,
    }


def har_to_url_file(
    entries: list[HarEntry],
) -> str:
    """Convert filtered HAR entries to pywrkr URL-file format.

    Returns a string with one ``METHOD URL`` per line (suitable for
    ``pywrkr --url-file``). Only unique URLs are included.

    Note:
        The url-file format cannot carry request bodies, so recorded
        POST/PUT payloads are dropped. Use ``--format scenario`` to preserve
        bodies. URLs containing control characters are skipped to prevent
        line injection.
    """
    if not entries:
        raise ValueError("No HAR entries to convert (all filtered out?)")

    seen = set()
    lines = []
    for entry in entries:
        # The url-file format is line-oriented and parsed line-by-line. A URL
        # OR method containing a raw newline/carriage-return (a malicious or
        # buggy HAR can embed one in either field — both are emitted on the
        # same line as "METHOD URL") would split into extra lines, injecting
        # unrelated requests (e.g. a POST to another host). Skip entries whose
        # method or URL holds control/non-printable characters so the output
        # stays one request per entry.
        if (
            "\r" in entry.url
            or "\n" in entry.url
            or not entry.url.isprintable()
            or "\r" in entry.method
            or "\n" in entry.method
            or not entry.method.isprintable()
        ):
            logger.warning(
                "Skipping HAR entry with control characters: method=%r url=%r",
                entry.method,
                entry.url,
            )
            continue
        key = (entry.method, entry.url)
        if key in seen:
            continue
        seen.add(key)
        if entry.method == "GET":
            lines.append(entry.url)
        else:
            lines.append(f"{entry.method} {entry.url}")

    return "\n".join(lines) + "\n"


# Header names and query parameters that usually carry a live credential.
#
# The default skip list drops "cookie" but keeps these, and the query string is
# copied into the step path unconditionally, so a HAR recorded from a
# logged-in session puts a working bearer token in a file that tends to be
# committed next to the scenario. They are not stripped, because scenarios are
# often replayed against the same environment and an existing test asserts
# Authorization survives -- so the user is told instead.
_CREDENTIAL_HEADER_NAMES = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "x-api-key",
        "x-auth-token",
        "x-csrf-token",
    }
)
# Which field names look like a credential.
#
# The base is the substring rule token|key|secret|password|sig, widened
# with pass/pwd/session ids, applied to the lower-cased name with separators
# removed, so "passWord", "x-api-keys" and "apikeyid" all match. The only names
# it deliberately stops flagging are the ordinary words below: each is cut out
# of the name before the match, so "monkey" is quiet but "monkey_api_key" is
# not. Leaning towards warning is intended -- a missed credential is committed
# silently, a spurious warning costs a glance.
_ORDINARY_WORDS = tuple(
    sorted(
        {
            # ...key
            "monkey", "donkey", "turkey", "hockey", "jockey", "whiskey", "turnkey",
            "keyboard", "keyword", "keynote", "keyframe", "keystroke", "hotkey",
            "sortkey", "cachekey", "idempotencykey", "partitionkey", "foreignkey",
            "publickey", "dedupkey",
            # ...sig
            "design", "signup", "signin", "signout", "signal", "assign", "assignee",
            "assigned", "assignment", "consign",
            "resign", "insight",
            # ...secret
            "secretary",
            # ...pass
            "passport", "passenger", "passage", "passive", "passed", "passing",
            "passes", "compass", "bypass", "surpass", "trespass", "overpass",
            "underpass", "passthrough",
            # ...token: counts and pagination cursors, not secrets
            "maxtokens", "maxnewtokens", "prompttokens", "completiontokens",
            "totaltokens", "inputtokens", "outputtokens", "stoptokens", "numtokens",
            "tokencount", "tokenizer", "nexttoken", "pagetoken", "nextpagetoken",
            "continuationtoken",
        },
        key=len,
        reverse=True,
    )
)  # fmt: skip
_CREDENTIAL_COMPACT_RE = re.compile(
    r"token|key|secret|pass|pwd|sig|credential|sessid|sessionid|jwt"
)
_NAME_SEGMENT_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")
# Short words that are a credential only as a whole segment ("sid", "otp"), and
# ones that are only as the last segment ("card_pin", but not "pin_code_hint").
_CREDENTIAL_NAME_SEGMENTS = frozenset({"sid", "otp", "bearer", "authorization"})
_CREDENTIAL_LAST_SEGMENTS = frozenset({"pin", "auth"})
# Free text has no field names, so only whole words count there. The letter
# boundaries are case-sensitive so "accessToken" matches but "Secretary" and
# "tokenizer" do not; long base64-like runs are skipped, since random data
# spells "pwd" now and then.
_CREDENTIAL_TEXT_RE = re.compile(
    r"(?-i:(?<![a-z])|(?<=[a-z])(?=[A-Z]))"
    r"(?:passw(?:or)?ds?|pwd|secrets?|token|api[_-]?key|credential|bearer)"
    r"(?-i:(?![a-z]))",
    re.IGNORECASE,
)
# "_" and "=" are left out so "access_token=<long token>" keeps its name.
_OPAQUE_RUN_RE = re.compile(r"[A-Za-z0-9+/-]{64,}={0,2}")
# A name is printed in the warning only when it looks like an identifier. A HAR
# can hold a "field name" that is really a value -- a token used as a JSON key,
# a form part with no "=" -- or one with control characters that would forge
# log lines; those print as a placeholder.
_PRINTABLE_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.\[\]-]{0,63}")
_VALUE_LIKE_RUN_RE = re.compile(
    r"[A-Za-z0-9]{20,}|(?=[A-Za-z0-9]*[0-9])(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{10,}"
)
_UNNAMED = "<unnamed field>"
_MULTIPART_NAME_RE = re.compile(r'\bname="([^"\r\n]*)"')
_FORM_BODY_RE = re.compile(r"[^=&\s]+=[^&\s]*(?:&[^=&\s]+=[^&\s]*)*")
# Deeper JSON than this is kept as a string: encoding it again recurses once
# per level and overflows the stack on some Python versions.
_MAX_JSON_DEPTH = 200


def _is_credential_name(name: str) -> bool:
    """Whether a header, query, form or JSON field name looks like a credential."""
    segments = [seg.lower() for seg in _NAME_SEGMENT_RE.findall(name)]
    compact = "".join(segments)
    # An ordinary word is cut out only where it lines up with the name's own
    # segment boundaries: "sortKey" and "max_tokens" go quiet, but "common_key"
    # (not "monkey"), "presignedUrl" (not "resign") and "login_by_password"
    # (not "bypass") still count.
    bounds = {0}
    for seg in segments:
        bounds.add(max(bounds) + len(seg))
    masked = list(compact)
    for word in _ORDINARY_WORDS:
        start = compact.find(word)
        while start != -1:
            end = start + len(word)
            if start in bounds and end in bounds:
                masked[start:end] = "|" * len(word)
            start = compact.find(word, start + 1)
    if _CREDENTIAL_COMPACT_RE.search("".join(masked)):
        return True
    segments = [seg.lower() for seg in _NAME_SEGMENT_RE.findall(name)]
    if any(seg in _CREDENTIAL_NAME_SEGMENTS for seg in segments):
        return True
    if any(a == "session" and b == "id" for a, b in zip(segments, segments[1:])):
        return True
    words = [seg for seg in segments if not seg.isdigit()]
    return bool(words) and words[-1] in _CREDENTIAL_LAST_SEGMENTS


def _text_mentions_credential(text: str) -> bool:
    """Whether free text names a credential, ignoring base64-like runs."""
    return bool(_CREDENTIAL_TEXT_RE.search(_OPAQUE_RUN_RE.sub(" ", text)))


def _credential_headers(headers: dict[str, str]) -> list[str]:
    """Header names that look like they carry a credential."""
    found = [
        name
        for name in headers
        if name.lower() in _CREDENTIAL_HEADER_NAMES or _is_credential_name(name)
    ]
    return sorted(found, key=str.lower)


def _display_name(name: str) -> str:
    """The name as it may appear in the warning, or a placeholder."""
    if _PRINTABLE_NAME_RE.fullmatch(name) and not _VALUE_LIKE_RUN_RE.search(name):
        return name
    return _UNNAMED


def _credential_form_fields(encoded: str) -> set[str]:
    """Credential-shaped field names in a query string or form-encoded body.

    A bare part with no ``=`` may be a value rather than a name, so when it
    looks like a credential it is reported as ``<unnamed field>`` and never
    printed.
    """
    parts = encoded.split("&")
    pairs = "&".join(part for part in parts if "=" in part)
    found = {
        _display_name(name)
        for name, _ in parse_qsl(pairs, keep_blank_values=True)
        if _is_credential_name(name)
    }
    if any("=" not in part and _is_credential_name(unquote_plus(part)) for part in parts):
        found.add(_UNNAMED)
    return found


def _credential_query_params(url: str) -> set[str]:
    """Query parameter names that look like they carry a credential."""
    return _credential_form_fields(urlparse(url).query)


def _parse_json_body(body: str) -> object:
    """``json.loads`` that returns None for anything it cannot parse or nests too deeply."""
    try:
        parsed = json.loads(body)
    except (TypeError, ValueError, RecursionError):  # JSONDecodeError is a ValueError
        return None
    stack: list[tuple[object, int]] = [(parsed, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > _MAX_JSON_DEPTH:
            return None
        if isinstance(node, dict):
            stack.extend((v, depth + 1) for v in node.values())
        elif isinstance(node, list):
            stack.extend((v, depth + 1) for v in node)
    return parsed


def _credential_body_fields(entry: HarEntry) -> tuple[set[str], bool]:
    """Credential-shaped field names in a recorded request body.

    A JSON object or array is walked, nested objects included: its keys are
    judged as names and its string values as free text. A URL-encoded form body
    (by MIME type or by shape) is split into its field names, and a multipart
    body into its part names. Anything else, or a body whose names say
    nothing, is judged as free text.

    Returns:
        The matching field names, and whether the body's text mentions a
        credential where no field name did.
    """
    body = entry.body
    if not body:
        return set(), False

    parsed = _parse_json_body(body)
    if isinstance(parsed, (dict, list)):
        names: set[str] = set()
        text_hit = False
        stack: list[object] = [parsed]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                for key, value in node.items():
                    if _is_credential_name(key):
                        names.add(_display_name(key))
                    stack.append(value)
            elif isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, str) and _text_mentions_credential(node):
                text_hit = True
        return names, text_hit and not names

    content_type = (entry.content_type or "").lower()
    stripped = body.strip()
    if "x-www-form-urlencoded" in content_type or _FORM_BODY_RE.fullmatch(stripped):
        fields = _credential_form_fields(stripped)
        if fields:
            return fields, False
    elif "multipart/" in content_type:
        fields = {
            _display_name(n) for n in _MULTIPART_NAME_RE.findall(body) if _is_credential_name(n)
        }
        if fields:
            return fields, False

    return set(), _text_mentions_credential(body)


def _warn_on_credentials(
    entries: list[HarEntry],
    config: HarImportConfig,
    include_bodies: bool = True,
    include_headers: bool = True,
) -> None:
    """Warn when the output will contain something that looks like a secret.

    Silence here would be the dangerous part: nothing else in the pipeline
    tells the user that the file they are about to commit has a live token in
    it.

    Args:
        entries: The entries that will be written.
        config: Import options; headers are only checked when they are kept.
        include_bodies: Whether request bodies reach the output. The url-file
            format drops them, so they cannot leak there.
        include_headers: Whether headers reach the output; url-file drops
            them too.
    """
    header_names: set[str] = set()
    query_names: set[str] = set()
    body_names: set[str] = set()
    text_bodies = 0

    for entry in entries:
        if include_headers and config.preserve_headers:
            header_names.update(
                _display_name(h) for h in _credential_headers(_build_step_headers(entry, config))
            )
        query_names.update(_credential_query_params(entry.url))
        if include_bodies:
            fields, flagged = _credential_body_fields(entry)
            body_names.update(fields)
            text_bodies += flagged

    if not header_names and not query_names and not body_names and not text_bodies:
        return

    parts = []
    if header_names:
        parts.append("headers " + ", ".join(sorted(header_names, key=str.lower)))
    if query_names:
        parts.append("query parameters " + ", ".join(sorted(query_names, key=str.lower)))
    if body_names:
        parts.append("request body fields " + ", ".join(sorted(body_names, key=str.lower)))
    if text_bodies:
        parts.append(
            f"{text_bodies} request bod{'y' if text_bodies == 1 else 'ies'} "
            "whose text mentions a password, secret, token, API key or credential"
        )

    logger.warning(
        "The generated output contains %s, which may hold live credentials "
        "from the recorded session. Replace the values with a template such "
        "as ${token} and supply them at run time before committing this file.",
        " and ".join(parts),
    )


def convert_har(
    har_path: str,
    output_path: str | None = None,
    output_format: str = "scenario",
    config: HarImportConfig | None = None,
    name: str | None = None,
) -> str:
    """High-level HAR conversion: parse, filter, convert, and optionally write.

    Args:
        har_path: Path to the .har file.
        output_path: Where to write output. If None, returns the content
            as a string without writing to disk.
        output_format: "scenario" (JSON scenario) or "url-file" (URL list).
        config: Import options. Uses defaults if None.
        name: Scenario name (used with format="scenario").

    Returns:
        The generated content as a string.

    Raises:
        FileNotFoundError: HAR file not found.
        ValueError: Invalid HAR or no entries after filtering.
    """
    if config is None:
        config = HarImportConfig()

    entries = parse_har(har_path)
    filtered = filter_entries(entries, config)

    if not filtered:
        raise ValueError(
            f"No requests remained after filtering {len(entries)} HAR entries. "
            f"Try --include-static or adjusting --domain / --exclude filters."
        )

    as_scenario = output_format == "scenario"
    _warn_on_credentials(filtered, config, include_bodies=as_scenario, include_headers=as_scenario)

    scenario_name = name or os.path.splitext(os.path.basename(har_path))[0]

    if output_format == "scenario":
        scenario = har_to_scenario(filtered, config, name=scenario_name)
        content = json.dumps(scenario, indent=2) + "\n"
    elif output_format == "url-file":
        # The url-file format cannot carry request bodies. If any filtered
        # entry recorded a body, warn so the user knows POST/PUT payloads are
        # dropped (and can switch to --format scenario to preserve them).
        dropped = sum(1 for e in filtered if e.body)
        if dropped > 0:
            logger.warning(
                "%d HAR entries with request bodies were dropped; "
                "--format url-file cannot carry bodies. "
                "Use --format scenario to preserve them.",
                dropped,
            )
        content = har_to_url_file(filtered)
    else:
        raise ValueError(
            f"Unknown output format: {output_format!r} (expected 'scenario' or 'url-file')"
        )

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)

    return content
