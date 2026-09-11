"""Observational tmux dashboard driven only by durable event records."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path


def render_event(event: dict) -> str:
    role = str(event.get("role", "SYSTEM")).upper()
    message = str(event.get("message", ""))
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    identity = []
    for key in ("run_id", "state"):
        if event.get(key):
            identity.append(str(event[key]))
    details = []
    for key in (
        "model",
        "elapsed_seconds",
        "result_sha",
        "finding_id",
        "severity",
        "verdict",
    ):
        if data.get(key):
            details.append(f"{key}={data[key]}")
    identity_suffix = "".join(f"[{item}]" for item in identity)
    suffix = f" ({', '.join(details)})" if details else ""
    return f"[{role}]{identity_suffix} {message}{suffix}"


def pane(run_root: Path, role: str, *, follow: bool = True, poll_seconds: float = 0.5) -> None:
    events_path = run_root / "events.jsonl"
    seen = 0
    role = role.upper()
    while True:
        if events_path.exists():
            lines = events_path.read_text(encoding="utf-8").splitlines()
            for raw in lines[seen:]:
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    print("[SYSTEM] malformed event record", flush=True)
                    continue
                event_role = str(event.get("role", "")).upper()
                if event_role == role or (role == "ORCHA" and event_role == "SYSTEM"):
                    print(render_event(event), flush=True)
            seen = len(lines)
        if not follow:
            return
        time.sleep(poll_seconds)


def attach(run_id: str, runtime_root: Path) -> None:
    run_root = runtime_root / "runs" / run_id
    if not (run_root / "state.json").exists():
        raise ValueError(f"unknown run ID: {run_id}")
    session = f"cvb-{run_id}"[:48]
    exists = (
        subprocess.run(["tmux", "has-session", "-t", session], capture_output=True, check=False).returncode
        == 0
    )
    if not exists:
        base = [
            sys.executable,
            "-m",
            "tools.dev_orchestrator",
            "tmux-pane",
            "--run-id",
            run_id,
        ]
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", session, *base, "--role", "ORCHA"],
            check=True,
        )
        subprocess.run(
            ["tmux", "split-window", "-h", "-t", session, *base, "--role", "IMPLEMENTER"],
            check=True,
        )
        subprocess.run(
            ["tmux", "split-window", "-v", "-t", session, *base, "--role", "REVIEWER"],
            check=True,
        )
        subprocess.run(["tmux", "select-layout", "-t", session, "main-vertical"], check=True)
    subprocess.run(["tmux", "attach-session", "-t", session], check=True)
