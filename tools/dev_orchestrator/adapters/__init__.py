"""Development-agent adapter registry."""

from .base import AdapterRequest, AdapterResult, AgentAdapter
from .claude import ClaudeAdapter
from .codex import CodexAdapter
from .cursor import CursorAdapter
from .fake import FakeAdapter

__all__ = [
    "AdapterRequest",
    "AdapterResult",
    "AgentAdapter",
    "ClaudeAdapter",
    "CodexAdapter",
    "CursorAdapter",
    "FakeAdapter",
]
