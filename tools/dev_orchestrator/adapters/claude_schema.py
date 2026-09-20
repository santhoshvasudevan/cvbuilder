"""Compatibility transformation for Claude Code's bundled JSON Schema validator.

Claude Code 2.1.267 accepts structured output but does not bundle the draft 2020-12
meta-schema. Its validator accepts the same audit schema when it explicitly declares draft-07,
including JSON-pointer references into ``$defs``. Removing ``$schema`` entirely was tested and
rejected because it did not reliably enforce nested constraints through ``#/$defs``; the explicit
draft-07 declaration is therefore required. Transform only an in-memory deep copy so the canonical
schema remains draft 2020-12. Remove this shim once Claude ships a 2020-12 validator, after repeating
the nested-reference enforcement probe.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"
DRAFT_07 = "http://json-schema.org/draft-07/schema#"
UNSUPPORTED_DRAFT_2020_12_KEYWORDS = frozenset(
    {
        "prefixItems",
        "unevaluatedProperties",
        "unevaluatedItems",
        "dependentSchemas",
        "dependentRequired",
        "$dynamicRef",
        "$dynamicAnchor",
    }
)


def _present_unsupported_keywords(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        found.update(UNSUPPORTED_DRAFT_2020_12_KEYWORDS.intersection(value))
        for child in value.values():
            found.update(_present_unsupported_keywords(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_present_unsupported_keywords(child))
    return found


def schema_for_claude_cli(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a deep-copied schema with only its 2020-12 declaration changed to draft-07."""
    incompatible = _present_unsupported_keywords(schema)
    if incompatible:
        raise ValueError(
            "Claude draft-07 compatibility would weaken unsupported keywords: "
            + ", ".join(sorted(incompatible))
        )
    transformed = copy.deepcopy(schema)
    declaration = transformed.get("$schema")
    if declaration == DRAFT_2020_12:
        transformed["$schema"] = DRAFT_07
    elif declaration != DRAFT_07:
        raise ValueError(f"Unsupported Claude input schema declaration: {declaration!r}")
    return transformed


def schema_argument_for_claude_cli(path: Path) -> str:
    """Load a canonical schema and serialize its Claude-compatible in-memory copy."""
    schema = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(schema, dict):
        raise ValueError(f"Claude output schema must be a JSON object: {path}")
    transformed = schema_for_claude_cli(schema)
    return json.dumps(transformed, sort_keys=True, separators=(",", ":"))
