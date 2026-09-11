"""Append-only, sanitized structured event ledger."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from .redaction import redact


class EventLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def emit(self, *, run_id: str, role: str, state: str, event: str, message: str, **data: Any) -> dict:
        record = redact(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "run_id": run_id,
                "role": role,
                "state": state,
                "event": event,
                "message": message,
                "data": data,
            }
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n"
        descriptor = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(descriptor, encoded.encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return record

    def read(self) -> Iterable[dict]:
        if not self.path.exists():
            return []
        records = []
        for number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Malformed event line {number}: {exc}") from exc
            records.append(record)
        return records
