"""Manual opt-in provider smoke test (docs/IMPLEMENTATION_PLAN.md M2: "manual opt-in provider
smoke testing"). Never runs automatically -- not called by any automated test, not part of
`make test`/`make verify`. Makes at most one real HTTP call, and only when a real credential is
actually present in the environment for the named provider; otherwise reports NOT_LIVE_VERIFIED
and exits cleanly without ever attempting a network call.
"""

from django.core.management.base import BaseCommand, CommandError
from pydantic import BaseModel

from llm_provider.adapters import get_adapter_for_model
from llm_provider.errors import ConfigurationError
from llm_provider.models import LLMProvider
from llm_provider.types import NormalizedLLMRequest
from llm_provider.validation import validate_call_configuration


class _SmokeTestSchema(BaseModel):
    acknowledgement: str


class Command(BaseCommand):
    help = (
        "Manually smoke-test one configured LLMProvider with a minimal structured-output call. "
        "Opt-in only. Reports NOT_LIVE_VERIFIED without making any HTTP call if no credential "
        "is configured/present, or if no enabled model exists for the provider."
    )

    def add_arguments(self, parser):
        parser.add_argument("provider_name", help="LLMProvider.name to smoke-test.")
        parser.add_argument(
            "--model",
            dest="model_identifier",
            default=None,
            help="Specific LLMModel.model_identifier to use. Defaults to the first enabled model.",
        )

    def handle(self, *args, **options):
        provider_name = options["provider_name"]
        try:
            provider = LLMProvider.objects.get(name=provider_name)
        except LLMProvider.DoesNotExist as exc:
            raise CommandError(f"No LLMProvider named {provider_name!r}.") from exc

        model_identifier = options.get("model_identifier")
        models = provider.models.filter(enabled=True)
        if model_identifier:
            models = models.filter(model_identifier=model_identifier)
        model = models.first()
        if model is None:
            self.stdout.write(self.style.WARNING("NOT_LIVE_VERIFIED: no enabled LLMModel configured."))
            return

        try:
            validate_call_configuration(
                provider=provider, model=model, require_structured_output=True
            )
        except ConfigurationError as exc:
            self.stdout.write(self.style.WARNING(f"NOT_LIVE_VERIFIED: {exc}"))
            return

        request = NormalizedLLMRequest(
            stage="AJ_ANALYZE",
            messages=[{"role": "user", "content": "Reply with a short acknowledgement only."}],
            output_schema=_SmokeTestSchema,
            max_output_tokens=32,
        )
        adapter = get_adapter_for_model(model)
        result = adapter.generate(request)
        if result.is_error:
            self.stdout.write(
                self.style.ERROR(f"FAILED: {result.error.category.value}: {result.error.message}")
            )
        else:
            latency = f"{result.latency_ms:.0f}ms" if result.latency_ms is not None else "n/a"
            self.stdout.write(self.style.SUCCESS(f"VERIFIED: {model} responded in {latency}"))
