"""D-032: OpenAI Structured Outputs strict-schema `required`-completion fix.

Confirmed incident: a synthetic OpenAI `gpt-5` diagnostic request 400'd with
`type=invalid_request_error`, `param=response_format`, "'required' is required to be supplied
and to be an array including every key in properties. Missing 'diagnostic_terms'." Root cause:
Pydantic only lists a field in JSON Schema `required` when it has no default, so
`RequirementNormalizationItem`'s three `default_factory=list` fields were silently absent from
`required` -- but OpenAI's own documented rule is "all fields must be required" (developers.
openai.com/api/docs/guides/structured-outputs). `to_openai_strict_schema`/
`to_openai_compatible_strict_schema` (`llm_provider/schema_translation.py`) now complete
`required` recursively for every object node and reject an incomplete result locally, before any
HTTP call.

All HTTP calls in this file are mocked -- no network access, no credential, no live provider call.
"""

from __future__ import annotations

from typing import Literal
from unittest import mock

from django.test import SimpleTestCase, TestCase
from pydantic import BaseModel

from candidate_matching.schemas import (
    AgentCandidateAssessment,
    RelevanceRankingOutput,
    RequirementNormalizationItem,
    RequirementNormalizationOutput,
)
from candidate_memory.schemas import ChunkClassificationOnly, ChunkExtractionResult, ConflictCandidate
from job_intake.schemas import AgentJobberAnalysis
from resume_builder.schemas import AgentBuilderOutput

from ..adapters.nvidia import NvidiaNimAdapter
from ..adapters.openai import OpenAIAdapter
from ..adapters.openrouter import OpenRouterAdapter
from ..errors import TRANSIENT_ERROR_CATEGORIES, LLMErrorCategory
from ..models import LLMProvider, StageModelAssignment
from ..schema_translation import (
    OpenAIStrictSchemaContractError,
    to_openai_compatible_strict_schema,
    to_openai_strict_schema,
)
from ..smoke.common import SmokeTestOutput
from ..types import NormalizedLLMRequest
from .factories import make_model, make_provider

# The exact set of Pydantic models this codebase currently passes as `NormalizedLLMRequest.
# output_schema` to a live adapter (`grep -rn "output_schema=" --include="*.py"` across the whole
# tree, minus tests/migrations) -- MEMORY_BUILD, AJ_ANALYZE, AC_NORMALIZE, AC_RANK, AC_MATCH,
# AB_BUILD, and the provider smoke harness. `ConflictCandidate`/`ChunkClassificationOnly` are
# defined but not currently wired to any live request path -- included anyway as bonus coverage
# since the schema-translation functions operate on any `BaseModel` uniformly.
REGISTRY_USED_SCHEMAS = [
    ChunkExtractionResult,
    AgentJobberAnalysis,
    RequirementNormalizationOutput,
    RelevanceRankingOutput,
    AgentCandidateAssessment,
    AgentBuilderOutput,
    SmokeTestOutput,
]
NOT_YET_WIRED_SCHEMAS = [ConflictCandidate, ChunkClassificationOnly]


def _walk_object_nodes(node, path="$"):
    """Yield every JSON-Schema object node found anywhere in `node` -- root, `$defs`, nested
    properties, array `items`, and anything already inlined through `$ref` -- as
    `(path, node_dict)` pairs. Independent of `schema_translation._assert_object_contract_complete`
    so these tests check the actual translator output, not the same internal invariant helper the
    production code uses to check itself."""
    if isinstance(node, dict):
        if node.get("type") == "object" and isinstance(node.get("properties"), dict):
            yield path, node
        for key, value in node.items():
            yield from _walk_object_nodes(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from _walk_object_nodes(item, f"{path}[{index}]")


def _schema_text_contains(schema, key: str) -> bool:
    import json

    return f'"{key}"' in json.dumps(schema)


class RegressionFixtureTests(SimpleTestCase):
    """Item 1: reproduce the original failing schema shape (raw Pydantic output, pre-fix) as a
    fixture, then prove the fix corrects exactly that shape."""

    def test_raw_pydantic_schema_reproduces_the_incomplete_required_bug(self):
        raw = RequirementNormalizationItem.model_json_schema()
        self.assertEqual(
            raw["required"], ["requirement_id", "canonical_english_text", "source_language"]
        )
        self.assertEqual(
            set(raw["properties"].keys()),
            {
                "requirement_id",
                "canonical_english_text",
                "diagnostic_terms",
                "equivalents",
                "preserved_technical_terms",
                "source_language",
            },
        )
        # This is precisely the shape OpenAI rejected: 'required' is missing 'diagnostic_terms'
        # (and 'equivalents', 'preserved_technical_terms') from 'properties'.
        self.assertNotEqual(set(raw["required"]), set(raw["properties"].keys()))

    def test_diagnostic_error_fixture_shape(self):
        """A frozen, sanitized representation of the confirmed OpenAI 400 body -- never fetched
        live here, just documents the exact error fields the fix responds to."""
        diagnostic_error_fixture = {
            "error": {
                "type": "invalid_request_error",
                "param": "response_format",
                "message": (
                    "Invalid schema for response_format 'RequirementNormalizationOutput': In "
                    "context=(), 'required' is required to be supplied and to be an array "
                    "including every key in properties. Missing 'diagnostic_terms'."
                ),
            }
        }
        self.assertEqual(diagnostic_error_fixture["error"]["type"], "invalid_request_error")
        self.assertEqual(diagnostic_error_fixture["error"]["param"], "response_format")
        self.assertIn("diagnostic_terms", diagnostic_error_fixture["error"]["message"])


class RequirementNormalizationItemFixTests(SimpleTestCase):
    """Item 2/3: the specific model named in the incident."""

    def test_all_six_properties_required_after_openai_translation(self):
        schema = to_openai_strict_schema(RequirementNormalizationOutput)
        item = schema["$defs"]["RequirementNormalizationItem"]
        self.assertEqual(
            set(item["required"]),
            {
                "requirement_id",
                "canonical_english_text",
                "diagnostic_terms",
                "equivalents",
                "preserved_technical_terms",
                "source_language",
            },
        )

    def test_all_six_properties_required_after_nvidia_openrouter_translation(self):
        schema = to_openai_compatible_strict_schema(RequirementNormalizationOutput)
        item = schema["$defs"]["RequirementNormalizationItem"]
        self.assertEqual(set(item["required"]), set(item["properties"].keys()))

    def test_three_list_fields_remain_arrays_and_accept_empty(self):
        schema = to_openai_strict_schema(RequirementNormalizationOutput)
        item = schema["$defs"]["RequirementNormalizationItem"]
        for field in ("diagnostic_terms", "equivalents", "preserved_technical_terms"):
            with self.subTest(field=field):
                self.assertEqual(item["properties"][field]["type"], "array")
        # An empty list must still validate against the canonical model (never becomes
        # implicitly required-to-be-non-empty by this translation).
        validated = RequirementNormalizationItem.model_validate(
            {
                "requirement_id": "JR-1",
                "canonical_english_text": "text",
                "diagnostic_terms": [],
                "equivalents": [],
                "preserved_technical_terms": [],
                "source_language": "en",
            }
        )
        self.assertEqual(validated.diagnostic_terms, [])


class RecursiveRequiredCompletionTests(SimpleTestCase):
    """Items 4/5/6/7: `set(required) == set(properties.keys())` and `additionalProperties: false`
    for every object node -- root, `$defs`, nested objects, array item objects, `$ref` targets,
    nullable optional fields, defaulted non-null lists -- across every registry-used schema, in
    both the OpenAI dialect and the NVIDIA/OpenRouter dialect."""

    def test_every_object_node_is_fully_required_openai_dialect(self):
        for model in REGISTRY_USED_SCHEMAS + NOT_YET_WIRED_SCHEMAS:
            schema = to_openai_strict_schema(model)
            object_nodes = list(_walk_object_nodes(schema))
            with self.subTest(model=model.__name__):
                self.assertGreater(len(object_nodes), 0, "no object nodes found -- test is vacuous")
                for path, node in object_nodes:
                    prop_keys = set(node["properties"].keys())
                    required_keys = set(node.get("required") or [])
                    self.assertEqual(
                        required_keys, prop_keys, f"{model.__name__}{path}: required != properties"
                    )
                    self.assertIs(
                        node.get("additionalProperties"),
                        False,
                        f"{model.__name__}{path}: additionalProperties must be False",
                    )
                    # No nonexistent property was introduced into `required`.
                    self.assertTrue(required_keys.issubset(prop_keys))

    def test_every_object_node_is_fully_required_nvidia_openrouter_dialect(self):
        for model in REGISTRY_USED_SCHEMAS + NOT_YET_WIRED_SCHEMAS:
            schema = to_openai_compatible_strict_schema(model)
            object_nodes = list(_walk_object_nodes(schema))
            with self.subTest(model=model.__name__):
                for path, node in object_nodes:
                    prop_keys = set(node["properties"].keys())
                    required_keys = set(node.get("required") or [])
                    self.assertEqual(required_keys, prop_keys)
                    self.assertIs(node.get("additionalProperties"), False)

    def test_covers_defs_nested_and_array_item_objects_for_chunk_extraction_result(self):
        """`ChunkExtractionResult` is the deepest schema in the registry: `items` is an array of
        `ExtractedItem` (a `$ref`), which itself nests `SourcePassage` (required, non-nullable) and
        three `Optional[...] = None` structured-value objects (nullable, via `anyOf`)."""
        schema = to_openai_strict_schema(ChunkExtractionResult)
        defs = schema["$defs"]
        self.assertIn("ExtractedItem", defs)
        self.assertIn("SourcePassage", defs)
        self.assertIn("EmploymentDatesValue", defs)

        extracted_item = defs["ExtractedItem"]
        self.assertEqual(set(extracted_item["required"]), set(extracted_item["properties"].keys()))

        source_passage = defs["SourcePassage"]
        self.assertEqual(set(source_passage["required"]), set(source_passage["properties"].keys()))

        employment_dates = defs["EmploymentDatesValue"]
        self.assertEqual(set(employment_dates["required"]), set(employment_dates["properties"].keys()))
        # `start_month` is `int | None = None` in the canonical model -- nullable, but still
        # required by OpenAI strict mode (item: "truly optional fields must still be required...
        # represented using an allowed nullable schema").
        start_month = employment_dates["properties"]["start_month"]
        self.assertIn("start_month", employment_dates["required"])
        self.assertTrue(
            _schema_text_contains(start_month, "null") or start_month.get("type") == "null",
            "start_month must remain representable as null (anyOf with null), not forced non-null",
        )

        # `items` on the top-level object array of `ExtractedItem` ($ref-through array items).
        top_items_schema = schema["properties"]["items"]
        self.assertEqual(top_items_schema["type"], "array")
        self.assertIn("items", schema["required"])  # was default_factory=list -- now required too


class DeterminismTests(SimpleTestCase):
    """Item 8: deterministic schema output across repeated generation, including stable key order
    (required order mirrors properties' own insertion order, not an unordered set)."""

    def test_repeated_generation_is_byte_identical(self):
        first = to_openai_strict_schema(RequirementNormalizationOutput)
        second = to_openai_strict_schema(RequirementNormalizationOutput)
        self.assertEqual(first, second)

    def test_required_order_matches_properties_order(self):
        schema = to_openai_strict_schema(RequirementNormalizationOutput)
        item = schema["$defs"]["RequirementNormalizationItem"]
        self.assertEqual(item["required"], list(item["properties"].keys()))


class UnsupportedKeywordStrippingTests(SimpleTestCase):
    """Items 11/12: OpenAI-bound schemas contain no officially-unsupported keywords (confirmed
    against developers.openai.com/api/docs/guides/structured-outputs' "Supported properties" /
    "Some type-specific keywords are not yet supported" sections: only `pattern`/`format` are
    supported string constraints -- `minLength`/`maxLength` are never listed); NVIDIA/OpenRouter
    retain the stronger `maxLength: 60` bound this project currently relies on there."""

    def test_openai_schema_has_no_min_or_max_length(self):
        schema = to_openai_strict_schema(RequirementNormalizationOutput)
        self.assertFalse(_schema_text_contains(schema, "minLength"))
        self.assertFalse(_schema_text_contains(schema, "maxLength"))

    def test_openai_schema_keeps_officially_supported_maxitems(self):
        # maxItems (array bound) IS in OpenAI's documented supported subset -- must survive.
        schema = to_openai_strict_schema(RequirementNormalizationOutput)
        item = schema["$defs"]["RequirementNormalizationItem"]
        self.assertEqual(item["properties"]["diagnostic_terms"]["maxItems"], 8)
        self.assertEqual(schema["properties"]["items"]["maxItems"], 100)

    def test_nvidia_openrouter_schema_retains_max_length(self):
        schema = to_openai_compatible_strict_schema(RequirementNormalizationOutput)
        item = schema["$defs"]["RequirementNormalizationItem"]
        self.assertEqual(item["properties"]["diagnostic_terms"]["items"]["maxLength"], 60)
        self.assertEqual(item["properties"]["canonical_english_text"]["maxLength"], 500)

    def test_openai_schema_has_no_unsupported_keywords_across_registry(self):
        for model in REGISTRY_USED_SCHEMAS:
            schema = to_openai_strict_schema(model)
            with self.subTest(model=model.__name__):
                self.assertFalse(_schema_text_contains(schema, "minLength"))
                self.assertFalse(_schema_text_contains(schema, "maxLength"))
                self.assertFalse(_schema_text_contains(schema, "allOf"))
                self.assertFalse(_schema_text_contains(schema, "patternProperties"))


class InvariantGuardIsCatchableAndAccurateTests(SimpleTestCase):
    def test_invariant_passes_silently_on_complete_schema(self):
        # Should not raise for any registry schema (already exercised above); this just documents
        # that a fully-formed call never raises.
        for model in REGISTRY_USED_SCHEMAS:
            to_openai_strict_schema(model)  # no exception

    def test_invariant_raises_on_a_hand_broken_schema(self):
        from ..schema_translation import _assert_object_contract_complete

        broken = {
            "type": "object",
            "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
            "required": ["a"],
            "additionalProperties": False,
        }
        with self.assertRaises(OpenAIStrictSchemaContractError) as ctx:
            _assert_object_contract_complete(broken)
        self.assertIn("a", str(ctx.exception))
        self.assertIn("b", str(ctx.exception))

    def test_invariant_raises_when_additional_properties_not_false(self):
        from ..schema_translation import _assert_object_contract_complete

        broken = {
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "required": ["a"],
            # additionalProperties missing entirely.
        }
        with self.assertRaises(OpenAIStrictSchemaContractError):
            _assert_object_contract_complete(broken)


# --- Adapter-level: the guard fires before any HTTP call, and is classified CONFIGURATION -------

_CREDENTIAL_ENV_VAR = "TEST_STRICT_SCHEMA_KEY"


class _TinyOutput(BaseModel):
    ok: Literal["yes", "no"] = "yes"


def _request(**overrides) -> NormalizedLLMRequest:
    defaults = dict(
        stage=StageModelAssignment.Stage.AC_MATCH,
        messages=[{"role": "user", "content": "hi"}],
        output_schema=_TinyOutput,
        max_output_tokens=64,
    )
    defaults.update(overrides)
    return NormalizedLLMRequest(**defaults)


class _AdapterTestCaseMixin:
    provider_type: str
    adapter_cls: type
    module_path: str  # dotted path to the adapter module, for patching its imported name

    def setUp(self):
        super().setUp()
        self.provider = make_provider(
            provider_type=self.provider_type,
            name=f"{self.provider_type} strict-schema-guard test",
            credential_env_var=_CREDENTIAL_ENV_VAR,
        )
        self.model = make_model(
            provider=self.provider,
            model_id="test-model",
            supports_structured_output=True,
        )
        self.env_patch = mock.patch.dict("os.environ", {_CREDENTIAL_ENV_VAR: "dummy-test-value"})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)


class OpenAIInvariantGuardTests(_AdapterTestCaseMixin, TestCase):
    provider_type = LLMProvider.ProviderType.OPENAI
    adapter_cls = OpenAIAdapter
    module_path = "llm_provider.adapters.openai"

    def test_translate_schema_failure_is_configuration_before_any_http_call(self):
        with mock.patch(
            f"{self.module_path}.to_openai_strict_schema",
            side_effect=OpenAIStrictSchemaContractError("broken schema"),
        ), mock.patch("requests.post") as post_mock:
            result = self.adapter_cls(self.model).generate(_request())
        post_mock.assert_not_called()
        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category, LLMErrorCategory.CONFIGURATION)
        self.assertNotIn(result.error.category, TRANSIENT_ERROR_CATEGORIES)


class NvidiaInvariantGuardTests(_AdapterTestCaseMixin, TestCase):
    provider_type = LLMProvider.ProviderType.NVIDIA_NIM
    adapter_cls = NvidiaNimAdapter
    module_path = "llm_provider.adapters.nvidia"

    def test_translate_schema_failure_is_configuration_before_any_http_call(self):
        with mock.patch(
            f"{self.module_path}.to_openai_compatible_strict_schema",
            side_effect=OpenAIStrictSchemaContractError("broken schema"),
        ), mock.patch("requests.post") as post_mock:
            result = self.adapter_cls(self.model).generate(_request())
        post_mock.assert_not_called()
        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category, LLMErrorCategory.CONFIGURATION)
        self.assertNotIn(result.error.category, TRANSIENT_ERROR_CATEGORIES)


class OpenRouterInvariantGuardTests(_AdapterTestCaseMixin, TestCase):
    provider_type = LLMProvider.ProviderType.OPENROUTER
    adapter_cls = OpenRouterAdapter
    module_path = "llm_provider.adapters.openrouter"

    def setUp(self):
        super().setUp()
        self.provider.data_collection_policy = LLMProvider.DataCollectionPolicy.DENY
        self.provider.save(update_fields=["data_collection_policy"])

    def test_translate_schema_failure_is_configuration_before_any_http_call(self):
        with mock.patch(
            f"{self.module_path}.to_openai_compatible_strict_schema",
            side_effect=OpenAIStrictSchemaContractError("broken schema"),
        ), mock.patch("requests.post") as post_mock:
            result = self.adapter_cls(self.model).generate(_request())
        post_mock.assert_not_called()
        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category, LLMErrorCategory.CONFIGURATION)
        self.assertNotIn(result.error.category, TRANSIENT_ERROR_CATEGORIES)


class _FakeHttpResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = (
            payload
            if payload is not None
            else {
                "choices": [{"message": {"content": '{"ok": "yes"}'}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            }
        )

    def json(self):
        return self._payload


class FinalRequestBodyContainsCorrectedSchemaTests(_AdapterTestCaseMixin, TestCase):
    """Item 9: the final OpenAI (and NVIDIA) request body actually carries the corrected,
    fully-required schema -- not just that the translation function produces one in isolation."""

    provider_type = LLMProvider.ProviderType.OPENAI
    adapter_cls = OpenAIAdapter
    module_path = "llm_provider.adapters.openai"

    def test_openai_request_body_schema_is_fully_required(self):
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            OpenAIAdapter(self.model).generate(
                _request(output_schema=RequirementNormalizationOutput, max_output_tokens=4096)
            )
        body = post_mock.call_args.kwargs["json"]
        schema = body["response_format"]["json_schema"]["schema"]
        item = schema["$defs"]["RequirementNormalizationItem"]
        self.assertEqual(set(item["required"]), set(item["properties"].keys()))
        self.assertNotIn("maxLength", str(schema))


class NvidiaFinalRequestBodyTests(_AdapterTestCaseMixin, TestCase):
    provider_type = LLMProvider.ProviderType.NVIDIA_NIM
    adapter_cls = NvidiaNimAdapter
    module_path = "llm_provider.adapters.nvidia"

    def test_nvidia_request_body_schema_is_fully_required_and_keeps_max_length(self):
        with mock.patch("requests.post", return_value=_FakeHttpResponse()) as post_mock:
            NvidiaNimAdapter(self.model).generate(
                _request(output_schema=RequirementNormalizationOutput, max_output_tokens=4096)
            )
        body = post_mock.call_args.kwargs["json"]
        schema = body["response_format"]["json_schema"]["schema"]
        item = schema["$defs"]["RequirementNormalizationItem"]
        self.assertEqual(set(item["required"]), set(item["properties"].keys()))
        self.assertEqual(item["properties"]["canonical_english_text"]["maxLength"], 500)


class PostResponseValidationStillEnforcesStrippedConstraintsTests(_AdapterTestCaseMixin, TestCase):
    """Item 13: content the OpenAI-bound schema no longer *asks* the provider to enforce
    (maxLength) still fails canonical Pydantic validation once returned -- proving D-005's
    post-response re-validation, not the outbound schema, is what actually guarantees the bound."""

    provider_type = LLMProvider.ProviderType.OPENAI
    adapter_cls = OpenAIAdapter
    module_path = "llm_provider.adapters.openai"

    def test_overlong_term_in_openai_response_still_fails_schema_validation(self):
        overlong_term = "x" * 61
        payload = {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"items": [{"requirement_id": "JR-1", '
                            '"canonical_english_text": "short", '
                            f'"diagnostic_terms": ["{overlong_term}"], '
                            '"equivalents": [], "preserved_technical_terms": [], '
                            '"source_language": "en"}]}'
                        )
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }
        with mock.patch("requests.post", return_value=_FakeHttpResponse(payload=payload)):
            result = OpenAIAdapter(self.model).generate(
                _request(output_schema=RequirementNormalizationOutput, max_output_tokens=4096)
            )
        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category, LLMErrorCategory.SCHEMA_VALIDATION)

    def test_overlong_canonical_text_in_openai_response_still_fails_schema_validation(self):
        overlong_text = "y" * 501
        payload = {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"items": [{"requirement_id": "JR-1", '
                            f'"canonical_english_text": "{overlong_text}", '
                            '"diagnostic_terms": [], "equivalents": [], '
                            '"preserved_technical_terms": [], "source_language": "en"}]}'
                        )
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }
        with mock.patch("requests.post", return_value=_FakeHttpResponse(payload=payload)):
            result = OpenAIAdapter(self.model).generate(
                _request(output_schema=RequirementNormalizationOutput, max_output_tokens=4096)
            )
        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category, LLMErrorCategory.SCHEMA_VALIDATION)

    def test_too_many_items_in_openai_response_still_fails_schema_validation(self):
        """Array item-count bound (`MAX_NORMALIZATION_ITEMS`/`maxItems`) -- still officially
        supported by OpenAI and kept in the outbound schema, but this proves canonical validation
        is the actual authority even if a future provider silently dropped it too."""
        item = (
            '{"requirement_id": "JR-1", "canonical_english_text": "t", "diagnostic_terms": [], '
            '"equivalents": [], "preserved_technical_terms": [], "source_language": "en"}'
        )
        content = '{"items": [' + ", ".join([item] * 101) + "]}"
        payload = {
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }
        with mock.patch("requests.post", return_value=_FakeHttpResponse(payload=payload)):
            result = OpenAIAdapter(self.model).generate(
                _request(output_schema=RequirementNormalizationOutput, max_output_tokens=4096)
            )
        self.assertTrue(result.is_error)
        self.assertEqual(result.error.category, LLMErrorCategory.SCHEMA_VALIDATION)


class NoRawContentLoggedTests(_AdapterTestCaseMixin, TestCase):
    """Item 16: no raw response/prompt/credential ever reaches the CONFIGURATION error message
    this new guard produces -- it is built entirely from our own schema property names, never from
    provider response content."""

    provider_type = LLMProvider.ProviderType.OPENAI
    adapter_cls = OpenAIAdapter
    module_path = "llm_provider.adapters.openai"

    def test_configuration_error_message_contains_no_credential_or_provider_text(self):
        with mock.patch(
            f"{self.module_path}.to_openai_strict_schema",
            side_effect=OpenAIStrictSchemaContractError(
                "$.properties.diagnostic_terms: strict-mode object contract incomplete"
            ),
        ), mock.patch("requests.post") as post_mock:
            result = OpenAIAdapter(self.model).generate(_request())
        post_mock.assert_not_called()
        self.assertNotIn("dummy-test-value", result.error.message)
