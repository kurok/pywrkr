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
from urllib.parse import urlparse

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

        # Timing
        time_ms = entry.get("time", 0.0)

        started_datetime = entry.get("startedDateTime", "")

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
            try:
                parsed = json.loads(entry.body)
            except (json.JSONDecodeError, TypeError):
                step["body"] = entry.body
            else:
                step["body"] = parsed if isinstance(parsed, (dict, list)) else entry.body

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
