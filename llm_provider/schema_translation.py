"""Per-provider JSON-schema translation (requirements.md Sec 9.3 / LLM-007, D-005).

Pydantic models are the canonical structured-output contract (D-005). Provider-specific schema
translation belongs entirely inside this app -- pipeline/domain code must never contain
Gemini/OpenAI/NVIDIA-specific schema workarounds.
"""

from __future__ import annotations

import copy
from typing import Any

from pydantic import BaseModel


def _resolve_refs(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        if "$ref" in node:
            key = node["$ref"].rsplit("/", 1)[-1]
            return _resolve_refs(copy.deepcopy(defs.get(key, {})), defs)
        return {key: _resolve_refs(value, defs) for key, value in node.items() if key != "$defs"}
    if isinstance(node, list):
        return [_resolve_refs(item, defs) for item in node]
    return node


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline all `$ref`/`$defs` -- Gemini's reduced schema dialect does not support them."""
    defs = schema.get("$defs", {})
    return _resolve_refs(schema, defs)


def strip_enums(schema: Any) -> Any:
    """Recursively remove `enum` constraints.

    Accumulating too many `enum` constraints across schema fields has been observed to cause
    opaque 400 errors from Gemini. The safe pattern is to drop `enum` constraints from the
    schema sent to Gemini and instead re-validate the returned values against the allowed set
    server-side afterward (BaseLLMAdapter.generate() does this uniformly via
    `output_schema.model_validate(...)`, which enforces Literal/Enum fields regardless of what
    was sent to the provider).
    """
    if isinstance(schema, dict):
        return {key: strip_enums(value) for key, value in schema.items() if key != "enum"}
    if isinstance(schema, list):
        return [strip_enums(item) for item in schema]
    return schema


_JSON_TYPE_TO_GEMINI_TYPE = {
    "string": "STRING",
    "number": "NUMBER",
    "integer": "INTEGER",
    "boolean": "BOOLEAN",
    "array": "ARRAY",
    "object": "OBJECT",
}

# Keys that are part of standard JSON Schema / Pydantic output but not part of Gemini's reduced
# OpenAPI-subset dialect.
_GEMINI_UNSUPPORTED_KEYS = {"title", "additionalProperties", "$schema"}


def _convert_for_gemini(node: Any) -> Any:
    if isinstance(node, dict):
        converted: dict[str, Any] = {}
        for key, value in node.items():
            if key in _GEMINI_UNSUPPORTED_KEYS:
                continue
            if key == "type" and isinstance(value, str) and value in _JSON_TYPE_TO_GEMINI_TYPE:
                converted[key] = _JSON_TYPE_TO_GEMINI_TYPE[value]
            else:
                converted[key] = _convert_for_gemini(value)
        return converted
    if isinstance(node, list):
        return [_convert_for_gemini(item) for item in node]
    return node


def to_gemini_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Translate a canonical Pydantic model into Gemini's reduced schema dialect: no
    `$ref`/`$defs`, no `enum` constraints, OpenAPI-style uppercase type names."""
    raw = model.model_json_schema()
    return _convert_for_gemini(strip_enums(inline_refs(raw)))


class OpenAIStrictSchemaContractError(Exception):
    """A generated OpenAI-compatible strict-Structured-Outputs schema is not contract-complete --
    some object node's `required` does not list every key in its `properties`, or
    `additionalProperties` is not `false` (D-032). This is a local configuration/request-contract
    problem in *our own* schema generation, caught deterministically before any network call --
    never a provider response issue, and never retried. If this ever fires, `_complete_object_
    contract` below has a bug or encountered a Pydantic model shape it does not yet handle.
    """


def _complete_object_contract(node: Any) -> Any:
    """Recursively walk a JSON-Schema-shaped structure -- the root object, `$defs`, nested
    objects, array item objects, and everything reachable through `$ref` -- and, for every node
    describing an object (`type == "object"` with a `properties` mapping), set
    `additionalProperties: False` and `required` to *every* key in `properties`, in the same
    order `properties` already has them.

    OpenAI/NVIDIA/OpenRouter strict Structured Outputs all require this: "all fields must be
    required" is OpenAI's own documented rule (developers.openai.com/api/docs/guides/structured-
    outputs) -- there is no way to mark a property merely "optional" other than to fold `null`
    into its own type. Pydantic, by contrast, only lists a field in JSON Schema `required` when it
    has no default -- so a field like `diagnostic_terms: list[...] = Field(default_factory=list)`
    is silently absent from `required`, which is exactly what produced the confirmed
    `RequirementNormalizationOutput` HTTP 400 (D-032): OpenAI rejects a strict-mode schema whose
    `required` is incomplete before generation ever starts.

    A field with a non-null default (an empty list, `""`, a fixed string) keeps its original,
    non-nullable type here -- the provider must always supply a value, and the prompt/schema
    guides the model to use the empty/default shape when there is nothing to report. A field that
    was already nullable (`Optional[X] = None`) is untouched beyond being added to `required` --
    its schema already expresses "no value" via a `null` variant in `anyOf`, which is the
    officially documented way OpenAI represents an optional field in strict mode.
    """
    if isinstance(node, dict):
        converted = {key: _complete_object_contract(value) for key, value in node.items()}
        if converted.get("type") == "object":
            converted["additionalProperties"] = False
            if isinstance(converted.get("properties"), dict):
                converted["required"] = list(converted["properties"].keys())
        return converted
    if isinstance(node, list):
        return [_complete_object_contract(item) for item in node]
    return node


def _assert_object_contract_complete(node: Any, path: str = "$") -> None:
    """Local invariant check (D-032, Phase C): raises `OpenAIStrictSchemaContractError` before
    any HTTP call if any object node's `required` does not exactly equal `list(properties.keys())`
    (order-sensitive, matching `_complete_object_contract`'s own construction), or
    `additionalProperties` is not `False`. A defensive self-check on our own generated output --
    not a condition that depends on request/response content -- so any caller can safely treat a
    failure here as CONFIGURATION and never retry it.
    """
    if isinstance(node, dict):
        if node.get("type") == "object" and isinstance(node.get("properties"), dict):
            prop_keys = list(node["properties"].keys())
            required = node.get("required")
            if required != prop_keys or node.get("additionalProperties") is not False:
                raise OpenAIStrictSchemaContractError(
                    f"{path}: strict-mode object contract incomplete -- properties={prop_keys!r} "
                    f"required={required!r} additionalProperties={node.get('additionalProperties')!r}"
                )
        for key, value in node.items():
            _assert_object_contract_complete(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, item in enumerate(node):
            _assert_object_contract_complete(item, f"{path}[{index}]")


# OpenAI's officially documented "Supported properties" for a `string`-typed Structured Outputs
# schema node are only `pattern` and `format` (developers.openai.com/api/docs/guides/structured-
# outputs, "Supported properties" / "Some type-specific keywords are not yet supported";
# confirmed against the live doc 2026-09-05). `minLength`/`maxLength` are never listed there for
# any model -- so they are stripped from the OpenAI-bound schema only (D-032). This project's only
# current use (`candidate_matching.schemas.BoundedTerm`, `MAX_TEXT_CHARS`) keeps being enforced by
# the canonical Pydantic model post-response (D-005, unchanged), by the AC_NORMALIZE prompt's own
# stated character limit, and by NVIDIA/OpenRouter's outbound schema (see
# `to_openai_compatible_strict_schema` below), which is not confirmed to reject either keyword.
_OPENAI_UNSUPPORTED_STRING_LENGTH_KEYS = frozenset({"minLength", "maxLength"})


def _strip_keys(node: Any, keys: frozenset[str]) -> Any:
    if isinstance(node, dict):
        return {key: _strip_keys(value, keys) for key, value in node.items() if key not in keys}
    if isinstance(node, list):
        return [_strip_keys(item, keys) for item in node]
    return node


def to_openai_strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Translate a canonical Pydantic model into OpenAI's own strict-Structured-Outputs dialect
    (D-032).

    OpenAI supports `$ref`/`$defs` and `enum` directly, so those are left as-is. Strict mode
    additionally requires, on every object node (root, `$defs`, nested, array-item, and anything
    reached through `$ref`): `additionalProperties: false`, and `required` listing every key in
    `properties` (`_complete_object_contract`). `minLength`/`maxLength` are also stripped here --
    never part of OpenAI's documented supported subset (`_OPENAI_UNSUPPORTED_STRING_LENGTH_KEYS`).
    `_assert_object_contract_complete` re-checks the result before returning, so a violation is
    caught locally, before any network call, rather than surfacing as a provider-side 400.
    """
    raw = model.model_json_schema()
    schema = _complete_object_contract(raw)
    schema = _strip_keys(schema, _OPENAI_UNSUPPORTED_STRING_LENGTH_KEYS)
    _assert_object_contract_complete(schema)
    return schema


def to_openai_compatible_strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Translate a canonical Pydantic model into the strict-Structured-Outputs dialect used by
    NVIDIA NIM and OpenRouter (D-032): both are OpenAI-compatible `/chat/completions` endpoints
    that accept the same `response_format: {type: json_schema, strict: true}` shape as OpenAI, so
    they need the identical `required`-completion and `additionalProperties: false` fix
    `to_openai_strict_schema` applies (`_complete_object_contract`).

    Unlike `to_openai_strict_schema`, this function does **not** strip `minLength`/`maxLength`:
    neither provider is confirmed (by their own documentation or an observed error) to reject
    those keywords, and this project currently relies on NVIDIA/OpenRouter enforcing them
    server-side (`candidate_matching.tests.test_normalize.ProviderFacingSchemaContractTests`).
    Only OpenAI has a confirmed, documented gap here. If either provider is later confirmed to
    reject a keyword OpenAI already excludes, add a provider-specific strip set here -- never
    reuse `_OPENAI_UNSUPPORTED_STRING_LENGTH_KEYS` for a different provider without checking that
    provider's own documented contract first.
    """
    raw = model.model_json_schema()
    schema = _complete_object_contract(raw)
    _assert_object_contract_complete(schema)
    return schema
