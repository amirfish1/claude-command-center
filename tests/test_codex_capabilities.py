import copy
from types import SimpleNamespace

import pytest

import ccc_server.codex_capabilities as capabilities

from ccc_server.codex_capabilities import (
    catalog_from_schemas,
    codex_method_is_mutating,
    get_codex_catalog,
    validate_codex_operation,
    validate_schema,
)


DEFINITIONS = {
    "ListParams": {
        "type": "object",
        "properties": {
            "cursor": {"$ref": "#/definitions/Cursor"},
        },
        "additionalProperties": False,
    },
    "Cursor": {"type": ["string", "null"]},
    "MutationParams": {
        "type": "object",
        "properties": {"id": {"type": "string", "minLength": 1}},
        "required": ["id"],
        "additionalProperties": False,
    },
}


def _variant(method, params_ref="ListParams", description=None, params_schema=None):
    variant = {
        "type": "object",
        "properties": {
            "method": {"type": "string", "enum": [method]},
            "params": (
                copy.deepcopy(params_schema)
                if params_schema is not None
                else {"$ref": f"#/definitions/{params_ref}"}
            ),
        },
        "required": ["method", "params"],
    }
    if description:
        variant["description"] = description
    return variant


def _direction(*variants):
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "oneOf": list(variants),
        "definitions": copy.deepcopy(DEFINITIONS),
    }


def _schemas(*, experimental=False):
    requests = [
        _variant("thread/list"),
        _variant("thread/name/set", "MutationParams"),
        _variant("thread/inject_items", "MutationParams"),
        _variant("turn/start", "MutationParams"),
        _variant("plugin/list"),
        _variant("initialize", "MutationParams"),
        _variant("account/rateLimits/read", params_schema={"type": "null"}),
        _variant(
            "account/usage/read",
            params_schema={
                "anyOf": [
                    {"$ref": "#/definitions/ListParams"},
                    {"type": "null"},
                ]
            },
        ),
    ]
    if experimental:
        requests.extend(
            [
                _variant("project/list"),
                _variant("mock/experimentalMethod", "MutationParams"),
            ]
        )
    schemas = {
        "ClientRequest": _direction(*requests),
        "ClientNotification": _direction(_variant("initialized", "MutationParams")),
        "ServerRequest": _direction(
            _variant("item/tool/requestUserInput", "MutationParams"),
            _variant("attestation/generate", "MutationParams"),
        ),
        "ServerNotification": _direction(
            _variant("thread/started", "MutationParams"),
            *(
                [_variant("project/changed", "MutationParams")]
                if experimental
                else []
            ),
        ),
    }
    if experimental:
        schemas["ClientRequest"]["definitions"]["MutationParams"]["properties"][
            "previewField"
        ] = {"type": "boolean"}
    return schemas


def _by_method(records):
    return {record["method"]: record for record in records}


def test_catalog_enumerates_all_protocol_directions_and_marks_preview_methods():
    catalog = catalog_from_schemas(_schemas(), _schemas(experimental=True), "0.153.4")

    methods = _by_method(catalog["methods"])
    server_requests = _by_method(catalog["server_requests"])
    notifications = {
        (record["direction"], record["method"]): record
        for record in catalog["notifications"]
    }

    assert catalog["ok"] is True
    assert catalog["version"] == "0.153.4"
    assert len(catalog["fingerprint"]) == 64
    assert set(methods) == {
        "initialize",
        "mock/experimentalMethod",
        "plugin/list",
        "project/list",
        "account/rateLimits/read",
        "account/usage/read",
        "thread/list",
        "thread/inject_items",
        "thread/name/set",
        "turn/start",
    }
    assert methods["thread/list"] == {
        **methods["thread/list"],
        "method": "thread/list",
        "group": "Conversations",
        "scope": "thread",
        "read_only": True,
        "experimental": False,
        "available": True,
        "unavailable_reason": None,
    }
    assert methods["thread/list"]["params_type"] == "object"
    assert methods["account/rateLimits/read"]["params_type"] == "null"
    assert methods["account/rateLimits/read"]["params_schema"]["type"] == "object"
    assert methods["account/rateLimits/read"]["params_schema"]["additionalProperties"] is False
    assert methods["account/usage/read"]["params_type"] == "object"
    assert methods["account/usage/read"]["params_schema"]["type"] == "object"
    assert methods["project/list"]["experimental"] is True
    assert methods["project/list"]["available"] is False
    assert "Preview features" in methods["project/list"]["unavailable_reason"]
    assert methods["plugin/list"]["experimental"] is True
    assert methods["plugin/list"]["available"] is False
    assert methods["initialize"]["available"] is False
    assert methods["thread/inject_items"]["available"] is False
    assert methods["mock/experimentalMethod"]["available"] is False
    assert set(server_requests) == {
        "attestation/generate",
        "item/tool/requestUserInput",
    }
    assert server_requests["attestation/generate"]["available"] is False
    assert set(notifications) == {
        ("client_notification", "initialized"),
        ("server_notification", "project/changed"),
        ("server_notification", "thread/started"),
    }


def test_descriptor_has_human_metadata_and_a_self_contained_parameter_schema():
    descriptor = _by_method(
        catalog_from_schemas(_schemas(), _schemas(experimental=True))["methods"]
    )["thread/list"]

    assert descriptor["title"] == "List Conversations"
    assert "/" not in descriptor["title"]
    assert descriptor["params_schema"]["type"] == "object"
    assert descriptor["params_schema"]["title"] == "List Params"
    assert descriptor["params_schema"]["definitions"] == {
        "Cursor": {"type": ["string", "null"]}
    }
    assert descriptor["params_schema"]["properties"]["cursor"] == {
        "$ref": "#/definitions/Cursor"
    }


def test_catalog_constructor_does_not_mutate_input_schemas():
    stable = _schemas()
    full = _schemas(experimental=True)
    expected_stable = copy.deepcopy(stable)
    expected_full = copy.deepcopy(full)

    catalog_from_schemas(stable, full)

    assert stable == expected_stable
    assert full == expected_full


def test_mutating_classification_is_conservative():
    safe_reads = [
        "account/read",
        "fs/readFile",
        "fuzzyFileSearch",
        "model/list",
        "plugin/read",
        "project/list",
        "server/diagnostics",
        "thread/goal/get",
        "thread/list",
        "thread/queue/list",
        "thread/read",
    ]
    mutations = [
        "account/sendAddCreditsNudgeEmail",
        "account/rateLimitResetCredit/consume",
        "command/exec",
        "config/value/write",
        "mcpServer/oauth/login",
        "plugin/install",
        "thread/archive",
        "thread/goal/set",
        "thread/start",
        "turn/interrupt",
        "unknown/future/read",
    ]

    assert all(not codex_method_is_mutating(method) for method in safe_reads)
    assert all(codex_method_is_mutating(method) for method in mutations)


VALIDATION_SCHEMA = {
    "type": "object",
    "definitions": {
        "Target": {
            "oneOf": [
                {
                    "type": "object",
                    "properties": {
                        "kind": {"const": "file"},
                        "path": {"type": "string", "minLength": 1, "maxLength": 20},
                    },
                    "required": ["kind", "path"],
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "properties": {
                        "kind": {"const": "lines"},
                        "lines": {
                            "type": "array",
                            "items": {
                                "type": "integer",
                                "minimum": 0,
                                "maximum": 100,
                            },
                            "minItems": 1,
                            "maxItems": 3,
                        },
                    },
                    "required": ["kind", "lines"],
                    "additionalProperties": False,
                },
            ]
        }
    },
    "properties": {
        "name": {"type": "string", "minLength": 2, "pattern": "^[a-z]+$"},
        "mode": {"enum": ["fast", "careful"]},
        "target": {
            "anyOf": [
                {"$ref": "#/definitions/Target"},
                {"type": "null"},
            ]
        },
        "ratio": {
            "allOf": [
                {"type": "number", "minimum": 0},
                {"maximum": 1},
            ]
        },
        "count": {"type": "integer"},
    },
    "required": ["name", "mode", "target", "count"],
    "additionalProperties": False,
}


def test_validate_schema_accepts_refs_unions_nested_arrays_and_constraints():
    validate_schema(
        {
            "name": "alpha",
            "mode": "careful",
            "target": {"kind": "lines", "lines": [0, 5, 100]},
            "ratio": 0.5,
            "count": 3,
        },
        VALIDATION_SCHEMA,
    )
    validate_schema(
        {"name": "ok", "mode": "fast", "target": None, "count": 0},
        VALIDATION_SCHEMA,
    )


@pytest.mark.parametrize(
    "value,error_fragment",
    [
        ({"mode": "fast", "target": None, "count": 1}, "required"),
        (
            {"name": "ok", "mode": "fast", "target": None, "count": 1, "extra": 2},
            "unrecognized",
        ),
        ({"name": "O", "mode": "fast", "target": None, "count": 1}, "length"),
        ({"name": "Okay", "mode": "fast", "target": None, "count": 1}, "pattern"),
        ({"name": "ok", "mode": "turbo", "target": None, "count": 1}, "allowed"),
        (
            {
                "name": "ok",
                "mode": "fast",
                "target": {"kind": "lines", "lines": [101]},
                "count": 1,
            },
            "maximum",
        ),
        ({"name": "ok", "mode": "fast", "target": None, "ratio": -0.1, "count": 1}, "minimum"),
        ({"name": "ok", "mode": "fast", "target": None, "count": True}, "integer"),
    ],
)
def test_validate_schema_rejects_invalid_values(value, error_fragment):
    with pytest.raises(ValueError, match=error_fragment):
        validate_schema(value, VALIDATION_SCHEMA)


def test_validate_schema_rejects_unsupported_safety_constraints_and_cycles():
    with pytest.raises(ValueError, match="unsupported schema keyword.*contains"):
        validate_schema([], {"type": "array", "contains": {"const": 1}})

    cyclic = {
        "$ref": "#/definitions/A",
        "definitions": {
            "A": {"$ref": "#/definitions/B"},
            "B": {"$ref": "#/definitions/A"},
        },
    }
    with pytest.raises(ValueError, match="cycle"):
        validate_schema({}, cyclic)

    with pytest.raises(ValueError, match="malformed minLength"):
        validate_schema("x", {"type": "string", "minLength": "1"})


def test_validate_schema_bounds_collections_and_error_text_does_not_echo_values():
    with pytest.raises(ValueError, match="too many items"):
        validate_schema([None] * 10_001, {"type": "array", "items": {}})

    secret = "private-token-value"
    with pytest.raises(ValueError) as exc_info:
        validate_schema(secret, {"type": "string", "enum": ["safe"]})
    assert secret not in str(exc_info.value)

    for non_finite in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError, match="finite"):
            validate_schema(non_finite, {"type": "number"})


def test_validate_codex_operation_returns_descriptor_only_when_permitted_and_valid():
    catalog = catalog_from_schemas(_schemas(), _schemas(experimental=True))

    descriptor = validate_codex_operation("thread/list", {"cursor": None}, catalog)
    assert descriptor["method"] == "thread/list"
    assert validate_codex_operation("account/rateLimits/read", {}, catalog)[
        "params_type"
    ] == "null"

    with pytest.raises(ValueError, match="Unknown Codex method"):
        validate_codex_operation("account/read/private-token-value", {}, catalog)
    with pytest.raises(ValueError, match="Managed internally"):
        validate_codex_operation("initialize", {"id": "x"}, catalog)
    with pytest.raises(ValueError, match="parameters must be an object"):
        validate_codex_operation("thread/list", [], catalog)


def test_validate_codex_operation_reports_a_safe_parameter_error():
    catalog = catalog_from_schemas(_schemas(), _schemas(experimental=True))
    supplied_secret = "private-token-value"

    with pytest.raises(ValueError) as exc_info:
        validate_codex_operation(
            "thread/name/set", {"id": supplied_secret, "extra": supplied_secret}, catalog
        )

    message = str(exc_info.value)
    assert "Invalid parameters for Set Conversation Name" in message
    assert supplied_secret not in message


def test_catalog_cache_uses_loader_once_for_memory_and_disk_warm_reads(tmp_path):
    executable = tmp_path / "codex"
    executable.write_text("first", encoding="utf-8")
    executable.chmod(0o700)
    cache_dir = tmp_path / "cache"
    resolved = {"available": True, "bin": str(executable)}
    calls = []

    def loader(executable_path):
        calls.append(executable_path)
        return _schemas(), _schemas(experimental=True), "0.153.4"

    capabilities._CATALOG_MEMORY.clear()
    first = capabilities._load_or_build_catalog(resolved, loader, cache_dir)
    second = capabilities._load_or_build_catalog(resolved, loader, cache_dir)
    capabilities._CATALOG_MEMORY.clear()
    third = capabilities._load_or_build_catalog(resolved, loader, cache_dir)

    assert first["fingerprint"] == second["fingerprint"] == third["fingerprint"]
    assert calls == [str(executable)]
    assert cache_dir.stat().st_mode & 0o777 == 0o700
    assert all(cache_file.stat().st_mode & 0o777 == 0o600 for cache_file in cache_dir.iterdir())


def test_catalog_cache_invalidates_when_the_executable_changes(tmp_path):
    executable = tmp_path / "codex"
    executable.write_text("first", encoding="utf-8")
    executable.chmod(0o700)
    resolved = {"available": True, "bin": str(executable)}
    calls = []

    def loader(executable_path):
        calls.append(executable_path)
        return _schemas(), _schemas(experimental=True), f"version-{len(calls)}"

    capabilities._CATALOG_MEMORY.clear()
    first = capabilities._load_or_build_catalog(resolved, loader, tmp_path / "cache")
    executable.write_text("second-and-different", encoding="utf-8")
    second = capabilities._load_or_build_catalog(resolved, loader, tmp_path / "cache")

    assert first["version"] == "version-1"
    assert second["version"] == "version-2"
    assert len(calls) == 2


def test_get_catalog_resolves_at_call_time_and_applies_preview_opt_in_without_reload(
    monkeypatch, tmp_path
):
    executable = tmp_path / "codex"
    executable.write_text("binary", encoding="utf-8")
    executable.chmod(0o700)
    resolutions = []
    loads = []

    def resolve():
        resolutions.append(True)
        return {"available": True, "bin": str(executable)}

    def loader(executable_path):
        loads.append(executable_path)
        return _schemas(), _schemas(experimental=True), "0.153.4"

    monkeypatch.setattr(capabilities, "_core", SimpleNamespace(_resolve_codex_bin=resolve))
    monkeypatch.setattr(capabilities, "_SCHEMA_LOADER", loader)
    monkeypatch.setattr(capabilities, "_CACHE_DIR", tmp_path / "cache")
    monkeypatch.delenv("CCC_CODEX_EXPERIMENTAL", raising=False)
    capabilities._CATALOG_MEMORY.clear()

    ordinary = get_codex_catalog()
    monkeypatch.setenv("CCC_CODEX_EXPERIMENTAL", "1")
    preview = get_codex_catalog()

    ordinary_methods = _by_method(ordinary["methods"])
    preview_methods = _by_method(preview["methods"])
    assert ordinary_methods["project/list"]["available"] is False
    assert preview_methods["project/list"]["available"] is True
    assert preview_methods["project/list"]["unavailable_reason"] is None
    assert preview_methods["mock/experimentalMethod"]["available"] is False
    assert preview_methods["initialize"]["available"] is False
    with pytest.raises(ValueError, match="unrecognized"):
        validate_codex_operation("turn/start", {"id": "x", "previewField": True}, ordinary)
    validate_codex_operation(
        "turn/start", {"id": "x", "previewField": True}, preview
    )
    assert len(resolutions) == 2
    assert loads == [str(executable)]


def test_get_catalog_returns_a_bounded_error_when_codex_is_unavailable(monkeypatch):
    monkeypatch.setattr(
        capabilities,
        "_core",
        SimpleNamespace(
            _resolve_codex_bin=lambda: {
                "available": False,
                "bin": None,
                "reason": "unavailable " + ("private-token-value " * 100),
            }
        ),
    )

    catalog = get_codex_catalog()

    assert catalog["ok"] is False
    assert catalog["methods"] == []
    assert catalog["server_requests"] == []
    assert catalog["notifications"] == []
    assert len(catalog["error"]) <= 240
    assert "private-token-value" not in catalog["error"]


def test_advanced_injection_is_available_with_explicit_preview_opt_in():
    base = catalog_from_schemas(_schemas(), _schemas(experimental=True))
    preview = capabilities._catalog_with_runtime_availability(base, True)
    assert _by_method(preview["methods"])["thread/inject_items"]["available"]


def test_experimental_param_description_requires_opt_in_even_in_default_schema():
    schema = _direction(_variant("app/list"))
    schema["definitions"]["ListParams"]["description"] = "EXPERIMENTAL - available app integrations"
    result = catalog_from_schemas({"ClientRequest": schema}, {"ClientRequest": schema})
    method = result["methods"][0]
    assert method["experimental"]
    assert not method["available"]


@pytest.mark.parametrize("fmt,value", [("uint16", 65536), ("uint32", 2**32), ("int64", 2**63), ("uint64", -1), ("uint", 2**128)])
def test_integer_wire_widths_are_enforced(fmt, value):
    with pytest.raises(ValueError):
        validate_schema(value, {"type": "integer", "format": fmt})


def test_open_values_cannot_bypass_structural_limits():
    with pytest.raises(ValueError):
        validate_schema({"open": [None] * 10001}, {"type": "object"})
    deep = None
    for _ in range(1000):
        deep = {"nested": deep}
    with pytest.raises(ValueError):
        validate_schema(deep, True)


def test_invalid_unicode_has_a_safe_validation_error():
    for value in ("\ud800", {"\ud800": "value"}):
        with pytest.raises(ValueError, match="invalid Unicode sequence") as error:
            validate_schema(value, True)
        assert type(error.value) is ValueError


def test_platform_actions_follow_actual_host_on_warm_catalog(monkeypatch):
    schema = _direction(_variant("windowsSandbox/setupStart"))
    base = catalog_from_schemas({"ClientRequest": schema}, {"ClientRequest": schema})
    monkeypatch.setattr(capabilities, "_HOST_PLATFORM", "darwin")
    assert not capabilities._catalog_with_runtime_availability(base, True)["methods"][0]["available"]
    monkeypatch.setattr(capabilities, "_HOST_PLATFORM", "win32")
    assert capabilities._catalog_with_runtime_availability(base, True)["methods"][0]["available"]


def test_unique_items_uses_linear_canonical_comparisons(monkeypatch):
    comparisons = 0
    original = capabilities._json_equal
    def counted(a, b):
        nonlocal comparisons
        comparisons += 1
        return original(a, b)
    monkeypatch.setattr(capabilities, "_json_equal", counted)
    validate_schema(list(range(1500)), {"type": "array", "uniqueItems": True})
    assert comparisons < 3000
    with pytest.raises(ValueError):
        validate_schema([1, 1.0], {"type": "array", "uniqueItems": True})
    validate_schema([True, 1], {"type": "array", "uniqueItems": True})
