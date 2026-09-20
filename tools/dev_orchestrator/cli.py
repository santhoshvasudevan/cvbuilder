"""Command-line interface for the external development orchestrator."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import ConfigError, load_config
from .controller import ControllerError, OrchestrationController
from .doctor import run_doctor
from .state import StateStore
from .tmux_ui import attach, pane


def _root() -> Path:
    return Path.cwd().resolve()


def _runtime(root: Path) -> Path:
    return root / ".orchestration"


def _controller(root: Path) -> OrchestrationController:
    runtime = _runtime(root)
    config = load_config(runtime / "config.yaml")
    return OrchestrationController(repository_root=root, runtime_root=runtime, config=config)


def _latest_run(runtime: Path, phase: str, state_name: str | None = None) -> str:
    candidates = []
    runs = runtime / "runs"
    if runs.exists():
        for state_path in runs.glob("*/state.json"):
            try:
                state = StateStore(state_path).load()
            except Exception:
                continue
            if state.phase == phase and (state_name is None or state.state == state_name):
                candidates.append((state.updated_at, state.run_id))
    if not candidates:
        raise ControllerError(f"no matching {phase} run found")
    return sorted(candidates)[-1][1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dev-orchestrator")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate-config")
    sub.add_parser("doctor")
    plan_parser = sub.add_parser("plan")
    plan_parser.add_argument("--phase", required=True)
    plan_parser.add_argument("--dry-run", action="store_true")
    approve_parser = sub.add_parser("approve")
    approve_parser.add_argument("--run-id", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--phase", required=True)
    run_parser.add_argument("--run-id")
    run_parser.add_argument("--dry-run", action="store_true")
    status_parser = sub.add_parser("status")
    status_parser.add_argument("--run-id", required=True)
    attach_parser = sub.add_parser("attach")
    attach_parser.add_argument("--run-id", required=True)
    resume_parser = sub.add_parser("resume")
    resume_parser.add_argument("--run-id", required=True)
    decision_parser = sub.add_parser("record-decision")
    decision_parser.add_argument("--run-id", required=True)
    decision_parser.add_argument("--file", required=True, type=Path)
    supplemental_parser = sub.add_parser("record-supplemental-audit")
    supplemental_parser.add_argument("--run-id", required=True)
    supplemental_parser.add_argument("--file", required=True, type=Path)
    abort_parser = sub.add_parser("abort")
    abort_parser.add_argument("--run-id", required=True)
    pane_parser = sub.add_parser("tmux-pane", help=argparse.SUPPRESS)
    pane_parser.add_argument("--run-id", required=True)
    pane_parser.add_argument("--role", required=True, choices=["ORCHA", "IMPLEMENTER", "REVIEWER"])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = _root()
    runtime = _runtime(root)
    try:
        config = load_config(runtime / "config.yaml")
        if args.command == "validate-config":
            print("configuration valid (version 1)")
            return 0
        if args.command == "doctor":
            checks = run_doctor(root, runtime, config)
            for check in checks:
                print(f"{check.status:4} {check.name}: {check.detail}")
            return 1 if any(check.status == "FAIL" for check in checks) else 0
        controller = OrchestrationController(repository_root=root, runtime_root=runtime, config=config)
        if args.command == "plan":
            run_id, contract = controller.plan(args.phase, dry_run=args.dry_run)
            print(
                json.dumps(
                    {"run_id": run_id, "contract": str(controller.paths(run_id).contract_json)},
                    indent=2,
                )
            )
            return 0
        if args.command == "approve":
            state = controller.approve(args.run_id)
            print(json.dumps({"run_id": state.run_id, "state": state.state}, indent=2))
            return 0
        if args.command == "run":
            if args.dry_run:
                run_id, _ = controller.plan(args.phase, dry_run=True)
                print(
                    json.dumps(
                        {
                            "run_id": run_id,
                            "state": "AWAITING_PHASE_APPROVAL",
                            "live_agents_invoked": False,
                            "contract": str(controller.paths(run_id).contract_json),
                        },
                        indent=2,
                    )
                )
                return 0
            run_id = args.run_id or _latest_run(runtime, args.phase, "IMPLEMENTING")
            state = controller.execute(run_id)
            print(json.dumps({"run_id": run_id, "state": state.state}, indent=2))
            return 0 if state.state == "COMPLETED" else 2
        if args.command == "status":
            state = StateStore(controller.paths(args.run_id).state).load()
            print(json.dumps(state.__dict__, indent=2, sort_keys=True))
            return 0
        if args.command == "attach":
            attach(args.run_id, runtime)
            return 0
        if args.command == "resume":
            state = controller.execute(args.run_id, resume=True)
            print(json.dumps({"run_id": args.run_id, "state": state.state}, indent=2))
            return 0 if state.state == "COMPLETED" else 2
        if args.command == "record-decision":
            try:
                decision = json.loads(args.file.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ControllerError(f"invalid operator-decision JSON: {exc}") from exc
            state, artifact = controller.record_operator_decision(args.run_id, decision)
            print(
                json.dumps(
                    {
                        "run_id": args.run_id,
                        "state": state.state,
                        "operator_decision": str(artifact),
                    },
                    indent=2,
                )
            )
            return 0
        if args.command == "record-supplemental-audit":
            artifact = controller.record_supplemental_audit(args.run_id, args.file)
            print(
                json.dumps(
                    {
                        "run_id": args.run_id,
                        "artifact": str(artifact),
                        "relative_path": str(
                            artifact.relative_to(controller.paths(args.run_id).root)
                        ),
                    },
                    indent=2,
                )
            )
            return 0
        if args.command == "abort":
            state = controller.abort(args.run_id)
            print(json.dumps({"run_id": args.run_id, "state": state.state}, indent=2))
            return 0
        if args.command == "tmux-pane":
            pane(runtime / "runs" / args.run_id, args.role)
            return 0
    except (ConfigError, ControllerError, ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 2
