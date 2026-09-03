"""Plain intake form -- field shape only; the real "exactly one source" and size-limit validation
lives in `services/intake.py::resolve_posting_source` so it's testable independently of Django's
form machinery and shared by any future non-HTML entry point."""

from __future__ import annotations

from django import forms


class JobPostingIntakeForm(forms.Form):
    url = forms.URLField(
        required=False, max_length=2000, label="Job posting URL",
        widget=forms.URLInput(attrs={"placeholder": "https://..."}),
    )
    pasted_text = forms.CharField(
        required=False, label="Or paste the posting text",
        widget=forms.Textarea(attrs={"rows": 12}),
    )
