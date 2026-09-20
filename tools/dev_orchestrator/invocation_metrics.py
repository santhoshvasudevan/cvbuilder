"""Passive metrics derived from provider events for one agent invocation."""

from __future__ import annotations

import dataclasses
import json
from typing import Any


def _captured_bytes(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    return len(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    )


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


@dataclasses.dataclass
class InvocationMetrics:
    """Accumulate only values already exposed by sanitized adapter events."""

    task_id: str = ""
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    tool_output_bytes: int = 0
    _tool_call_ids: set[str] = dataclasses.field(default_factory=set)
    _completed_tool_call_ids: set[str] = dataclasses.field(default_factory=set)

    @property
    def tool_call_count(self) -> int:
        return len(self._tool_call_ids)

    @staticmethod
    def _identifier(event: dict) -> str:
        for key in ("task_id", "taskId", "request_id", "requestId", "thread_id", "threadId"):
            value = event.get(key)
            if isinstance(value, str) and value:
                return value
        return ""

    @staticmethod
    def _tool_call(event: dict) -> tuple[str, Any, bool] | None:
        event_type = str(event.get("type", ""))
        if event_type == "tool_call":
            call_id = event.get("call_id") or event.get("toolCallId")
            tool_call = event.get("tool_call")
            if not isinstance(call_id, str) or not call_id or not isinstance(tool_call, dict):
                return None
            result = tool_call.get("result")
            if result is None:
                for value in tool_call.values():
                    if isinstance(value, dict) and "result" in value:
                        result = value["result"]
                        break
            return call_id, result, event.get("subtype") == "completed"

        if event_type not in {"item.started", "item.completed"}:
            return None
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") in {"agent_message", "reasoning"}:
            return None
        call_id = item.get("id")
        if not isinstance(call_id, str) or not call_id:
            return None
        result = item.get("aggregated_output")
        if result is None:
            result = item.get("output") or item.get("result")
        return call_id, result, event_type == "item.completed"

    def observe(self, event: dict) -> None:
        self.task_id = self.task_id or self._identifier(event)
        usage = event.get("usage")
        if isinstance(usage, dict):
            self.input_tokens += _integer(
                usage.get("input_tokens", usage.get("inputTokens", 0))
            )
            self.cached_input_tokens += _integer(
                usage.get("cached_input_tokens", usage.get("cacheReadTokens", 0))
            )
            self.output_tokens += _integer(
                usage.get("output_tokens", usage.get("outputTokens", 0))
            )
            self.reasoning_output_tokens += _integer(usage.get("reasoning_output_tokens", 0))

        tool_call = self._tool_call(event)
        if tool_call is None:
            return
        call_id, output, completed = tool_call
        self._tool_call_ids.add(call_id)
        if completed and call_id not in self._completed_tool_call_ids:
            self._completed_tool_call_ids.add(call_id)
            self.tool_output_bytes += _captured_bytes(output)
