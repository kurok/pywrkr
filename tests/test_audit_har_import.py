"""Regression tests for confirmed HAR-import defects (audit pass).

Each test targets one finding from the audit work order and is written to
FAIL on the pre-fix code and PASS on the fixed code in
``src/pywrkr/har_import.py``.
"""

import json
import logging
import sys
from unittest.mock import patch

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


def test_har_time_and_started_datetime_coerced(tmp_path):
    """Exporters emit these fields as strings, nulls and numbers.

    Stored raw they reached the think-time computation and the ISO parser,
    which died on the unexpected type -- and neither AttributeError nor
    TypeError is caught by the CLI, so the user saw a traceback rather than an
    error message.
    """
    path = _write_har(
        tmp_path,
        {
            "entries": [
                {
                    "request": {"url": "http://h/a", "method": "GET"},
                    "response": {"status": 200},
                    "time": "12.5",
                    "startedDateTime": 5,
                },
                {
                    "request": {"url": "http://h/b", "method": "GET"},
                    "response": {"status": 200},
                    "time": None,
                    "startedDateTime": "2024-01-01T00:00:01Z",
                },
                {
                    "request": {"url": "http://h/c", "method": "GET"},
                    "response": {"status": 200},
                    "time": {"not": "a number"},
                    "startedDateTime": None,
                },
            ]
        },
    )

    entries = parse_har(path)
    assert [e.time_ms for e in entries] == [12.5, 0.0, 0.0]
    assert [e.started_datetime for e in entries] == ["", "2024-01-01T00:00:01Z", ""]

    # The whole point: this used to raise rather than produce a scenario.
    scenario = har_to_scenario(entries, HarImportConfig())
    assert len(scenario["steps"]) == 3


def test_har_preserve_headers_warns_on_credentials(tmp_path, caplog):
    """A recorded session's Authorization header and ?access_token= survive
    into the generated file, which usually gets committed next to the scenario.
    They are deliberately not stripped -- scenarios get replayed against the
    same environment -- so the only protection is saying so out loud.
    """
    path = _write_har(
        tmp_path,
        {
            "entries": [
                {
                    "request": {
                        "url": "http://h/api?access_token=SECRETQ&page=2",
                        "method": "GET",
                        "headers": [
                            {"name": "Authorization", "value": "Bearer SECRET1"},
                            {"name": "X-API-Key", "value": "SECRET2"},
                            {"name": "Accept", "value": "application/json"},
                        ],
                    },
                    "response": {"status": 200},
                }
            ]
        },
    )

    with caplog.at_level(logging.WARNING):
        convert_har(str(path), config=HarImportConfig(preserve_headers=True))

    warning = "\n".join(r.getMessage() for r in caplog.records)
    assert "Authorization" in warning
    assert "X-API-Key" in warning
    assert "access_token" in warning
    assert "${token}" in warning
    # Not everything is a secret.
    assert "Accept" not in warning
    assert "page" not in warning


def test_har_without_credentials_does_not_warn(tmp_path, caplog):
    """The warning has to stay rare enough to be worth reading."""
    path = _write_har(
        tmp_path,
        {
            "entries": [
                {
                    "request": {
                        "url": "http://h/api?page=2",
                        "method": "GET",
                        "headers": [{"name": "Accept", "value": "application/json"}],
                    },
                    "response": {"status": 200},
                }
            ]
        },
    )

    with caplog.at_level(logging.WARNING):
        convert_har(str(path), config=HarImportConfig(preserve_headers=True))

    assert not [r for r in caplog.records if "credentials" in r.message]


def _body_har(tmp_path, body, mime="application/json"):
    return _write_har(
        tmp_path,
        {
            "entries": [
                {
                    "request": {
                        "url": "https://app.example.test/api/login",
                        "method": "POST",
                        "headers": [{"name": "Content-Type", "value": mime}],
                        "postData": {"mimeType": mime, "text": body},
                    },
                    "response": {"status": 200},
                }
            ]
        },
    )


def _credential_warnings(caplog):
    return "\n".join(r.getMessage() for r in caplog.records if "credentials" in r.getMessage())


def test_har_import_warns_on_credential_in_request_body(tmp_path, caplog):
    """A recorded login puts the password in the POST body by construction."""
    path = _body_har(tmp_path, '{"username": "u", "password": "S3cret-Dummy-Passw0rd"}')

    with caplog.at_level(logging.WARNING):
        content = convert_har(path)

    warning = _credential_warnings(caplog)
    assert "request body fields password" in warning
    assert "username" not in warning
    # Never the value, and the body itself is kept (warn, do not strip).
    assert "S3cret-Dummy-Passw0rd" not in warning
    assert json.loads(content)["steps"][0]["body"]["password"] == "S3cret-Dummy-Passw0rd"


def test_har_import_warns_on_nested_credential_in_request_body(tmp_path, caplog):
    body = '{"query": "mutation", "variables": [{"input": {"apiKey": "K", "name": "n"}}]}'
    path = _body_har(tmp_path, body)

    with caplog.at_level(logging.WARNING):
        convert_har(path)

    assert "request body fields apiKey" in _credential_warnings(caplog)


def test_har_import_warns_on_form_encoded_credential(tmp_path, caplog):
    path = _body_har(
        tmp_path, "username=u&password=S3cret&remember=1", "application/x-www-form-urlencoded"
    )

    with caplog.at_level(logging.WARNING):
        convert_har(path)

    warning = _credential_warnings(caplog)
    assert "request body fields password" in warning
    assert "S3cret" not in warning
    assert "remember" not in warning


def test_har_import_warns_on_unstructured_body_mentioning_a_secret(tmp_path, caplog):
    path = _body_har(tmp_path, "<login><passwd>S3cret</passwd></login>", "text/xml")

    with caplog.at_level(logging.WARNING):
        convert_har(path)

    warning = _credential_warnings(caplog)
    assert "1 request body whose text mentions" in warning
    assert "S3cret" not in warning


@pytest.mark.parametrize(
    ("body", "mime"),
    [
        ('{"email": "a@example.test", "items": [{"quantity": 1}]}', "application/json"),
        (
            '{"monkey": 1, "design": 2, "keyword": "k", "signup": true, "sortKey": "a", '
            '"cacheKey": "b", "idempotencyKey": "c", "passport": "p"}',
            "application/json",
        ),
        ("email=a%40example.test&page=2", "application/x-www-form-urlencoded"),
        ("<order><design>blue</design></order>", "text/xml"),
        ("12345", "text/plain"),
    ],
)
def test_har_import_does_not_warn_on_ordinary_body(tmp_path, caplog, body, mime):
    path = _body_har(tmp_path, body, mime)

    with caplog.at_level(logging.WARNING):
        convert_har(path)

    assert not _credential_warnings(caplog)


def test_har_import_url_file_does_not_warn_on_dropped_body(tmp_path, caplog):
    """url-file drops bodies, so a body credential never reaches that output."""
    path = _body_har(tmp_path, '{"password": "S3cret"}')

    with caplog.at_level(logging.WARNING):
        content = convert_har(path, output_format="url-file")

    assert "S3cret" not in content
    assert not _credential_warnings(caplog)


@pytest.mark.parametrize(
    ("body", "mime", "field"),
    [
        ('{"passwd": "S3cret"}', "application/json", "passwd"),
        ('{"credentials": {"user": "a", "pass": "S3cret"}}', "application/json", "credentials"),
        ("log=admin&pwd=S3cret&wp-submit=Log+In", "application/x-www-form-urlencoded", "pwd"),
        ("user=a&pass=S3cret", "application/x-www-form-urlencoded", "pass"),
        ('{"client_secret": "S3cret", "privateKey": "S3cret"}', "application/json", "privateKey"),
    ],
)
def test_har_import_names_common_password_fields(tmp_path, caplog, body, mime, field):
    """A WordPress login posts ``pwd``; the rule must not depend on the word "password"."""
    path = _body_har(tmp_path, body, mime)

    with caplog.at_level(logging.WARNING):
        convert_har(path)

    warning = _credential_warnings(caplog)
    assert field in warning
    assert "S3cret" not in warning


def test_har_import_form_body_without_equals_is_not_printed(tmp_path, caplog):
    """A bare value is not a field name; printing it would log the secret."""
    path = _body_har(tmp_path, "sk_live_TOKEN_abc123", "application/x-www-form-urlencoded")

    with caplog.at_level(logging.WARNING):
        convert_har(path)

    warning = _credential_warnings(caplog)
    assert "sk_live_TOKEN_abc123" not in warning
    assert "request body fields <unnamed field>" in warning


def test_har_import_query_without_equals_is_not_printed(tmp_path, caplog):
    """A bare part may be the value itself: still warned, never printed."""
    path = _write_har(
        tmp_path,
        {
            "entries": [
                {
                    "request": {"url": "http://h/api?sk_live_TOKEN_abc123", "method": "GET"},
                    "response": {"status": 200},
                }
            ]
        },
    )

    with caplog.at_level(logging.WARNING):
        convert_har(path)

    assert "sk_live_TOKEN_abc123" not in "\n".join(r.getMessage() for r in caplog.records)
    assert "query parameters <unnamed field>" in _credential_warnings(caplog)


def test_har_import_control_characters_in_field_name_are_not_logged(tmp_path, caplog):
    path = _body_har(tmp_path, json.dumps({"password\n\u001b[31mFAKE LOG LINE": "x"}))

    with caplog.at_level(logging.WARNING):
        convert_har(path)

    warning = _credential_warnings(caplog)
    assert "<unnamed field>" in warning
    assert "FAKE LOG LINE" not in warning
    assert "\u001b" not in warning


@pytest.mark.parametrize(
    "body",
    [
        '{"query": "mutation { login(email: \\"a\\", password: \\"x\\") }"}',
        '{"data": "password=hunter2"}',
        '"my-api-token-value"',
    ],
)
def test_har_import_flags_credential_text_inside_json_values(tmp_path, caplog, body):
    path = _body_har(tmp_path, body)

    with caplog.at_level(logging.WARNING):
        convert_har(path)

    warning = _credential_warnings(caplog)
    assert "1 request body whose text mentions" in warning
    assert "hunter2" not in warning


def test_har_import_names_multipart_part(tmp_path, caplog):
    body = '--b\r\nContent-Disposition: form-data; name="pwd"\r\n\r\nS3cret\r\n--b--\r\n'
    path = _body_har(tmp_path, body, "multipart/form-data; boundary=b")

    with caplog.at_level(logging.WARNING):
        convert_har(path)

    warning = _credential_warnings(caplog)
    assert "request body fields pwd" in warning
    assert "S3cret" not in warning


def test_har_import_checks_base64_body(tmp_path, caplog):
    import base64

    raw = base64.b64encode(b'{"password": "S3cret"}').decode()
    path = _write_har(
        tmp_path,
        {
            "entries": [
                {
                    "request": {
                        "url": "https://h/login",
                        "method": "POST",
                        "postData": {
                            "mimeType": "application/json",
                            "text": raw,
                            "encoding": "base64",
                        },
                    },
                    "response": {"status": 200},
                }
            ]
        },
    )

    with caplog.at_level(logging.WARNING):
        convert_har(path)

    assert "request body fields password" in _credential_warnings(caplog)


def test_har_import_one_warning_names_every_carrier(tmp_path, caplog):
    path = _write_har(
        tmp_path,
        {
            "entries": [
                {
                    "request": {
                        "url": "https://h/login?access_token=Q",
                        "method": "POST",
                        "headers": [{"name": "Authorization", "value": "Bearer H"}],
                        "postData": {"mimeType": "application/json", "text": '{"pwd": "B"}'},
                    },
                    "response": {"status": 200},
                }
            ]
        },
    )

    with caplog.at_level(logging.WARNING):
        convert_har(path, config=HarImportConfig(preserve_headers=True))

    records = [r.getMessage() for r in caplog.records if "credentials" in r.getMessage()]
    assert len(records) == 1
    assert "headers Authorization" in records[0]
    assert "query parameters access_token" in records[0]
    assert "request body fields pwd" in records[0]


def test_har_import_deeply_nested_body_does_not_crash(tmp_path):
    """Deep JSON used to escape ``har_to_scenario`` as a RecursionError traceback."""
    for body in ("[" * 100000, "[" * 2000 + "]" * 2000, "[" * 100000 + "]" * 100000):
        content = convert_har(_body_har(tmp_path, body))
        assert json.loads(content)["steps"][0]["body"] == body


# Every name main's query rule (token|key|secret|password|sig) flagged that is
# really a credential must still be flagged by the segment rule.
_CREDENTIAL_NAMES = [
    "access_token",
    "api_key",
    "apiKey",
    "APIKey",
    "key",
    "sig",
    "signature",
    "X-Amz-Signature",
    "client_secret",
    "password",
    "newpassword",
    "subscription-key",
    "Ocp-Apim-Subscription-Key",
    "app_key",
    "appkey",
    "authkey",
    "api_keys",
    "passkey",
    "privkey",
    "session_key",
    "consumer_key",
    "oauth_consumer_key",
    "master_key",
    "hmac_key",
    "keyId",
    "pwd",
    "passwd",
    "passcode",
    "user_pass",
    "card_pin",
    "otp",
    "session_id",
    "sessionId",
    "PHPSESSID",
    "JSESSIONID",
    "primaryKey",
    "sign",
    "apiSign",
    "sigv4",
    "hmacsig",
    "apikeys",
    "x-apikeys",
    "keyid",
    "accesskeyid",
    "keypass",
    "keystore",
    "passWord",
    "userPassWord",
    "passPhrase",
    "pass_code",
    "pass1",
    "newpwd",
    "loginpass",
    "connect.sid",
    "ASP.NET_SessionId",
    # An ordinary word straddling a segment boundary must not swallow the
    # credential next to it.
    "presignedUrl",
    "pre_signed_url",
    "X-Amz-Presign",
    "common_key",
    "commonKey",
    "commonkey",
    "login_by_password",
    "authByPassword",
    "gas_signature",
    "node_signature",
    "shot_key",
    "return_key",
    "homepage_token",
    "groupKey",
]
_ORDINARY_NAMES = [
    "monkey",
    "design",
    "keyword",
    "signup",
    "assignee",
    "sortKey",
    "cacheKey",
    "idempotencyKey",
    "public_key",
    "passport",
    "pass_through",
    "auth_type",
    "authProvider",
    "cookie_consent",
    "max_tokens",
    "prompt_tokens",
    "max_new_tokens",
    "NextToken",
    "pageToken",
    "continuation_token",
    "tokenizer",
    "keyword",
    "assigned_to",
    "page",
    # A count of CSRF tokens and a search keyword: both are listed words lined
    # up with the name's segments, so they are deliberately quiet.
    "csrf_token_count",
    "api_keyword",
]


def test_credential_name_rule_is_a_superset_of_mains_except_ordinary_words():
    """Every name the old substring rule (token|key|secret|password|sig)
    flagged still counts, unless an ordinary word listed in ``_ORDINARY_WORDS``
    is all that matched."""
    import re

    from pywrkr.har_import import _is_credential_name

    main_rule = re.compile(r"token|key|secret|password|sig", re.IGNORECASE)
    for name in _CREDENTIAL_NAMES + _ORDINARY_NAMES:
        if main_rule.search(name) and not _is_credential_name(name):
            assert name in _ORDINARY_NAMES, name


@pytest.mark.parametrize("name", _CREDENTIAL_NAMES)
def test_credential_name_rule_flags(name):
    from pywrkr.har_import import _is_credential_name

    assert _is_credential_name(name)


@pytest.mark.parametrize("name", _ORDINARY_NAMES)
def test_credential_name_rule_ignores(name):
    from pywrkr.har_import import _is_credential_name

    assert not _is_credential_name(name)


def test_har_import_warns_on_subscription_key_query(tmp_path, caplog):
    """Azure APIM's query credential: flagged on main, must stay flagged."""
    path = _write_har(
        tmp_path,
        {
            "entries": [
                {
                    "request": {"url": "https://x.test/a?subscription-key=abc", "method": "GET"},
                    "response": {"status": 200},
                }
            ]
        },
    )

    with caplog.at_level(logging.WARNING):
        convert_har(path)

    assert "query parameters subscription-key" in _credential_warnings(caplog)


@pytest.mark.parametrize("mime", ["", "text/plain"])
def test_har_import_reads_form_shaped_body_whatever_the_mime_type(tmp_path, caplog, mime):
    path = _body_har(tmp_path, "user=a&pass=hunter2", mime)

    with caplog.at_level(logging.WARNING):
        convert_har(path)

    warning = _credential_warnings(caplog)
    assert "request body fields pass" in warning
    assert "hunter2" not in warning


@pytest.mark.parametrize(
    "body",
    [
        '{"tokens": {"secret_AbCdEf0123456789": {}}}',
        '{"sk_live_secretABC123": 1}',
    ],
)
def test_har_import_token_shaped_name_is_not_printed(tmp_path, caplog, body):
    path = _body_har(tmp_path, body)

    with caplog.at_level(logging.WARNING):
        convert_har(path)

    warning = _credential_warnings(caplog)
    assert "<unnamed field>" in warning
    assert "AbCdEf0123456789" not in warning
    assert "secretABC123" not in warning


@pytest.mark.parametrize(
    "body",
    [
        '{"title": "Secretary", "note": "tokenizer"}',
        '{"model": "m", "max_tokens": 5, "messages": [{"content": "Tokens remaining"}]}',
        '{"grant_type": "client_credentials"}',
    ],
)
def test_har_import_ordinary_words_do_not_warn(tmp_path, caplog, body):
    path = _body_har(tmp_path, body)

    with caplog.at_level(logging.WARNING):
        convert_har(path)

    assert not _credential_warnings(caplog)


def test_har_import_url_file_does_not_warn_on_dropped_headers(tmp_path, caplog):
    path = _write_har(
        tmp_path,
        {
            "entries": [
                {
                    "request": {
                        "url": "https://h/a",
                        "method": "GET",
                        "headers": [{"name": "Authorization", "value": "Bearer X"}],
                    },
                    "response": {"status": 200},
                }
            ]
        },
    )

    with caplog.at_level(logging.WARNING):
        convert_har(path, output_format="url-file", config=HarImportConfig(preserve_headers=True))

    assert not _credential_warnings(caplog)


def test_har_import_names_wordpress_registration_fields(tmp_path, caplog):
    path = _body_har(tmp_path, "user_login=bob&pass1=hunter2&pass2=hunter2", "")

    with caplog.at_level(logging.WARNING):
        convert_har(path)

    warning = _credential_warnings(caplog)
    assert "request body fields pass1, pass2" in warning
    assert "hunter2" not in warning


def test_har_import_form_body_with_trailing_newline(tmp_path, caplog):
    path = _body_har(tmp_path, "user=bob&app_key=X\n", "text/plain")

    with caplog.at_level(logging.WARNING):
        convert_har(path)

    assert "request body fields app_key" in _credential_warnings(caplog)


def test_har_import_base64_payload_does_not_warn(tmp_path, caplog):
    """Random base64 spells "pwd" now and then; an upload must not warn."""
    import base64
    import random

    data = base64.b64encode(random.Random(305).randbytes(300_000)).decode()
    path = _body_har(tmp_path, json.dumps({"image": data}))

    with caplog.at_level(logging.WARNING):
        convert_har(path)

    assert not _credential_warnings(caplog)


@pytest.mark.parametrize(
    "name", ["X-Hub-Signature-256", "oauth2_token", "user[0][password]", "token_v12"]
)
def test_identifier_names_with_digits_are_printed(name):
    from pywrkr.har_import import _display_name

    assert _display_name(name) == name


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Authorization: Bearer eyJ", True),
        ("old passwords", True),
        ("api_secrets", True),
        # Plural token/credential is how ordinary text talks about them
        # ("Tokens remaining", grant_type=client_credentials), so it stays quiet.
        ("two tokens", False),
        ("client_credentials", False),
    ],
)
def test_credential_text_words(text, expected):
    from pywrkr.har_import import _text_mentions_credential

    assert _text_mentions_credential(text) is expected


@pytest.mark.parametrize(
    ("body", "mime"),
    [
        ('{"redirect": "https://x/cb?access_token=' + "A" * 64 + '"}', "application/json"),
        ("token=" + "A" * 64 + " extra", "text/plain"),
        ('{"x": "pw_' + "a" * 61 + '_password_"}', "application/json"),
    ],
)
def test_har_import_long_value_does_not_hide_its_name(tmp_path, caplog, body, mime):
    """The base64 skip must not swallow "access_token=" along with the value."""
    path = _body_har(tmp_path, body, mime)

    with caplog.at_level(logging.WARNING):
        convert_har(path)

    warning = _credential_warnings(caplog)
    assert "1 request body whose text mentions" in warning
    assert "A" * 64 not in warning


def test_har_import_cross_segment_names_warn_end_to_end(tmp_path, caplog):
    path = _write_har(
        tmp_path,
        {
            "entries": [
                {
                    "request": {
                        "url": "https://h/a?presignedUrl=x",
                        "method": "POST",
                        "postData": {
                            "mimeType": "application/json",
                            "text": '{"common_key": "S3CRET", "login_by_password": "hunter2"}',
                        },
                    },
                    "response": {"status": 200},
                }
            ]
        },
    )

    with caplog.at_level(logging.WARNING):
        convert_har(path)

    warning = _credential_warnings(caplog)
    assert "query parameters presignedUrl" in warning
    assert "request body fields common_key, login_by_password" in warning
    assert "S3CRET" not in warning
    assert "hunter2" not in warning


def test_long_names_are_judged_in_linear_time():
    """A base64 blob posted as a form body becomes one huge "field name"."""
    import time

    from pywrkr.har_import import _is_credential_name

    start = time.perf_counter()
    _is_credential_name("aB" * 40000)
    _is_credential_name("aB" * 100)
    assert time.perf_counter() - start < 1.0


def test_har_import_large_form_shaped_base64_body_is_fast_and_quiet(tmp_path, caplog):
    import base64
    import random
    import time

    blob = base64.b64encode(random.Random(313).randbytes(600_000)).decode()
    path = _body_har(tmp_path, blob, "application/x-www-form-urlencoded")

    start = time.perf_counter()
    with caplog.at_level(logging.WARNING):
        convert_har(path)
    assert time.perf_counter() - start < 5.0
    assert not _credential_warnings(caplog)


_TOKEN64 = "ab12" * 16


@pytest.mark.parametrize(
    "text",
    [
        f"https://a.example/reset-password/{_TOKEN64}",
        f"link: https://a.example/api/token/{_TOKEN64}",
        f"api-key-{_TOKEN64}",
        f"client-secret-{_TOKEN64}",
        f"token-{_TOKEN64}",
        f"pwd/{_TOKEN64}",
        f"secret+{_TOKEN64}",
    ],
)
def test_credential_word_joined_to_a_long_token_still_counts(text):
    from pywrkr.har_import import _text_mentions_credential

    assert _text_mentions_credential(text)


def test_har_import_reset_link_in_json_warns(tmp_path, caplog):
    body = json.dumps({"callback": f"https://a.example/reset-password/{_TOKEN64}"})
    path = _body_har(tmp_path, body)

    with caplog.at_level(logging.WARNING):
        convert_har(path)

    warning = _credential_warnings(caplog)
    assert "1 request body whose text mentions" in warning
    assert _TOKEN64 not in warning


def test_random_base64_does_not_count_as_credential_text():
    import base64
    import random

    from pywrkr.har_import import _text_mentions_credential

    for seed in range(100):
        data = base64.b64encode(random.Random(seed).randbytes(100_000)).decode()
        assert not _text_mentions_credential(data), seed


@pytest.mark.parametrize(
    ("text", "mime"),
    [
        ({"password": "x"}, "application/json"),
        (12345, "text/plain"),
        (["a"], "application/json"),
        ("a=b", 5),
        ("a=b", ["a"]),
    ],
)
def test_har_import_non_string_post_data_does_not_crash(tmp_path, text, mime):
    """Exporters put odd shapes in postData; main converted these, so must we."""
    path = _write_har(
        tmp_path,
        {
            "entries": [
                {
                    "request": {
                        "url": "https://h/a",
                        "method": "POST",
                        "postData": {"mimeType": mime, "text": text},
                    },
                    "response": {"status": 200},
                }
            ]
        },
    )
    convert_har(path)


@pytest.mark.parametrize(
    "text",
    [
        f"https://app/resetPassword/{_TOKEN64}",
        f"verifyToken/{_TOKEN64}",
        f"getApiKey/{_TOKEN64}",
        f"passwordReset/{_TOKEN64}",
        f"client_secret_{_TOKEN64}",
    ],
)
def test_camel_and_snake_case_word_beside_a_long_token_counts(text):
    from pywrkr.har_import import _text_mentions_credential

    assert _text_mentions_credential(text)


def test_random_url_safe_base64_does_not_count_as_credential_text():
    import base64
    import random

    from pywrkr.har_import import _text_mentions_credential

    for seed in range(300):
        data = base64.urlsafe_b64encode(random.Random(seed).randbytes(3000)).decode()
        assert not _text_mentions_credential(data), seed


def test_short_base64_body_is_not_read_as_a_form_field():
    import base64
    import random

    from pywrkr.har_import import _credential_body_fields

    for seed in range(500):
        rng = random.Random(seed)
        body = base64.b64encode(rng.randbytes(rng.randint(48, 190))).decode()
        entry = HarEntry(url="http://h/", method="POST", body=body, content_type="")
        assert _credential_body_fields(entry) == (set(), False), seed


@pytest.mark.parametrize(
    "name", ["newPasswordConfirmation", "googleRecaptchaToken", "oauth2Token", "password12"]
)
def test_camel_case_names_are_printed(name):
    from pywrkr.har_import import _display_name

    assert _display_name(name) == name


# --- #323: --domain with a port ----------------------------------------------


def _domain_entries():
    return [
        _entry("http://localhost:8080/x"),
        _entry("http://localhost:9000/y"),
        _entry("http://example.com/z"),
        _entry("https://api.example.com/v"),
    ]


def _kept(domains):
    return [
        e.url for e in filter_entries(_domain_entries(), HarImportConfig(allowed_domains=domains))
    ]


def test_domain_filter_accepts_a_host_with_a_port():
    assert _kept(["localhost:8080"]) == ["http://localhost:8080/x"]


def test_domain_filter_bare_host_matches_any_port():
    assert _kept(["localhost"]) == ["http://localhost:8080/x", "http://localhost:9000/y"]


def test_domain_filter_still_rejects_another_host():
    assert "http://example.com/z" not in _kept(["localhost"])
    assert _kept(["localhost:8080"]) == ["http://localhost:8080/x"]


def test_domain_filter_default_port_and_subdomains():
    assert _kept(["example.com:443"]) == ["https://api.example.com/v"]
    assert _kept(["EXAMPLE.com"]) == ["http://example.com/z", "https://api.example.com/v"]
    assert _kept(["example.com:80"]) == ["http://example.com/z"]


def test_domain_filter_ipv6_with_a_port():
    entries = [_entry("http://[::1]:8080/a"), _entry("http://[::1]:9000/b")]
    kept = filter_entries(entries, HarImportConfig(allowed_domains=["[::1]:8080"]))
    assert [e.url for e in kept] == ["http://[::1]:8080/a"]


def test_empty_filter_error_names_the_domain_values(tmp_path):
    path = _write_har(
        tmp_path,
        {
            "entries": [
                {
                    "request": {"url": "http://localhost:8080/x", "method": "GET"},
                    "response": {"status": 200},
                }
            ]
        },
    )
    with pytest.raises(ValueError) as exc:
        convert_har(path, config=HarImportConfig(allowed_domains=["127.0.0.1:8080"]))
    message = str(exc.value)
    assert "'127.0.0.1:8080'" in message
    assert "localhost:8080" in message
    assert "a port only when one is given" in message


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("api.example.com", ("api.example.com", None)),
        (" Localhost:8080 ", ("localhost", 8080)),
        ("[::1]:8080", ("::1", 8080)),
        ("::1", ("::1", None)),
        ("http://localhost:8080/path?q=1", ("localhost", 8080)),
        ("https://Example.com", ("example.com", 443)),
        ("fe80::1%eth0", ("fe80::1%eth0", None)),
        ("0:0:0:0:0:0:0:1", ("::1", None)),
        ("Example.COM.:8080", ("example.com", 8080)),
    ],
)
def test_split_domain(value, expected):
    from pywrkr.har_import import _split_domain

    assert _split_domain(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "localhost:99999",
        "host:abc",
        "http://localhost:99999/",
        "host:80:90",
        ":8080",
        "",
        "  ",
        "localhost:+80",
        "localhost:",
        "localhost:0",
        "localhost:\u0668\u0660",
    ],
)
def test_split_domain_rejects_a_bad_port(value):
    from pywrkr.har_import import _split_domain

    with pytest.raises(ValueError, match="expected host or host:port"):
        _split_domain(value)


def test_domain_filter_accepts_a_pasted_url():
    assert _kept(["http://localhost:8080/"]) == ["http://localhost:8080/x"]


def test_domain_filter_matches_an_ip_address_exactly():
    entries = [_entry("http://127.0.0.1:8080/a"), _entry("http://10.0.0.1/b")]
    kept = filter_entries(entries, HarImportConfig(allowed_domains=["0.0.1"]))
    assert kept == []
    kept = filter_entries(entries, HarImportConfig(allowed_domains=["127.0.0.1"]))
    assert [e.url for e in kept] == ["http://127.0.0.1:8080/a"]
    # Nor does an IP --domain take in a name ending in it.
    kept = filter_entries(
        [_entry("http://foo.10.0.0.1/")], HarImportConfig(allowed_domains=["10.0.0.1"])
    )
    assert kept == []


@pytest.mark.parametrize(
    ("domain", "url"),
    [
        ("0:0:0:0:0:0:0:1", "http://[::1]:8080/x"),
        ("fe80::1%eth0", "http://[fe80::1%25eth0]/x"),
        ("example.com", "http://Example.com./x"),
        ("example.com.", "http://example.com/x"),
    ],
)
def test_domain_filter_compares_canonical_hosts(domain, url):
    kept = filter_entries([_entry(url)], HarImportConfig(allowed_domains=[domain]))
    assert [e.url for e in kept] == [url]


@pytest.mark.parametrize(
    "urls",
    [
        ["http://example.com/a", "http://example.com./b"],
        ["http://[::1]:8080/a", "http://[0:0:0:0:0:0:0:1]:8080/b"],
    ],
)
def test_host_spellings_the_filter_merges_are_one_origin(urls):
    domain = "example.com" if "example" in urls[0] else "::1"
    kept = filter_entries([_entry(u) for u in urls], HarImportConfig(allowed_domains=[domain]))
    assert len(kept) == 2
    scenario = har_to_scenario(kept, HarImportConfig())
    assert len(scenario["steps"]) == 2


def test_default_port_spelled_out_is_one_origin():
    """--domain example.com:80 keeps both spellings; the scenario must accept them."""
    entries = [_entry("http://example.com/a"), _entry("http://Example.com:80/b")]
    kept = filter_entries(entries, HarImportConfig(allowed_domains=["example.com:80"]))
    assert len(kept) == 2
    scenario = har_to_scenario(kept, HarImportConfig())
    assert len(scenario["steps"]) == 2


def test_empty_filter_hint_strips_userinfo_escapes_and_caps(tmp_path):
    urls = ["http://user:s3cret@h0.test/x?token=abc", "http://h\x1bx.test/"] + [
        f"http://h{i}.test/" for i in range(1, 12)
    ]
    path = _write_har(
        tmp_path,
        {
            "entries": [
                {"request": {"url": u, "method": "GET"}, "response": {"status": 200}} for u in urls
            ]
        },
    )
    with pytest.raises(ValueError) as exc:
        convert_har(path, config=HarImportConfig(allowed_domains=["nowhere.test"]))
    message = str(exc.value)
    assert "s3cret" not in message and "token" not in message
    assert "\x1b" not in message
    assert "h10.test" not in message
    assert message.endswith(" ....")


def test_empty_filter_hint_only_when_domain_emptied_it(tmp_path):
    path = _write_har(
        tmp_path,
        {
            "entries": [
                {
                    "request": {"url": "http://localhost:8080/app.css", "method": "GET"},
                    "response": {"status": 200},
                }
            ]
        },
    )
    with pytest.raises(ValueError) as exc:
        convert_har(path, config=HarImportConfig(allowed_domains=["localhost"]))
    assert "--domain was" not in str(exc.value)


def test_domain_filter_pasted_https_url_means_that_origin():
    """https://example.com is the https origin, not http entries as well."""
    entries = [_entry("http://example.com/a"), _entry("https://example.com/b")]
    kept = filter_entries(entries, HarImportConfig(allowed_domains=["https://example.com"]))
    assert [e.url for e in kept] == ["https://example.com/b"]


def test_userinfo_keeps_origins_apart():
    """Merging http://user:pw@h with http://h would replay the credentials on every step."""
    entries = [_entry("http://user:pw@h.test/a"), _entry("http://h.test/b")]
    with pytest.raises(ValueError, match="spans multiple hosts"):
        har_to_scenario(entries, HarImportConfig())


def test_empty_filter_hint_brackets_ipv6(tmp_path):
    path = _write_har(
        tmp_path,
        {
            "entries": [
                {
                    "request": {"url": "http://[::1]:8080/x", "method": "GET"},
                    "response": {"status": 200},
                }
            ]
        },
    )
    with pytest.raises(ValueError) as exc:
        convert_har(path, config=HarImportConfig(allowed_domains=["[::1]:9000"]))
    assert "[::1]:8080" in str(exc.value)


def test_cli_reports_a_bad_domain_cleanly(tmp_path, capsys):
    from pywrkr.main import main

    path = _write_har(
        tmp_path,
        {
            "entries": [
                {"request": {"url": "http://h/x", "method": "GET"}, "response": {"status": 200}}
            ]
        },
    )
    with patch.object(sys, "argv", ["pywrkr", "har-import", path, "--domain", "localhost:99999"]):
        with pytest.raises(SystemExit) as exc:
            main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "expected host or host:port" in err
    assert "Traceback" not in err
