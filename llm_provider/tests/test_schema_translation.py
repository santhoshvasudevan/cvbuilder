import json
from typing import Literal

from django.test import SimpleTestCase
from pydantic import BaseModel

from ..adapters.gemini import GeminiAdapter
from ..adapters.nvidia import NvidiaNimAdapter
from ..adapters.openai import OpenAIAdapter
from ..schema_translation import inline_refs, strip_enums, to_gemini_schema, to_openai_strict_schema


class _Address(BaseModel):
    city: str
    country: str


class _Person(BaseModel):
    name: str
    role: Literal["engineer", "manager", "designer"]
    address: _Address


def _schema_has_key_anywhere(node, key: str) -> bool:
    text = json.dumps(node)
    return f'"{key}"' in text


class InlineRefsTests(SimpleTestCase):
    def test_removes_ref_and_defs(self):
        raw = _Person.model_json_schema()
        self.assertTrue(_schema_has_key_anywhere(raw, "$defs"))

        inlined = inline_refs(raw)

        self.assertFalse(_schema_has_key_anywhere(inlined, "$ref"))
        self.assertFalse(_schema_has_key_anywhere(inlined, "$defs"))
        # The nested Address fields must still be present after inlining.
        self.assertTrue(_schema_has_key_anywhere(inlined, "city"))
        self.assertTrue(_schema_has_key_anywhere(inlined, "country"))


class StripEnumsTests(SimpleTestCase):
    def test_removes_enum_keys_recursively(self):
        raw = inline_refs(_Person.model_json_schema())
        self.assertTrue(_schema_has_key_anywhere(raw, "enum"))

        stripped = strip_enums(raw)

        self.assertFalse(_schema_has_key_anywhere(stripped, "enum"))


class ToGeminiSchemaTests(SimpleTestCase):
    def test_no_ref_defs_or_enum_and_uppercase_types(self):
        schema = to_gemini_schema(_Person)

        self.assertFalse(_schema_has_key_anywhere(schema, "$ref"))
        self.assertFalse(_schema_has_key_anywhere(schema, "$defs"))
        self.assertFalse(_schema_has_key_anywhere(schema, "enum"))
        self.assertEqual(schema["type"], "OBJECT")
        self.assertEqual(schema["properties"]["name"]["type"], "STRING")
        self.assertEqual(schema["properties"]["address"]["type"], "OBJECT")
        self.assertEqual(schema["properties"]["address"]["properties"]["city"]["type"], "STRING")

    def test_drops_keys_gemini_does_not_support(self):
        schema = to_gemini_schema(_Person)
        self.assertFalse(_schema_has_key_anywhere(schema, "additionalProperties"))
        self.assertFalse(_schema_has_key_anywhere(schema, "title"))


class ToOpenAIStrictSchemaTests(SimpleTestCase):
    def test_sets_additional_properties_false_on_every_object(self):
        schema = to_openai_strict_schema(_Person)

        self.assertEqual(schema["additionalProperties"], False)
        nested_address_schema = schema["$defs"]["_Address"]
        self.assertEqual(nested_address_schema["additionalProperties"], False)

    def test_keeps_ref_defs_and_enum_openai_supports_both(self):
        schema = to_openai_strict_schema(_Person)
        self.assertTrue(_schema_has_key_anywhere(schema, "$defs"))
        self.assertTrue(_schema_has_key_anywhere(schema, "enum"))


class PerAdapterSchemaTranslationTests(SimpleTestCase):
    """M2 acceptance criteria: each of the three real adapters has at least a
    schema-translation unit test -- no live API calls made."""

    def test_openai_adapter_translate_schema(self):
        schema = OpenAIAdapter.translate_schema(_Person)
        self.assertEqual(schema["additionalProperties"], False)

    def test_nvidia_nim_adapter_translate_schema_is_openai_compatible(self):
        schema = NvidiaNimAdapter.translate_schema(_Person)
        self.assertEqual(schema["additionalProperties"], False)
        self.assertTrue(_schema_has_key_anywhere(schema, "$defs"))

    def test_gemini_adapter_translate_schema_uses_reduced_dialect(self):
        schema = GeminiAdapter.translate_schema(_Person)
        self.assertFalse(_schema_has_key_anywhere(schema, "$ref"))
        self.assertFalse(_schema_has_key_anywhere(schema, "enum"))
        self.assertEqual(schema["type"], "OBJECT")
