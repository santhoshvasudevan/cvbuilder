"""Provider-neutral subprocess adapter interface and safe process runner."""

from __future__ import annotations

import dataclasses
import json
import os
import queue
import re
import signal
import subprocess
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable

from ..redaction import redact, redact_text


@dataclasses.dataclass(frozen=True)
class AdapterRequest:
    role: str
    prompt: str
    workdir: Path
    model: str
    reasoning_effort: str | None
    permission_profile: str
    timeout_seconds: int
    heartbeat_seconds: int
    log_dir: Path
    output_schema: Path | None = None
    session_id: str = ""
    allow_write: bool = False
    safety_verified: bool = False
    packet_bytes: int = 0
    retry_reason: str = ""


@dataclasses.dataclass
class AdapterResult:
    status: str
    exit_code: int | None = None
    session_id: str = ""
    final_message: str = ""
    handoff: dict | None = None
    stdout_log: str = ""
    stderr_log: str = ""
    error: str = ""
    timed_out: bool = False

    @property
    def succeeded(self) -> bool:
        return self.status == "COMPLETED" and self.exit_code == 0 and self.handoff is not None


class AgentAdapter(ABC):
    @abstractmethod
    def start(
        self, request: AdapterRequest, event_callback: Callable[[str, dict], None] | None = None
    ) -> AdapterResult:
        raise NotImplementedError

    def resume(
        self, request: AdapterRequest, event_callback: Callable[[str, dict], None] | None = None
    ) -> AdapterResult:
        if not request.session_id:
            return AdapterResult(status="FAILED", error="resume requires a session ID")
        return self.start(request, event_callback)

    def terminate(self) -> None:
        """Terminate the active child process, if any."""


class ProcessAdapter(AgentAdapter):
    """Shared streaming runner. Concrete adapters only build argv and parse NDJSON events."""

    def __init__(self, binary: str):
        self.binary = binary
        self._process: subprocess.Popen | None = None

    @staticmethod
    def safe_environment() -> dict[str, str]:
        """Pass only process/runtime essentials, never provider keys or arbitrary env secrets."""
        allowed = {
            "PATH",
            "HOME",
            "USER",
            "LOGNAME",
            "SHELL",
            "TMPDIR",
            "TMP",
            "TEMP",
            "LANG",
            "LC_ALL",
            "TERM",
            "COLORTERM",
            "NO_COLOR",
            "PGHOST",
            "PGPORT",
            "PGUSER",
            "PGPASSWORD",
            "PGDATABASE",
            "DATABASE_URL",
        }
        return {key: value for key, value in os.environ.items() if key in allowed}

    @abstractmethod
    def build_command(self, request: AdapterRequest, final_output_path: Path) -> list[str]:
        raise NotImplementedError

    def parse_event(self, line: str) -> dict:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            return {"type": "text", "message": redact_text(line)}
        return value if isinstance(value, dict) else {"type": "json", "value": value}

    def session_id_from_event(self, event: dict) -> str:
        for key in ("session_id", "sessionId", "thread_id", "threadId", "chat_id", "chatId"):
            value = event.get(key)
            if isinstance(value, str) and value:
                return value
        return ""

    def final_message_from_event(self, event: dict) -> str:
        event_type = str(event.get("type", "")).lower()
        if event_type in {"result", "final", "assistant.final", "agent_message"}:
            for key in ("result", "message", "text", "content"):
                value = event.get(key)
                if isinstance(value, str):
                    return value
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") == "agent_message":
            value = item.get("text") or item.get("content")
            return value if isinstance(value, str) else ""
        return ""

    @staticmethod
    def contains_hidden_reasoning(event: dict) -> bool:
        event_type = str(event.get("type", "")).lower()
        if "reasoning" in event_type or event_type.startswith("analysis"):
            return True
        item = event.get("item")
        if isinstance(item, dict):
            item_type = str(item.get("type", "")).lower()
            if "reasoning" in item_type or item_type.startswith("analysis"):
                return True
        return False

    @staticmethod
    def _parse_handoff(final_message: str) -> dict | None:
        if not final_message:
            return None
        candidate = final_message.strip()
        fenced = re.fullmatch(r"```(?:json)?\s*(\{.*\})\s*```", candidate, flags=re.DOTALL)
        if fenced:
            candidate = fenced.group(1)
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _extract_last_json_object(final_message: str) -> dict | None:
        """Tolerant extract: last JSON object in fenced blocks or balanced braces amid prose."""
        if not final_message:
            return None
        parsed: list[dict] = []
        fence_pattern = re.compile(r"```(?:json)?\s*(.*?)\s*```", flags=re.DOTALL)
        for match in fence_pattern.finditer(final_message):
            block = match.group(1).strip()
            try:
                value = json.loads(block)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                parsed.append(value)
        if parsed:
            return parsed[-1]

        candidates: list[str] = []
        depth = 0
        start: int | None = None
        for index, char in enumerate(final_message):
            if char == "{":
                if depth == 0:
                    start = index
                depth += 1
            elif char == "}" and depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    candidates.append(final_message[start : index + 1])
                    start = None
        for candidate in reversed(candidates):
            try:
                value = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
        return None

    def start(
        self, request: AdapterRequest, event_callback: Callable[[str, dict], None] | None = None
    ) -> AdapterResult:
        request.log_dir.mkdir(parents=True, exist_ok=True)
        request.log_dir.chmod(0o700)
        invocation = len(list(request.log_dir.glob(f"{request.role}-*.stdout.log"))) + 1
        stem = f"{request.role}-{invocation:03d}"
        stdout_path = request.log_dir / f"{stem}.stdout.log"
        stderr_path = request.log_dir / f"{stem}.stderr.log"
        final_path = request.log_dir / f"{stem}.final.json"
        argv = self.build_command(request, final_path)
        line_queue: queue.Queue[tuple[str, str] | None] = queue.Queue()

        def drain(stream, stream_name: str) -> None:
            try:
                for line in iter(stream.readline, ""):
                    line_queue.put((stream_name, line.rstrip("\n")))
            finally:
                line_queue.put(None)

        try:
            self._process = subprocess.Popen(
                argv,
                cwd=request.workdir,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                start_new_session=True,
                env=self.safe_environment(),
            )
        except OSError as exc:
            return AdapterResult(status="FAILED", error=redact_text(str(exc)))

        assert self._process.stdin and self._process.stdout and self._process.stderr
        try:
            self._process.stdin.write(request.prompt)
            self._process.stdin.close()
        except BrokenPipeError:
            # Some CLIs receive the prompt as an argv element and close stdin immediately.
            pass
        threads = [
            threading.Thread(target=drain, args=(self._process.stdout, "stdout"), daemon=True),
            threading.Thread(target=drain, args=(self._process.stderr, "stderr"), daemon=True),
        ]
        for thread in threads:
            thread.start()

        session_id = request.session_id
        final_message = ""
        completed_streams = 0
        started_at = time.monotonic()
        deadline = started_at + request.timeout_seconds
        next_heartbeat = started_at + request.heartbeat_seconds
        with (
            stdout_path.open("w", encoding="utf-8") as stdout_handle,
            stderr_path.open("w", encoding="utf-8") as stderr_handle,
        ):
            while completed_streams < 2 or self._process.poll() is None:
                if event_callback and time.monotonic() >= next_heartbeat:
                    event_callback(
                        "system",
                        {
                            "type": "heartbeat",
                            "elapsed_seconds": round(time.monotonic() - started_at, 1),
                        },
                    )
                    next_heartbeat = time.monotonic() + request.heartbeat_seconds
                if time.monotonic() >= deadline:
                    self.terminate()
                    return AdapterResult(
                        status="TIMEOUT",
                        exit_code=self._process.poll(),
                        session_id=session_id,
                        final_message=final_message,
                        stdout_log=str(stdout_path),
                        stderr_log=str(stderr_path),
                        error=f"agent exceeded {request.timeout_seconds}s timeout",
                        timed_out=True,
                    )
                try:
                    item = line_queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                if item is None:
                    completed_streams += 1
                    continue
                stream_name, raw_line = item
                event = None
                safe_line = redact_text(raw_line)
                if stream_name == "stdout":
                    parsed = self.parse_event(raw_line)
                    if parsed.get("type") == "text" and parsed.get("message") == redact_text(raw_line):
                        event = parsed
                        safe_line = redact_text(raw_line)
                    else:
                        event = redact(parsed)
                        safe_line = json.dumps(event, sort_keys=True, ensure_ascii=False)
                    if self.contains_hidden_reasoning(event):
                        safe_line = json.dumps(
                            {"type": "reasoning_omitted", "message": "reasoning event not persisted"}
                        )
                        event = {"type": "reasoning_omitted", "message": "reasoning event omitted"}
                handle = stdout_handle if stream_name == "stdout" else stderr_handle
                handle.write(safe_line + "\n")
                handle.flush()
                if stream_name == "stdout":
                    assert event is not None
                    session_id = self.session_id_from_event(event) or session_id
                    candidate_message = self.final_message_from_event(event)
                    candidate_handoff = self._parse_handoff(candidate_message)
                    if candidate_handoff is not None:
                        final_message = json.dumps(candidate_handoff, indent=2, sort_keys=True) + "\n"
                        final_path.write_text(final_message, encoding="utf-8")
                    elif candidate_message:
                        final_message = candidate_message
                    if event_callback:
                        event_callback(stream_name, event)
                elif event_callback:
                    event_callback(stream_name, {"type": "stderr", "message": safe_line})

        exit_code = self._process.wait()
        for thread in threads:
            thread.join(timeout=1)
        self._process.stdout.close()
        self._process.stderr.close()
        if final_path.exists():
            raw_final = final_path.read_text(encoding="utf-8")
            parsed_final = self._parse_handoff(raw_final)
            if parsed_final is not None:
                final_message = json.dumps(redact(parsed_final), indent=2, sort_keys=True) + "\n"
            else:
                final_message = redact_text(raw_final)
            final_path.write_text(final_message, encoding="utf-8")
        handoff = self._parse_handoff(final_message)
        if handoff is None and request.role == "implementer":
            handoff = self._extract_last_json_object(final_message)
        if exit_code != 0:
            status = "FAILED"
            error = f"agent exited with code {exit_code}"
        elif handoff is None:
            status = "FAILED"
            error = "agent exited successfully without a valid structured final output"
        else:
            status = "COMPLETED"
            error = ""
        return AdapterResult(
            status=status,
            exit_code=exit_code,
            session_id=session_id,
            final_message=final_message,
            handoff=handoff,
            stdout_log=str(stdout_path),
            stderr_log=str(stderr_path),
            error=error,
        )

    def terminate(self) -> None:
        process = self._process
        if process is None or process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
