"""Regression tests for confirmed HAR-import defects (audit pass).

Each test targets one finding from the audit work order and is written to
FAIL on the pre-fix code and PASS on the fixed code in
``src/pywrkr/har_import.py``.
"""

import json
import logging

import pytest

from pywrkr.har_import import (
    HarEntry,
    HarImportConfig,
    _build_step_headers,
    convert_har,
    filter_entries,
    har_to_scenario,
    har_to_url_file,
    parse_har,
)


def _write_har(tmp_path, log_obj):
    """Write a HAR document and return its path."""
    path = tmp_path / "test.har"
    path.write_text(json.dumps({"log": log_obj}), encoding="utf-8")
    return str(path)


def _entry(url, method="GET", body=None, content_type=None, status=200):
    return HarEntry(
        url=url,
        method=method,
        body=body,
        content_type=content_type,
        status=status,
    )


# --- har-1: multi-host HAR -> wrong host ------------------------------------


def test_har1_multi_host_scenario_raises():
    """A HAR spanning multiple hosts must not silently collapse to one host."""
    entries = [
        _entry("http://api.example.com/v1/users"),
        _entry("http://cdn.other.com/data.json"),
    ]
    with pytest.raises(ValueError, match="multiple hosts"):
        har_to_scenario(entries, HarImportConfig())


def test_har1_single_host_scenario_ok():
    """A single-host HAR still produces a valid scenario."""
    entries = [
        _entry("http://api.example.com/v1/users"),
        _entry("http://api.example.com/v1/orders"),
    ]
    scenario = har_to_scenario(entries, HarImportConfig())
    assert scenario["base_url"] == "http://api.example.com"
    assert len(scenario["steps"]) == 2


# --- har-2: scalar JSON body coerced then dropped ---------------------------


def test_har2_numeric_body_stays_string():
    entries = [_entry("http://h/x", method="POST", body="12345")]
    scenario = har_to_scenario(entries, HarImportConfig())
    assert scenario["steps"][0]["body"] == "12345"
    assert isinstance(scenario["steps"][0]["body"], str)


def test_har2_bool_and_null_body_stay_strings():
    for raw in ("true", "false", "null", "3.14", '"quoted"'):
        entries = [_entry("http://h/x", method="POST", body=raw)]
        step = har_to_scenario(entries, HarImportConfig())["steps"][0]
        assert step["body"] == raw
        assert isinstance(step["body"], str)


def test_har2_structured_json_body_still_parsed():
    entries = [_entry("http://h/x", method="POST", body='{"a": 1}')]
    step = har_to_scenario(entries, HarImportConfig())["steps"][0]
    assert step["body"] == {"a": 1}


# --- har-3: malformed HAR -> ValueError, not AttributeError/TypeError --------


def test_har3_entries_null_raises_valueerror(tmp_path):
    path = _write_har(tmp_path, {"entries": None})
    with pytest.raises(ValueError):
        parse_har(path)


def test_har3_non_dict_entry_skipped(tmp_path):
    path = _write_har(
        tmp_path,
        {"entries": ["a string", {"request": {"url": "http://h/ok"}}]},
    )
    entries = parse_har(path)
    assert [e.url for e in entries] == ["http://h/ok"]


def test_har3_headers_as_dict_does_not_crash(tmp_path):
    path = _write_har(
        tmp_path,
        {"entries": [{"request": {"url": "http://h/ok", "headers": {"Accept": "x"}}}]},
    )
    entries = parse_har(path)
    assert entries[0].url == "http://h/ok"
    assert entries[0].headers == {}


def test_har3_postdata_as_string_does_not_crash(tmp_path):
    path = _write_har(
        tmp_path,
        {
            "entries": [
                {
                    "request": {
                        "url": "http://h/ok",
                        "method": "POST",
                        "postData": "rawstring",
                    }
                }
            ]
        },
    )
    entries = parse_har(path)
    assert entries[0].body is None


# --- har-4: control-char URL line injection in url-file ----------------------


def test_har4_newline_url_dropped():
    entries = [
        _entry("http://localhost:8080/a\nPOST http://evil.com/inject"),
        _entry("http://localhost:8080/clean"),
    ]
    out = har_to_url_file(entries)
    lines = [ln for ln in out.splitlines() if ln]
    assert lines == ["http://localhost:8080/clean"]
    assert "evil.com" not in out


def test_har4_newline_method_dropped():
    """Line injection is also possible through the method field, which is
    emitted on the same 'METHOD URL' line. A method with an embedded newline
    must be dropped, not just a malicious URL."""
    entries = [
        _entry("http://localhost:8080/a", method="GET\nPOST http://evil.com/inject"),
        _entry("http://localhost:8080/clean", method="GET"),
    ]
    out = har_to_url_file(entries)
    lines = [ln for ln in out.splitlines() if ln]
    assert lines == ["http://localhost:8080/clean"]
    assert "evil.com" not in out


# --- har-5: string response.status under --assert-status ---------------------


def test_har5_string_status_coerced(tmp_path):
    path = _write_har(
        tmp_path,
        {
            "entries": [
                {
                    "request": {"url": "http://h/ok"},
                    "response": {"status": "200"},
                }
            ]
        },
    )
    entries = parse_har(path)
    assert entries[0].status == 200
    scenario = har_to_scenario(entries, HarImportConfig(assert_status=True))
    assert scenario["steps"][0]["assert_status"] == 200


def test_har5_garbage_status_coerced_to_zero(tmp_path):
    path = _write_har(
        tmp_path,
        {
            "entries": [
                {
                    "request": {"url": "http://h/ok"},
                    "response": {"status": "not-a-number"},
                }
            ]
        },
    )
    entries = parse_har(path)
    assert entries[0].status == 0
    scenario = har_to_scenario(entries, HarImportConfig(assert_status=True))
    assert "assert_status" not in scenario["steps"][0]


# --- har-6: relative / non-HTTP first URL -> garbage base_url ----------------


def test_har6_relative_and_data_urls_dropped():
    entries = [
        _entry("/just/a/path?x=1"),
        _entry("data:text/html,hello"),
        _entry("http://real.example.com/api"),
    ]
    filtered = filter_entries(entries, HarImportConfig())
    assert [e.url for e in filtered] == ["http://real.example.com/api"]
    scenario = har_to_scenario(filtered, HarImportConfig())
    assert scenario["base_url"] == "http://real.example.com"


# --- har-7: --preserve-headers lowercases names ------------------------------


def test_har7_preserve_headers_keeps_casing(tmp_path):
    path = _write_har(
        tmp_path,
        {
            "entries": [
                {
                    "request": {
                        "url": "http://h/ok",
                        "headers": [
                            {"name": "X-Custom-Header", "value": "v"},
                            {"name": "Authorization", "value": "Bearer t"},
                        ],
                    }
                }
            ]
        },
    )
    entries = parse_har(path)
    headers = _build_step_headers(entries[0], HarImportConfig(preserve_headers=True))
    assert headers == {"X-Custom-Header": "v", "Authorization": "Bearer t"}


def test_har7_skip_list_still_applies_case_insensitively():
    entry = HarEntry(
        url="http://h/ok",
        headers={"Host": "h", "X-Keep": "v"},
    )
    headers = _build_step_headers(entry, HarImportConfig(preserve_headers=True))
    assert "Host" not in headers
    assert headers == {"X-Keep": "v"}


# --- har-8: url-file drops POST/PUT bodies (warning) -------------------------


def test_har8_url_file_warns_on_dropped_bodies(tmp_path, caplog):
    path = _write_har(
        tmp_path,
        {
            "entries": [
                {
                    "request": {
                        "url": "http://h/api/create",
                        "method": "POST",
                        "postData": {"text": '{"name": "widget"}', "mimeType": "application/json"},
                    }
                }
            ]
        },
    )
    with caplog.at_level(logging.WARNING):
        out = convert_har(path, output_format="url-file")
    assert "POST http://h/api/create" in out
    assert any("request bodies were dropped" in r.message for r in caplog.records)


def test_scheme_mismatch_same_host_rejected():
    # har-1 (hardening): http vs https on the SAME host is a different origin;
    # the single-base_url scenario format must refuse it rather than silently
    # replaying every step against the first entry's scheme.
    entries = [
        HarEntry(url="http://h/a", method="GET", status=200, time_ms=10),
        HarEntry(url="https://h/b", method="GET", status=200, time_ms=10),
    ]
    with pytest.raises(ValueError, match="multiple hosts"):
        har_to_scenario(entries, HarImportConfig())
