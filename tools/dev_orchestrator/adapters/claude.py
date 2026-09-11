"""Safe optional Claude adapter placeholder.

The initial configuration keeps this adapter disabled.  Disabled operation deliberately performs
no binary resolution, authentication check, or subprocess launch.
"""

from __future__ import annotations

from typing import Callable

from .base import AdapterRequest, AdapterResult, AgentAdapter


class ClaudeAdapter(AgentAdapter):
    def __init__(self, binary: str, *, enabled: bool = False):
        self.binary = binary
        self.enabled = enabled

    def start(
        self, request: AdapterRequest, event_callback: Callable[[str, dict], None] | None = None
    ) -> AdapterResult:
        del request, event_callback
        if not self.enabled:
            return AdapterResult(
                status="DISABLED",
                error="Claude adapter is disabled; no binary or authentication check was performed.",
            )
        return AdapterResult(
            status="FAILED",
            error="Enabled Claude execution is not qualified in the initial orchestration release.",
        )
