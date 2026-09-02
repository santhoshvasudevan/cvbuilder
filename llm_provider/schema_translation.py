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


def _require_additional_properties_false(node: Any) -> Any:
    if isinstance(node, dict):
        converted = {key: _require_additional_properties_false(value) for key, value in node.items()}
        if converted.get("type") == "object":
            converted.setdefault("additionalProperties", False)
        return converted
    if isinstance(node, list):
        return [_require_additional_properties_false(item) for item in node]
    return node


def to_openai_strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Translate a canonical Pydantic model into an OpenAI-strict-structured-output schema.

    OpenAI supports `$ref`/`$defs` and `enum` directly, so those are left as-is; strict mode
    additionally requires every object node to set `additionalProperties: false`.
    """
    raw = model.model_json_schema()
    return _require_additional_properties_false(raw)
