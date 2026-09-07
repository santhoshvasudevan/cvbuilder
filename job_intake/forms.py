"""Plain intake form -- field shape only; the real "exactly one source" and size-limit validation
lives in `services/intake.py::resolve_posting_source` so it's testable independently of Django's
form machinery and shared by any future non-HTML entry point."""

from __future__ import annotations

from django import forms

from llm_provider.models import StageModelAssignment
from llm_provider.services.eligibility import eligible_models_for_stage, model_display_label

SYSTEM_DEFAULT_CHOICE = ("", "System default")


def _model_choices(stage: str) -> list[tuple[str, str]]:
    default_assignment = (
        StageModelAssignment.objects.select_related("model").filter(stage=stage).first()
    )
    default_model_id = default_assignment.model_id if default_assignment else None
    choices = []
    for model in eligible_models_for_stage(stage):
        label = model_display_label(model)
        if model.pk == default_model_id:
            label = f"{label} [Default]"
        choices.append((str(model.pk), label))
    return choices


class JobPostingIntakeForm(forms.Form):
    url = forms.URLField(
        required=False, max_length=2000, label="Job posting URL",
        widget=forms.URLInput(attrs={"placeholder": "https://..."}),
    )
    pasted_text = forms.CharField(
        required=False, label="Or paste the posting text",
        widget=forms.Textarea(attrs={"rows": 12}),
    )
    model_aj = forms.ChoiceField(
        required=False,
        label="Agent Jobber model (optional override)",
        help_text="Leave on System default to use the configured AJ_ANALYZE default.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["model_aj"].choices = [SYSTEM_DEFAULT_CHOICE] + _model_choices(
            StageModelAssignment.Stage.AJ_ANALYZE
        )
