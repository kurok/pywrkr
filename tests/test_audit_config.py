"""Regression tests for audit finding cfg-1: load_scenario value-type validation.

Before the fix, load_scenario validated only structure (dict, non-empty 'steps'
list, each step a dict with a 'path'). Wrong-typed values (body as int/float/bool,
headers as a list, path/method as int, think_time as str) were accepted verbatim
and propagated into the worker, crashing far from the config file. These tests
assert that load_scenario now raises a clear ValueError naming the offending
step/field at load time.

Note: list bodies are valid (HAR import produces them for JSON-array payloads).
"""

import json

import pytest

from pywrkr.config import load_scenario


def _write_scenario(tmp_path, steps, **extra):
    data = {"steps": steps, **extra}
    p = tmp_path / "scenario.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


@pytest.mark.parametrize(
    "bad_body",
    [
        42,  # int
        3.14,  # float
        True,  # bool (subclass of int)
    ],
)
def test_body_wrong_type_raises(tmp_path, bad_body):
    path = _write_scenario(tmp_path, [{"path": "/", "body": bad_body}])
    with pytest.raises(
        ValueError, match=r"Step 0 'body' must be a string, object, array, or null"
    ):
        load_scenario(path)


def test_body_str_dict_list_accepted(tmp_path):
    path = _write_scenario(
        tmp_path,
        [
            {"path": "/a", "body": "raw string"},
            {"path": "/b", "body": {"key": "value"}},
            {"path": "/c", "body": [1, 2, 3]},
            {"path": "/d"},  # missing body -> None, still fine
        ],
    )
    scenario = load_scenario(path)
    assert scenario.steps[0].body == "raw string"
    assert scenario.steps[1].body == {"key": "value"}
    assert scenario.steps[2].body == [1, 2, 3]
    assert scenario.steps[3].body is None


def test_headers_wrong_type_raises(tmp_path):
    path = _write_scenario(tmp_path, [{"path": "/", "headers": ["X-Foo: bar"]}])
    with pytest.raises(ValueError, match=r"Step 0 'headers' must be an object"):
        load_scenario(path)


def test_path_wrong_type_raises(tmp_path):
    path = _write_scenario(tmp_path, [{"path": 42}])
    with pytest.raises(ValueError, match=r"Step 0 'path' must be a string"):
        load_scenario(path)


def test_method_wrong_type_raises(tmp_path):
    path = _write_scenario(tmp_path, [{"path": "/", "method": 123}])
    with pytest.raises(ValueError, match=r"Step 0 'method' must be a string"):
        load_scenario(path)


def test_think_time_wrong_type_raises(tmp_path):
    path = _write_scenario(tmp_path, [{"path": "/", "think_time": "soon"}])
    with pytest.raises(ValueError, match=r"Step 0 'think_time' must be a number or null"):
        load_scenario(path)


def test_think_time_bool_rejected(tmp_path):
    # bool is a subclass of int but is not a meaningful think_time value.
    path = _write_scenario(tmp_path, [{"path": "/", "think_time": True}])
    with pytest.raises(ValueError, match=r"Step 0 'think_time' must be a number or null"):
        load_scenario(path)


def test_think_time_numeric_still_accepted(tmp_path):
    path = _write_scenario(
        tmp_path,
        [
            {"path": "/a", "think_time": 2},
            {"path": "/b", "think_time": 1.5},
            {"path": "/c"},  # missing think_time -> None
        ],
    )
    scenario = load_scenario(path)
    assert scenario.steps[0].think_time == 2
    assert scenario.steps[1].think_time == 1.5
    assert scenario.steps[2].think_time is None


def test_error_message_names_correct_step_index(tmp_path):
    # First step is valid; the bad value is in step index 1.
    path = _write_scenario(
        tmp_path,
        [
            {"path": "/ok", "body": "fine"},
            {"path": "/", "body": 42},
        ],
    )
    with pytest.raises(
        ValueError, match=r"Step 1 'body' must be a string, object, array, or null"
    ):
        load_scenario(path)


def test_load_scenario_logs_file_path_not_step_path(caplog):
    # cfg-1 follow-up: the per-step `path` local must not shadow the function's
    # `path` parameter, which is reused in the trailing
    # "Loaded scenario ... from %s" log message.
    import logging
    import os
    import tempfile

    scenario = {
        "name": "S",
        "base_url": "http://h",
        "steps": [{"path": "/LAST-STEP-PATH", "method": "GET"}],
    }
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(scenario, f)
    f.close()
    try:
        with caplog.at_level(logging.INFO):
            load_scenario(f.name)
        msgs = " ".join(r.getMessage() for r in caplog.records)
        assert f.name in msgs
        assert "/LAST-STEP-PATH" not in msgs
    finally:
        os.unlink(f.name)
