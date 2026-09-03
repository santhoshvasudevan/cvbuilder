"""Generic Human Review Gate mechanics (HITL-001..007, D-010) shared by Gate 1 (`candidate_matching`)
and Gate 2 (`resume_builder`). A gate's own pass/fail state is plain `JobApplication` state
(`pipeline_phase`, checked fresh on every request -- HITL-004) -- `ReviewFeedback` here is only the
audit record of *why* a re-run happened, not the mechanism that performs the re-run itself (that is
`reviews/services.py`, which calls back into `job_intake`/`candidate_matching`/`resume_builder`).
"""

from __future__ import annotations

from django.db import models


class ReviewFeedback(models.Model):
    class Gate(models.TextChoices):
        GATE_1 = "GATE_1", "Gate 1 (Candidate matching)"
        GATE_2 = "GATE_2", "Gate 2 (Resume draft)"

    class Target(models.TextChoices):
        AJ = "AJ", "Agent Jobber"
        AC = "AC", "Agent Candidate"
        AB = "AB", "Agent Builder"

    job_application = models.ForeignKey(
        "job_applications.JobApplication", on_delete=models.CASCADE, related_name="review_feedback"
    )
    gate = models.CharField(max_length=10, choices=Gate.choices)
    target = models.CharField(max_length=10, choices=Target.choices)
    comments = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.gate} feedback -> {self.target} on app {self.job_application_id}"
