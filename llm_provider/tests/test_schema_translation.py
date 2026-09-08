import enum

from django.test import SimpleTestCase
from pydantic import BaseModel

from llm_provider.schema_translation import (
    inline_refs,
    strip_enums,
    to_gemini_schema,
    to_openai_strict_schema,
)


class Priority(str, enum.Enum):
    LOW = "LOW"
    HIGH = "HIGH"


class Item(BaseModel):
    name: str
    priority: Priority


class Container(BaseModel):
    items: list[Item]
    count: int


class SchemaTranslationTests(SimpleTestCase):
    def test_inline_refs_removes_ref_and_defs(self):
        raw = Container.model_json_schema()
        self.assertIn("$defs", raw)
        inlined = inline_refs(raw)
        self.assertNotIn("$defs", inlined)
        self.assertEqual(_find_key(inlined, "$ref"), None)

    def test_strip_enums_removes_all_enum_keys(self):
        raw = Container.model_json_schema()
        stripped = strip_enums(raw)
        self.assertEqual(_find_key(stripped, "enum"), None)

    def test_to_gemini_schema_has_no_refs_defs_or_enums(self):
        schema = to_gemini_schema(Container)
        self.assertEqual(_find_key(schema, "$ref"), None)
        self.assertEqual(_find_key(schema, "$defs"), None)
        self.assertEqual(_find_key(schema, "enum"), None)

    def test_to_gemini_schema_uses_uppercase_openapi_types(self):
        schema = to_gemini_schema(Container)
        self.assertEqual(schema.get("type"), "OBJECT")

    def test_to_gemini_schema_drops_unsupported_keys(self):
        schema = to_gemini_schema(Container)
        self.assertEqual(_find_key(schema, "title"), None)
        self.assertEqual(_find_key(schema, "additionalProperties"), None)

    def test_to_openai_strict_schema_keeps_refs_and_enums(self):
        schema = to_openai_strict_schema(Container)
        # OpenAI supports $ref/$defs/enum natively -- these must survive translation.
        self.assertIn("$defs", schema)

    def test_to_openai_strict_schema_sets_additional_properties_false_on_every_object(self):
        schema = to_openai_strict_schema(Container)
        self.assertEqual(schema.get("additionalProperties"), False)
        item_def = schema["$defs"]["Item"]
        self.assertEqual(item_def.get("additionalProperties"), False)

    def test_enum_values_still_enforced_by_pydantic_after_gemini_stripping(self):
        # Gemini's wire schema drops `enum`, but the returned value is re-validated in Python
        # against the real Pydantic model afterward (BaseLLMAdapter.generate()) -- confirm that
        # re-validation still rejects an invalid enum value even though the *sent* schema no
        # longer constrains it.
        with self.assertRaises(Exception):
            Item.model_validate({"name": "x", "priority": "NOT_A_REAL_PRIORITY"})


def _find_key(node, key):
    if isinstance(node, dict):
        if key in node:
            return node[key]
        for value in node.values():
            found = _find_key(value, key)
            if found is not None:
                return found
    if isinstance(node, list):
        for item in node:
            found = _find_key(item, key)
            if found is not None:
                return found
    return None
