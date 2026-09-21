from django import forms

from candidate_memory.models import CareerEngagement


class ExperienceSlotSelectionForm(forms.Form):
    """Operator-only selection of up to three engagements with explicit order."""

    slot_1 = forms.ModelChoiceField(
        label="Primary slot 1",
        queryset=CareerEngagement.objects.none(),
        required=True,
    )
    slot_2 = forms.ModelChoiceField(
        label="Primary slot 2",
        queryset=CareerEngagement.objects.none(),
        required=True,
    )
    slot_3 = forms.ModelChoiceField(
        label="Primary slot 3",
        queryset=CareerEngagement.objects.none(),
        required=True,
    )

    def __init__(self, *args, memory, **kwargs):
        super().__init__(*args, **kwargs)
        engagements = CareerEngagement.objects.filter(memory=memory).order_by("sort_hint", "id")
        for name in ("slot_1", "slot_2", "slot_3"):
            self.fields[name].queryset = engagements

    def clean(self):
        cleaned = super().clean()
        selected = [
            cleaned.get("slot_1"),
            cleaned.get("slot_2"),
            cleaned.get("slot_3"),
        ]
        if all(selected) and len({engagement.pk for engagement in selected}) != 3:
            raise forms.ValidationError(
                "Each primary ExperienceSlot must use a distinct CareerEngagement."
            )
        return cleaned

    def ordered_engagement_ids(self) -> list[int]:
        return [
            self.cleaned_data["slot_1"].pk,
            self.cleaned_data["slot_2"].pk,
            self.cleaned_data["slot_3"].pk,
        ]
