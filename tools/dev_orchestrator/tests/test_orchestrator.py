from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from tools.dev_orchestrator.adapters import AdapterResult, ClaudeAdapter, CursorAdapter, FakeAdapter
from tools.dev_orchestrator.adapters.base import AdapterRequest, ProcessAdapter
from tools.dev_orchestrator.config import ConfigError, load_config
from tools.dev_orchestrator.controller import ControllerError, OrchestrationController, _atomic_json
from tools.dev_orchestrator.events import EventLog
from tools.dev_orchestrator.evidence import EvidenceCollector, EvidenceError
from tools.dev_orchestrator.git_safety import GitBoundary, GitRepository, GitSafetyError
from tools.dev_orchestrator.invocation_metrics import InvocationMetrics
from tools.dev_orchestrator.redaction import REDACTED, redact, redact_text
from tools.dev_orchestrator.schemas import (
    SchemaError,
    validate_audit_response,
    validate_implementer_response,
    validate_operator_decision,
    validate_phase_contract,
)
from tools.dev_orchestrator.state import RunState, RunStateName, StateStore
from tools.dev_orchestrator.tmux_ui import render_event

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
BASE = "a" * 40
RESULT = "b" * 40
CORRECTED = "e" * 40
REPAIRED = "d" * 40
M3A_REQUIREMENTS = [
    "FACT-002",
    "FACT-003",
    "FACT-005",
    "CTX-004",
    "STATIC-001",
    "STATIC-002",
    "STATIC-003",
    "LLM-012",
    "requirements.md §6.1",
]
M3A_DOCUMENTATION = [
    "docs/CURRENT_STATE.md",
    "docs/IMPLEMENTATION_PLAN.md",
    "docs/TEST_STRATEGY.md",
    "docs/REQUIREMENT_TRACEABILITY.md",
]


def implementer(status: str = "IMPLEMENTED", result_sha: str = RESULT) -> dict:
    return {
        "schema_version": 1,
        "status": status,
        "base_sha": BASE,
        "result_sha": result_sha if status == "IMPLEMENTED" else "",
        "session_id": "cursor-session",
        "requirements_addressed": M3A_REQUIREMENTS,
        "files_changed": ["candidate_memory/models.py"],
        "migrations": ["candidate_memory/migrations/0001_initial.py"],
        "tests_added": ["candidate_memory/tests/test_models.py"],
        "test_commands": [
            {"command": ".venv/bin/python manage.py test candidate_memory", "exit_code": 0},
            {"command": "make migrations-check", "exit_code": 0},
            {"command": "make verify", "exit_code": 0},
            {"command": "make secrets", "exit_code": 0},
            {"command": "git diff --check", "exit_code": 0},
        ],
        "documentation_updated": M3A_DOCUMENTATION,
        "known_gaps": [],
        "questions": ["Which design?"] if status == "QUESTION" else [],
        "decisions_required": [],
        "summary": "implemented" if status == "IMPLEMENTED" else "question",
    }


def finding(finding_id: str = "AUD-001", status: str = "OPEN") -> dict:
    return {
        "finding_id": finding_id,
        "severity": "HIGH",
        "requirement_ids": ["STATIC-001"],
        "decision_ids": ["V2-D024"],
        "location": "candidate_memory/models.py:1",
        "explanation": "finding",
        "required_correction": "correct it",
        "test_added": False,
        "test_commit_sha": "",
        "status": status,
    }


def audit(
    verdict: str = "PASS",
    findings: list[dict] | None = None,
    audit_test_commit_sha: str = "",
    audit_test_requested: bool = False,
) -> dict:
    return {
        "schema_version": 1,
        "verdict": verdict,
        "base_sha": BASE,
        "candidate_sha": RESULT,
        "findings": findings or [],
        "test_commands": [{"command": "make verify", "exit_code": 0}],
        "summary": verdict.lower(),
        "audit_test_requested": audit_test_requested,
        "audit_test_commit_sha": audit_test_commit_sha,
    }


def closure() -> dict:
    return {
        "schema_version": 1,
        "base_sha": BASE,
        "final_sha": RESULT,
        "accepted_requirement_ids": M3A_REQUIREMENTS,
        "closed_findings": [],
        "unresolved_findings": [],
        "residual_risks": [],
        "deterministic_verification_results": ["make verify: 0"],
        "documentation_status": "updated",
        "merge_recommendation": "MERGE",
        "no_merge_or_push_confirmed": True,
        "summary": "ready for operator",
    }


class FakeRepository:
    registered_worktrees: list[Path]

    def __init__(self):
        self.registered_worktrees = []

    def verify_bootstrap_boundary(self, **kwargs) -> GitBoundary:
        del kwargs
        return GitBoundary(
            branch="buildwithAgent",
            head=BASE,
            clean=True,
            default_branch_is_ancestor=True,
            bootstrap_base_is_ancestor=True,
            excluded_ancestors=(),
        )

    def create_audit_worktree(self, path: Path, candidate_sha: str) -> None:
        del candidate_sha
        path.mkdir(parents=True)
        docs = path / "docs"
        docs.mkdir(exist_ok=True)
        (docs / "CURRENT_STATE.md").write_text(
            "audit worktree current state fixture\n",
            encoding="utf-8",
        )

    def assert_isolated_worktrees(self, implementation: Path, audit: Path) -> None:
        if implementation.resolve() == audit.resolve():
            raise AssertionError("worktrees were not isolated")

    def create_audit_branch(self, path: Path, branch: str, candidate_sha: str) -> None:
        del path, branch, candidate_sha

    def validate_audit_test_commit(self, path: Path, candidate_sha: str, commit_sha: str):
        del path, candidate_sha, commit_sha
        return ("candidate_memory/tests/test_adversarial.py",)

    def commit_exists(self, sha: str) -> bool:
        return bool(sha)

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        return bool(ancestor and descendant)

    def worktrees(self):
        return [{"worktree": str(path)} for path in self.registered_worktrees]

    def head(self, cwd=None):
        del cwd
        return BASE

    def branch(self, cwd=None):
        del cwd
        return "agent/m3a-test/implementation"

    def status(self, cwd=None):
        del cwd
        return []

    def run(self, *args, cwd=None):
        del cwd
        if args[:2] == ("diff", "--stat"):
            return "candidate_memory/models.py | 1 +"
        if args[:2] == ("diff", "--name-only"):
            return "tools/dev_orchestrator/controller.py"
        return ""


class AcceptEvidence:
    def collect_and_validate(self, **kwargs):
        return {"accepted": True, "result_sha": kwargs["result_sha"]}

    def validate_test_commands(self, worktree, reported):
        del worktree
        return tuple((item["command"], item["exit_code"]) for item in reported)


class RejectEvidence:
    def __init__(self, message: str):
        self.message = message

    def collect_and_validate(self, **kwargs):
        del kwargs
        raise EvidenceError(self.message)

    def validate_test_commands(self, worktree, reported):
        del worktree, reported
        raise EvidenceError(self.message)


class TestController(OrchestrationController):
    def _prepare_worktrees(self, state, paths):
        del state
        worktree = paths.worktrees / "implementation"
        worktree.mkdir(parents=True, exist_ok=True)
        docs = worktree / "docs"
        docs.mkdir(exist_ok=True)
        current_state = docs / "CURRENT_STATE.md"
        if not current_state.exists():
            source = REPOSITORY_ROOT / "docs" / "CURRENT_STATE.md"
            current_state.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        return worktree, None


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runtime = Path(self.temp.name) / ".orchestration"
        self.config = load_config(REPOSITORY_ROOT / ".orchestration/config.yaml")

    def make_controller(
        self,
        *,
        implementer_responses,
        reviewer_responses,
        orcha_responses=(),
        closure_responses=None,
        evidence=None,
        config=None,
    ):
        adapters = {
            "implementer": FakeAdapter(implementer_responses),
            "reviewer": FakeAdapter(reviewer_responses),
            "orcha": FakeAdapter(orcha_responses),
            "orcha_closure": FakeAdapter(closure_responses or [closure()]),
        }
        controller = TestController(
            repository_root=REPOSITORY_ROOT,
            runtime_root=self.runtime,
            config=config or self.config,
            adapters=adapters,
            evidence_collector=evidence or AcceptEvidence(),
        )
        controller.repository = FakeRepository()
        return controller, adapters

    def prepare_run(self, controller, state_name=RunStateName.IMPLEMENTING, session_id="", run_id="m3a-test"):
        paths = controller.paths(run_id)
        paths.root.mkdir(parents=True, exist_ok=True)
        paths.handoffs.mkdir(exist_ok=True)
        paths.supplemental_audits.mkdir(exist_ok=True)
        paths.logs.mkdir(exist_ok=True)
        contract = json.loads(
            (REPOSITORY_ROOT / ".orchestration/contracts/M3A.json").read_text(encoding="utf-8")
        )
        contract.update(
            {
                "run_id": run_id,
                "base_sha": BASE,
                "future_implementation_base_sha": BASE,
            }
        )
        _atomic_json(paths.contract_json, contract)
        state = RunState(run_id=run_id, phase="M3A", state=state_name.value, base_sha=BASE)
        if session_id:
            state.sessions["implementer"] = session_id
        StateStore(paths.state).save(state)
        return run_id

    @staticmethod
    def seed_worktree_current_state(worktree: Path, content: str | None = None) -> Path:
        docs = worktree / "docs"
        docs.mkdir(parents=True, exist_ok=True)
        target = docs / "CURRENT_STATE.md"
        if content is None:
            content = (REPOSITORY_ROOT / "docs" / "CURRENT_STATE.md").read_text(encoding="utf-8")
        target.write_text(content, encoding="utf-8")
        return target

    def test_implementation_audit_pass_closure(self):
        controller, _ = self.make_controller(
            implementer_responses=[implementer()], reviewer_responses=[audit()]
        )
        state = controller.execute(self.prepare_run(controller))
        self.assertEqual(state.state, "COMPLETED")

    def test_dry_run_plan_cannot_be_approved(self):
        controller, _ = self.make_controller(implementer_responses=[], reviewer_responses=[])
        run_id = self.prepare_run(controller, state_name=RunStateName.AWAITING_PHASE_APPROVAL)
        _atomic_json(
            controller.paths(run_id).manifest,
            {"schema_version": 1, "run_id": run_id, "dry_run": True},
        )

        with self.assertRaisesRegex(ControllerError, "dry-run plans cannot be approved"):
            controller.approve(run_id)

    def test_question_orcha_answer_resume(self):
        controller, adapters = self.make_controller(
            implementer_responses=[implementer("QUESTION"), implementer()],
            reviewer_responses=[audit()],
            orcha_responses=[{"decision": "ANSWER", "answer": "Use a reference."}],
        )
        state = controller.execute(self.prepare_run(controller))
        self.assertEqual(state.state, "COMPLETED")
        self.assertEqual(state.question_cycles, 1)
        self.assertEqual(len(adapters["implementer"].requests), 2)

    def test_correction_then_pass(self):
        controller, _ = self.make_controller(
            implementer_responses=[implementer(), implementer()],
            reviewer_responses=[audit("CORRECTION_REQUIRED", [finding()]), audit()],
            orcha_responses=[{"decision": "CORRECT", "prompt": "Fix AUD-001", "finding_ids": ["AUD-001"]}],
        )
        state = controller.execute(self.prepare_run(controller))
        self.assertEqual(state.state, "COMPLETED")
        self.assertEqual(state.correction_cycles, 1)

    def test_maximum_correction_cycles_escalates(self):
        limits = dataclasses.replace(self.config.limits, max_correction_cycles=3)
        config = dataclasses.replace(self.config, limits=limits)
        reviews = [audit("CORRECTION_REQUIRED", [finding(f"AUD-{index:03d}")]) for index in range(1, 5)]
        corrections = [
            {
                "decision": "CORRECT",
                "prompt": f"fix {index}",
                "finding_ids": [f"AUD-{index:03d}"],
            }
            for index in range(1, 4)
        ]
        controller, _ = self.make_controller(
            implementer_responses=[implementer()] * 4,
            reviewer_responses=reviews,
            orcha_responses=corrections,
            config=config,
        )
        state = controller.execute(self.prepare_run(controller))
        self.assertEqual(state.state, "OPERATOR_ESCALATION")
        self.assertIn("maximum correction", state.last_error)

    def test_repeated_finding_escalates_after_two_corrections(self):
        repeated = audit("CORRECTION_REQUIRED", [finding("AUD-STABLE")])
        correction = {
            "decision": "CORRECT",
            "prompt": "fix stable",
            "finding_ids": ["AUD-STABLE"],
        }
        controller, _ = self.make_controller(
            implementer_responses=[implementer()] * 3,
            reviewer_responses=[repeated, repeated, repeated],
            orcha_responses=[correction, correction],
        )
        state = controller.execute(self.prepare_run(controller))
        self.assertEqual(state.state, "OPERATOR_ESCALATION")
        self.assertIn("survived two corrections", state.last_error)

    def test_malformed_implementer_json_escalates(self):
        controller, _ = self.make_controller(
            implementer_responses=[{"status": "IMPLEMENTED"}], reviewer_responses=[]
        )
        state = controller.execute(self.prepare_run(controller))
        self.assertEqual(state.state, "OPERATOR_ESCALATION")

    def test_malformed_reviewer_json_escalates(self):
        controller, _ = self.make_controller(
            implementer_responses=[implementer()], reviewer_responses=[{"verdict": "PASS"}]
        )
        state = controller.execute(self.prepare_run(controller))
        self.assertEqual(state.state, "OPERATOR_ESCALATION")

    def test_failed_reviewer_command_preserves_correction_required_findings(self):
        review = audit("CORRECTION_REQUIRED", [finding()])
        review["test_commands"] = [
            {"command": ".venv/bin/python manage.py test candidate_memory", "exit_code": 1},
            {"command": "git diff --check", "exit_code": 0},
        ]
        controller, adapters = self.make_controller(
            implementer_responses=[implementer(), implementer()],
            reviewer_responses=[review, audit()],
            orcha_responses=[
                {
                    "decision": "CORRECT",
                    "prompt": "Correct AUD-001 without weakening tests.",
                    "finding_ids": ["AUD-001"],
                }
            ],
        )

        state = controller.execute(self.prepare_run(controller))

        self.assertEqual(state.state, "COMPLETED")
        paths = controller.paths(state.run_id)
        saved = json.loads((paths.handoffs / "audit-00.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["findings"][0]["finding_id"], "AUD-001")
        verification = json.loads(
            (paths.handoffs / "audit-verification-00.json").read_text(encoding="utf-8")
        )
        self.assertEqual(verification["failed_reviewer_commands"], review["test_commands"][:1])
        self.assertIn("AUD-001", adapters["orcha"].requests[0].prompt)

    def test_failed_reviewer_command_cannot_support_pass(self):
        review = audit()
        review["test_commands"] = [{"command": "make verify", "exit_code": 1}]
        controller, _ = self.make_controller(
            implementer_responses=[implementer()], reviewer_responses=[review]
        )

        state = controller.execute(self.prepare_run(controller))

        self.assertEqual(state.state, "OPERATOR_ESCALATION")
        self.assertEqual(state.last_error, "reviewer reported a failed deterministic test command")

    def test_nonzero_process_exit_escalates(self):
        failure = AdapterResult(status="FAILED", exit_code=7, error="agent exited with code 7")
        controller, _ = self.make_controller(implementer_responses=[failure], reviewer_responses=[])
        state = controller.execute(self.prepare_run(controller))
        self.assertIn("code 7", state.last_error)

    def test_timeout_escalates(self):
        timeout = AdapterResult(status="TIMEOUT", timed_out=True, error="timeout")
        controller, _ = self.make_controller(implementer_responses=[timeout], reviewer_responses=[])
        state = controller.execute(self.prepare_run(controller))
        self.assertEqual(state.state, "OPERATOR_ESCALATION")

    def test_success_without_final_output_escalates(self):
        missing = AdapterResult(status="FAILED", exit_code=0, error="missing structured final output")
        controller, _ = self.make_controller(implementer_responses=[missing], reviewer_responses=[])
        state = controller.execute(self.prepare_run(controller))
        self.assertIn("missing structured", state.last_error)

    def assert_evidence_rejection(self, message):
        controller, _ = self.make_controller(
            implementer_responses=[implementer()],
            reviewer_responses=[],
            evidence=RejectEvidence(message),
        )
        state = controller.execute(self.prepare_run(controller))
        self.assertEqual(state.state, "OPERATOR_ESCALATION")
        self.assertIn(message, state.last_error)

    def test_dirty_worktree_rejected(self):
        self.assert_evidence_rejection("worktree dirty")

    def test_invalid_result_sha_rejected(self):
        self.assert_evidence_rejection("result SHA does not exist")

    def test_unauthorized_path_change_rejected(self):
        self.assert_evidence_rejection("prohibited path changed")

    def test_unauthorized_requirements_modification_rejected(self):
        self.assert_evidence_rejection("requirements changed")

    def test_audit_test_commit_requires_operator_gate(self):
        controller, _ = self.make_controller(
            implementer_responses=[implementer()],
            reviewer_responses=[audit("CORRECTION_REQUIRED", [finding()], "c" * 40)],
        )
        state = controller.execute(self.prepare_run(controller))
        self.assertEqual(state.state, "OPERATOR_ESCALATION")
        self.assertIn("transfer approval", state.last_error)

    def test_read_only_audit_can_request_isolated_test_commit(self):
        requested_finding = finding()
        committed_finding = finding()
        committed_finding.update({"test_added": True, "test_commit_sha": "c" * 40})
        controller, adapters = self.make_controller(
            implementer_responses=[implementer()],
            reviewer_responses=[
                audit(
                    "CORRECTION_REQUIRED",
                    [requested_finding],
                    audit_test_requested=True,
                ),
                audit(
                    "CORRECTION_REQUIRED",
                    [committed_finding],
                    "c" * 40,
                ),
            ],
        )

        state = controller.execute(self.prepare_run(controller))

        self.assertEqual(state.state, "OPERATOR_ESCALATION")
        self.assertIn("ready", state.last_error)
        self.assertFalse(adapters["reviewer"].requests[0].allow_write)
        self.assertTrue(adapters["reviewer"].requests[1].allow_write)
        self.assertTrue(adapters["reviewer"].requests[1].safety_verified)

    def test_interrupted_run_resumes_saved_cursor_session(self):
        controller, adapters = self.make_controller(
            implementer_responses=[implementer()], reviewer_responses=[audit()]
        )
        state = controller.execute(self.prepare_run(controller, session_id="persisted-session"), resume=True)
        self.assertEqual(state.state, "COMPLETED")
        self.assertEqual(adapters["implementer"].requests[0].session_id, "persisted-session")

    def test_interrupted_question_state_resumes_orcha_decision(self):
        controller, adapters = self.make_controller(
            implementer_responses=[implementer()],
            reviewer_responses=[audit()],
            orcha_responses=[{"decision": "ANSWER", "answer": "Use a reference."}],
        )
        run_id = self.prepare_run(controller, state_name=RunStateName.IMPLEMENTER_QUESTION)
        paths = controller.paths(run_id)
        _atomic_json(paths.handoffs / "implementer-00.json", implementer("QUESTION"))
        state = StateStore(paths.state).load()
        state.question_cycles = 1
        state.sessions["implementer"] = "cursor-session"
        StateStore(paths.state).save(state)

        resumed = controller.execute(run_id, resume=True)

        self.assertEqual(resumed.state, "COMPLETED")
        self.assertEqual(len(adapters["orcha"].requests), 1)

    def test_interrupted_closure_state_reloads_audit_handoff(self):
        controller, adapters = self.make_controller(
            implementer_responses=[], reviewer_responses=[], closure_responses=[closure()]
        )
        run_id = self.prepare_run(controller, state_name=RunStateName.CLOSURE_REVIEW)
        paths = controller.paths(run_id)
        state = StateStore(paths.state).load()
        state.result_sha = RESULT
        StateStore(paths.state).save(state)
        _atomic_json(paths.handoffs / "audit-00.json", audit())
        (paths.worktrees / "audit-00").mkdir(parents=True)
        self.seed_worktree_current_state(paths.worktrees / "audit-00")

        resumed = controller.execute(run_id, resume=True)

        self.assertEqual(resumed.state, "COMPLETED")
        self.assertIn('"verdict": "PASS"', adapters["orcha_closure"].requests[0].prompt)

    def test_operator_decision_recovers_same_run_and_resumes_saved_cursor_session(self):
        generated_prompt = (
            "Preserve completed candidate_memory work. Modify only "
            "job_applications/tests/test_settings.py and original paths; run the targeted test and "
            "make verify; fix causes without weakening tests; commit M3A; return a concise handoff. "
            "Use IMPLEMENTED only on success, otherwise BLOCKED or QUESTION. Never merge, push, "
            "rebase, reset, or modify the main checkout."
        )
        controller, adapters = self.make_controller(
            implementer_responses=[implementer()],
            reviewer_responses=[audit()],
            orcha_responses=[
                {
                    "decision": "CORRECT",
                    "prompt": generated_prompt,
                    "finding_ids": ["OPERATOR-DECISION-01"],
                }
            ],
        )
        run_id = self.prepare_run(
            controller,
            state_name=RunStateName.OPERATOR_ESCALATION,
            session_id="persisted-session",
        )
        paths = controller.paths(run_id)
        implementation_path = paths.worktrees / "implementation"
        implementation_path.mkdir(parents=True)
        controller.repository.registered_worktrees.append(implementation_path)  # type: ignore[attr-defined]
        question = implementer("QUESTION")
        assistant_event = {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": f"```json\n{json.dumps(question)}\n```"}]
            },
            "session_id": "persisted-session",
        }
        (paths.logs / "implementer-001.stdout.log").write_text(
            json.dumps(assistant_event) + "\n"
            + json.dumps({"type": "result", "result": "{…[TRUNCATED]"})
            + "\n",
            encoding="utf-8",
        )
        original_contract = paths.contract_json.read_bytes()
        decision = {
            "run_id": run_id,
            "decision": "APPROVED",
            "reason": "candidate_memory is now implemented",
            "additional_allowed_paths": ["job_applications/tests/test_settings.py"],
            "constraints": [
                "Remove only candidate_memory from the not-yet-built boundary.",
                "Preserve unrelated assertions.",
            ],
        }

        recorded, decision_path = controller.record_operator_decision(run_id, decision)
        self.assertEqual(recorded.state, "ORCHA_DECISION")
        self.assertEqual(paths.contract_json.read_bytes(), original_contract)
        self.assertEqual(json.loads(decision_path.read_text(encoding="utf-8")), decision)

        resumed = controller.execute(run_id, resume=True)

        self.assertEqual(resumed.state, "COMPLETED")
        self.assertEqual(resumed.correction_cycles, 1)
        self.assertEqual(adapters["implementer"].requests[0].session_id, "persisted-session")
        self.assertIn(generated_prompt, adapters["implementer"].requests[0].prompt)
        self.assertIn(
            "## Startup identity (verify before acting)",
            adapters["implementer"].requests[0].prompt,
        )
        self.assertEqual(adapters["implementer"].requests[0].workdir, implementation_path)
        self.assertIn("job_applications/tests/test_settings.py", adapters["orcha"].requests[0].prompt)
        self.assertEqual(len(list(paths.worktrees.glob("implementation"))), 1)
        self.assertTrue((paths.root / resumed.latest_orcha_prompt_path).exists())
        events = [json.loads(line)["event"] for line in paths.events.read_text().splitlines()]
        self.assertIn("operator_decision_recorded", events)
        self.assertIn("orcha_correction_prompt_generated", events)
        self.assertIn("cursor_resume_started", events)

    def test_pending_operator_decision_can_retry_orcha_after_escalation(self):
        generated_prompt = (
            "Preserve candidate_memory and update job_applications/tests/test_settings.py. Run the "
            "targeted test and make verify, then commit and return a handoff using IMPLEMENTED, "
            "BLOCKED, or QUESTION. "
            "Never merge, push, rebase, reset, or modify/check out `main`."
        )
        controller, adapters = self.make_controller(
            implementer_responses=[implementer()],
            reviewer_responses=[audit()],
            orcha_responses=[
                {
                    "decision": "CORRECT",
                    "prompt": generated_prompt,
                    "finding_ids": ["OPERATOR-DECISION-01"],
                }
            ],
        )
        run_id = self.prepare_run(
            controller,
            state_name=RunStateName.OPERATOR_ESCALATION,
            session_id="persisted-session",
        )
        paths = controller.paths(run_id)
        implementation_path = paths.worktrees / "implementation"
        implementation_path.mkdir(parents=True)
        controller.repository.registered_worktrees.append(implementation_path)  # type: ignore[attr-defined]
        _atomic_json(paths.handoffs / "implementer-00.json", implementer("QUESTION"))
        decision = {
            "run_id": run_id,
            "decision": "APPROVED",
            "reason": "boundary advanced",
            "additional_allowed_paths": ["job_applications/tests/test_settings.py"],
            "constraints": ["Preserve unrelated assertions."],
        }
        _atomic_json(paths.handoffs / "operator-decision-01.json", decision)
        state = StateStore(paths.state).load()
        state.operator_decision_count = 1
        state.pending_operator_decision_path = "handoffs/operator-decision-01.json"
        state.recovered_handoff_path = "handoffs/implementer-00.json"
        StateStore(paths.state).save(state)

        resumed = controller.execute(run_id, resume=True)

        self.assertEqual(resumed.state, "COMPLETED")
        self.assertIn(generated_prompt, adapters["implementer"].requests[0].prompt)
        events = [json.loads(line)["event"] for line in paths.events.read_text().splitlines()]
        self.assertIn("operator_recovery_retry", events)

    def test_failed_reviewer_can_retry_same_audit_after_tooling_repair(self):
        controller, adapters = self.make_controller(
            implementer_responses=[], reviewer_responses=[audit()]
        )
        run_id = self.prepare_run(controller, state_name=RunStateName.OPERATOR_ESCALATION)
        paths = controller.paths(run_id)
        implementation = paths.worktrees / "implementation"
        audit_worktree = paths.worktrees / "audit-00"
        implementation.mkdir(parents=True)
        audit_worktree.mkdir(parents=True)
        self.seed_worktree_current_state(implementation)
        self.seed_worktree_current_state(audit_worktree)
        controller.repository.registered_worktrees.extend([implementation, audit_worktree])  # type: ignore[attr-defined]
        state = StateStore(paths.state).load()
        state.result_sha = RESULT
        state.controller_sha = "c" * 40
        state.last_error = "agent exited with code 1"
        StateStore(paths.state).save(state)
        _atomic_json(paths.evidence, {"accepted": True, "result_sha": RESULT})
        EventLog(paths.events).emit(
            run_id=run_id,
            role="REVIEWER",
            state=RunStateName.OPERATOR_ESCALATION.value,
            event="agent_failure",
            message=state.last_error,
        )
        repaired = "d" * 40
        boundary = GitBoundary(
            branch="buildwithAgent",
            head=repaired,
            clean=True,
            default_branch_is_ancestor=True,
            bootstrap_base_is_ancestor=True,
            excluded_ancestors=(),
        )

        boundary_patch = mock.patch.object(
            controller, "_verify_repository_boundary", return_value=boundary
        )
        head_patch = mock.patch.object(
            controller.repository, "head", side_effect=lambda cwd=None: RESULT if cwd else repaired
        )
        with boundary_patch, head_patch:
            resumed = controller.execute(run_id, resume=True)

        self.assertEqual(resumed.state, "COMPLETED")
        self.assertEqual(resumed.controller_sha, repaired)
        self.assertEqual(adapters["reviewer"].requests[0].workdir, audit_worktree)
        event_records = [json.loads(line) for line in paths.events.read_text().splitlines()]
        self.assertIn("reviewer_recovery_retry", [item["event"] for item in event_records])
        reviewer_metrics = [
            item
            for item in event_records
            if item["event"] == "agent_invocation_metrics" and item["role"] == "REVIEWER"
        ]
        self.assertEqual(
            reviewer_metrics[0]["data"]["retry_reason"], "reviewer_tooling_recovery"
        )

    def _prepare_implementer_strict_output_escalation(
        self,
        controller,
        *,
        run_id="m3a-test",
        session_id="persisted-session",
        register_worktree=True,
        emit_failure=True,
        failure_message="agent exited successfully without a valid structured final output",
        correction_cycles=1,
        write_malformed_final=True,
        create_handoff=False,
    ):
        run_id = self.prepare_run(
            controller,
            state_name=RunStateName.OPERATOR_ESCALATION,
            session_id=session_id,
            run_id=run_id,
        )
        paths = controller.paths(run_id)
        implementation = paths.worktrees / "implementation"
        implementation.mkdir(parents=True)
        if register_worktree:
            controller.repository.registered_worktrees.append(implementation)  # type: ignore[attr-defined]
        state = StateStore(paths.state).load()
        state.result_sha = RESULT
        state.controller_sha = "c" * 40
        state.correction_cycles = correction_cycles
        state.last_error = failure_message
        if not session_id:
            state.sessions.pop("implementer", None)
        StateStore(paths.state).save(state)
        if emit_failure:
            EventLog(paths.events).emit(
                run_id=run_id,
                role="IMPLEMENTER",
                state=RunStateName.OPERATOR_ESCALATION.value,
                event="agent_failure",
                message=failure_message,
            )
        if write_malformed_final:
            malformed = (
                '```json\n{"schema_version":1,"status":"IMPLEMENTED"}\n```\n'
                "Additional prose that must not be accepted as a handoff.\n"
            )
            (paths.logs / "implementer-001.final.json").write_text(malformed, encoding="utf-8")
        if create_handoff:
            _atomic_json(
                paths.handoffs / f"implementer-{correction_cycles:02d}.json",
                implementer(result_sha=RESULT),
            )
        return run_id, paths, implementation

    def test_failed_implementer_can_retry_strict_output_after_tooling_repair(self):
        corrected_handoff = implementer(result_sha=CORRECTED)
        corrected_handoff["files_changed"] = [
            "candidate_memory/models.py",
            "candidate_memory/tests/test_models.py",
        ]
        review = audit()
        review["candidate_sha"] = CORRECTED
        closed = closure()
        closed["final_sha"] = CORRECTED
        controller, adapters = self.make_controller(
            implementer_responses=[corrected_handoff],
            reviewer_responses=[review],
            closure_responses=[closed],
        )
        run_id, paths, implementation = self._prepare_implementer_strict_output_escalation(controller)
        self.assertFalse((paths.handoffs / "implementer-01.json").exists())
        repaired_boundary = GitBoundary(
            branch="buildwithAgent",
            head=REPAIRED,
            clean=True,
            default_branch_is_ancestor=True,
            bootstrap_base_is_ancestor=True,
            excluded_ancestors=(),
        )

        def fake_run(*args, cwd=None):
            if args[:2] == ("diff", "--name-only"):
                if cwd is not None:
                    return "candidate_memory/models.py\ncandidate_memory/tests/test_models.py"
                return (
                    "tools/dev_orchestrator/controller.py\n"
                    "docs/DEVELOPMENT_ORCHESTRATION.md\n"
                    "docs/DEVELOPMENT_RUNBOOK.md"
                )
            return ""

        boundary_patch = mock.patch.object(
            controller, "_verify_repository_boundary", return_value=repaired_boundary
        )
        head_patch = mock.patch.object(
            controller.repository,
            "head",
            side_effect=lambda cwd=None: CORRECTED if cwd else REPAIRED,
        )
        run_patch = mock.patch.object(controller.repository, "run", side_effect=fake_run)
        with boundary_patch, head_patch, run_patch:
            resumed = controller.execute(run_id, resume=True)

        self.assertEqual(resumed.state, "COMPLETED")
        self.assertEqual(resumed.controller_sha, REPAIRED)
        self.assertEqual(resumed.result_sha, CORRECTED)
        self.assertEqual(len(adapters["implementer"].requests), 1)
        request = adapters["implementer"].requests[0]
        self.assertEqual(request.session_id, "persisted-session")
        self.assertEqual(request.retry_reason, "implementer_strict_output")
        self.assertEqual(request.workdir, implementation)
        self.assertIn("Do not modify any files", request.prompt)
        self.assertIn(CORRECTED, request.prompt)
        self.assertIn("candidate_memory/models.py", request.prompt)
        self.assertIn("Return only one JSON object", request.prompt)
        self.assertIn(".venv/bin/python manage.py test candidate_memory", request.prompt)
        self.assertIn("make verify", request.prompt)
        self.assertIn("test_commands must contain only real executable commands", request.prompt)
        executable_section = request.prompt.split(
            "Narrative expectations remain acceptance criteria only"
        )[0]
        self.assertNotIn("Fresh disposable PostgreSQL", executable_section)
        self.assertNotIn("Source-ingestion fixture test", executable_section)
        self.assertNotIn("Additional prose", request.prompt)
        saved = json.loads((paths.handoffs / "implementer-01.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["result_sha"], CORRECTED)
        self.assertEqual(saved["status"], "IMPLEMENTED")
        malformed = (paths.logs / "implementer-001.final.json").read_text(encoding="utf-8")
        self.assertIn("Additional prose", malformed)
        events = [json.loads(line)["event"] for line in paths.events.read_text().splitlines()]
        self.assertIn("implementer_strict_output_retry", events)
        self.assertIn("cursor_resume_started", events)
        self.assertEqual(len(list(paths.worktrees.glob("implementation"))), 1)

    def test_implementer_strict_output_retry_fail_closed_preconditions(self):
        cases = {
            "dirty_worktree": {"status": [" M candidate_memory/models.py"]},
            "missing_worktree": {"create_worktree": False},
            "unregistered_worktree": {"register_worktree": False},
            "non_descendant_head": {"is_ancestor": False},
            "absent_session": {"session_id": ""},
            "conflicting_handoff": {"create_handoff": True},
            "unexpected_last_event": {
                "failure_message": "agent exited with code 7",
                "match": "strict-output retry",
                "expect_no_invoke": True,
            },
            "same_head_as_prior_result": {"implementation_head": RESULT},
            "no_tooling_repair": {"repair_head": "c" * 40},
        }
        for name, options in cases.items():
            with self.subTest(name=name):
                controller, adapters = self.make_controller(
                    implementer_responses=[implementer(result_sha=CORRECTED)],
                    reviewer_responses=[audit()],
                )
                expect_no_invoke = options.get("expect_no_invoke", False)
                run_id, paths, implementation = self._prepare_implementer_strict_output_escalation(
                    controller,
                    run_id=f"m3a-test-{name}",
                    session_id=options.get("session_id", "persisted-session"),
                    register_worktree=options.get("register_worktree", True),
                    failure_message=options.get(
                        "failure_message",
                        "agent exited successfully without a valid structured final output",
                    ),
                    create_handoff=options.get("create_handoff", False),
                )
                if options.get("create_worktree", True) is False:
                    # Replace with absent path while keeping parent layout.
                    for child in implementation.iterdir():
                        child.unlink()
                    implementation.rmdir()
                    controller.repository.registered_worktrees = [
                        path
                        for path in controller.repository.registered_worktrees  # type: ignore[attr-defined]
                        if path != implementation
                    ]
                repair_head = options.get("repair_head", REPAIRED)
                implementation_head = options.get("implementation_head", CORRECTED)
                boundary = GitBoundary(
                    branch="buildwithAgent",
                    head=repair_head,
                    clean=True,
                    default_branch_is_ancestor=True,
                    bootstrap_base_is_ancestor=True,
                    excluded_ancestors=(),
                )
                status_value = options.get("status", [])
                is_ancestor_value = options.get("is_ancestor", True)

                def fake_run(*args, cwd=None):
                    if args[:2] == ("diff", "--name-only"):
                        if cwd is not None:
                            return "candidate_memory/models.py"
                        return (
                            "tools/dev_orchestrator/controller.py\n"
                            "docs/DEVELOPMENT_ORCHESTRATION.md"
                        )
                    return ""

                patches = [
                    mock.patch.object(controller, "_verify_repository_boundary", return_value=boundary),
                    mock.patch.object(
                        controller.repository,
                        "head",
                        side_effect=lambda cwd=None: implementation_head if cwd else repair_head,
                    ),
                    mock.patch.object(
                        controller.repository, "status", side_effect=lambda cwd=None: list(status_value)
                    ),
                    mock.patch.object(
                        controller.repository,
                        "is_ancestor",
                        side_effect=lambda ancestor, descendant: bool(
                            is_ancestor_value and ancestor and descendant
                        ),
                    ),
                    mock.patch.object(controller.repository, "run", side_effect=fake_run),
                ]
                with patches[0], patches[1], patches[2], patches[3], patches[4]:
                    resumed = controller.execute(run_id, resume=True)

                self.assertEqual(resumed.state, "OPERATOR_ESCALATION")
                self.assertEqual(adapters["implementer"].requests, [])
                events = [json.loads(line)["event"] for line in paths.events.read_text().splitlines()]
                self.assertNotIn("implementer_strict_output_retry", events)
                if options.get("create_handoff"):
                    saved = json.loads((paths.handoffs / "implementer-01.json").read_text(encoding="utf-8"))
                    self.assertEqual(saved["result_sha"], RESULT)
                else:
                    self.assertFalse((paths.handoffs / "implementer-01.json").exists())
                if not expect_no_invoke:
                    self.assertTrue(resumed.last_error)

    def _prepare_evidence_handoff_escalation(
        self,
        controller,
        *,
        run_id="m3a-evidence",
        session_id="persisted-session",
        register_worktree=True,
        failure_message=(
            "unapproved verification executable/arguments: "
            "['Fresh', 'disposable', 'PostgreSQL', 'migration', 'apply']"
        ),
        correction_cycles=2,
        rejected_result_sha=RESULT,
        create_rejected=True,
    ):
        run_id = self.prepare_run(
            controller,
            state_name=RunStateName.OPERATOR_ESCALATION,
            session_id=session_id,
            run_id=run_id,
        )
        paths = controller.paths(run_id)
        implementation = paths.worktrees / "implementation"
        implementation.mkdir(parents=True)
        if register_worktree:
            controller.repository.registered_worktrees.append(implementation)  # type: ignore[attr-defined]
        rejected = implementer(result_sha=rejected_result_sha)
        rejected["test_commands"] = [
            *rejected["test_commands"],
            {
                "command": "Fresh disposable PostgreSQL migration apply from zero",
                "exit_code": 0,
            },
        ]
        rejected_path = paths.handoffs / f"implementer-{correction_cycles:02d}.json"
        rejected_bytes = b""
        if create_rejected:
            _atomic_json(rejected_path, rejected)
            rejected_bytes = rejected_path.read_bytes()
        state = StateStore(paths.state).load()
        state.result_sha = rejected_result_sha
        state.controller_sha = "c" * 40
        state.correction_cycles = correction_cycles
        state.last_error = failure_message
        if not session_id:
            state.sessions.pop("implementer", None)
        StateStore(paths.state).save(state)
        EventLog(paths.events).emit(
            run_id=run_id,
            role="ORCHA",
            state=RunStateName.OPERATOR_ESCALATION.value,
            event="agent_failure",
            message=failure_message,
        )
        return run_id, paths, implementation, rejected_path, rejected_bytes

    def test_strict_output_prompt_omits_prose_required_tests(self):
        prompt = OrchestrationController._build_implementer_strict_output_prompt(
            base_sha=BASE,
            result_sha=RESULT,
            files_changed=["candidate_memory/models.py"],
            required_tests=[
                ".venv/bin/python manage.py test candidate_memory",
                "make verify",
                "git diff --check",
                "Fresh disposable PostgreSQL migration apply from zero",
                "Source-ingestion fixture test covering provenance",
            ],
        )
        self.assertIn(".venv/bin/python manage.py test candidate_memory", prompt)
        self.assertIn("make verify", prompt)
        self.assertIn("git diff --check", prompt)
        self.assertIn("test_commands must contain only real executable commands", prompt)
        self.assertIn("Narrative expectations remain acceptance criteria only", prompt)
        self.assertIn("Fresh disposable PostgreSQL migration apply from zero", prompt)
        # Narrative appears only in the narrative section, not as a test_commands bullet requirement.
        executable_section = prompt.split("Narrative expectations remain acceptance criteria only")[0]
        self.assertNotIn("Fresh disposable PostgreSQL migration apply from zero", executable_section)

    def test_evidence_handoff_retry_preserves_rejected_and_validates_corrected(self):
        corrected = implementer(result_sha=RESULT)
        review = audit()
        controller, adapters = self.make_controller(
            implementer_responses=[corrected],
            reviewer_responses=[review],
        )
        run_id, paths, implementation, rejected_path, rejected_bytes = (
            self._prepare_evidence_handoff_escalation(controller)
        )
        boundary = GitBoundary(
            branch="buildwithAgent",
            head=REPAIRED,
            clean=True,
            default_branch_is_ancestor=True,
            bootstrap_base_is_ancestor=True,
            excluded_ancestors=(),
        )

        def fake_run(*args, cwd=None):
            if args[:2] == ("diff", "--name-only"):
                if cwd is not None:
                    return "candidate_memory/models.py"
                return (
                    "tools/dev_orchestrator/controller.py\n"
                    "docs/DEVELOPMENT_ORCHESTRATION.md\n"
                    "docs/DEVELOPMENT_RUNBOOK.md"
                )
            return ""

        with (
            mock.patch.object(controller, "_verify_repository_boundary", return_value=boundary),
            mock.patch.object(
                controller.repository,
                "head",
                side_effect=lambda cwd=None: RESULT if cwd else REPAIRED,
            ),
            mock.patch.object(controller.repository, "run", side_effect=fake_run),
        ):
            resumed = controller.execute(run_id, resume=True)

        self.assertEqual(resumed.state, "COMPLETED")
        self.assertEqual(resumed.correction_cycles, 2)
        self.assertEqual(resumed.controller_sha, REPAIRED)
        self.assertEqual(resumed.result_sha, RESULT)
        self.assertEqual(rejected_path.read_bytes(), rejected_bytes)
        corrected_path = paths.root / resumed.active_implementer_handoff_path
        self.assertTrue(corrected_path.exists())
        self.assertNotEqual(corrected_path, rejected_path)
        self.assertEqual(
            json.loads(corrected_path.read_text(encoding="utf-8"))["result_sha"],
            RESULT,
        )
        self.assertEqual(len(adapters["implementer"].requests), 1)
        request = adapters["implementer"].requests[0]
        self.assertEqual(request.session_id, "persisted-session")
        self.assertIn("test_commands must contain only real executable commands", request.prompt)
        self.assertNotIn(
            "['Fresh', 'disposable'",
            request.prompt,
        )
        events = [json.loads(line)["event"] for line in paths.events.read_text().splitlines()]
        self.assertIn("implementer_evidence_handoff_retry", events)
        self.assertIn("implementer_evidence_handoff_corrected", events)

    def test_evidence_handoff_retry_is_idempotent_on_second_resume(self):
        corrected = implementer(result_sha=RESULT)
        review = audit()
        controller, adapters = self.make_controller(
            implementer_responses=[corrected],
            reviewer_responses=[review, audit()],
            closure_responses=[closure(), closure()],
        )
        run_id, paths, implementation, rejected_path, rejected_bytes = (
            self._prepare_evidence_handoff_escalation(controller, run_id="m3a-evidence-idem")
        )
        boundary = GitBoundary(
            branch="buildwithAgent",
            head=REPAIRED,
            clean=True,
            default_branch_is_ancestor=True,
            bootstrap_base_is_ancestor=True,
            excluded_ancestors=(),
        )

        def fake_run(*args, cwd=None):
            if args[:2] == ("diff", "--name-only"):
                if cwd is not None:
                    return "candidate_memory/models.py"
                return "tools/dev_orchestrator/controller.py\ndocs/DEVELOPMENT_ORCHESTRATION.md"
            return ""

        head_patch = mock.patch.object(
            controller.repository,
            "head",
            side_effect=lambda cwd=None: RESULT if cwd else REPAIRED,
        )
        with (
            mock.patch.object(controller, "_verify_repository_boundary", return_value=boundary),
            head_patch,
            mock.patch.object(controller.repository, "run", side_effect=fake_run),
        ):
            first = controller.execute(run_id, resume=True)
        self.assertEqual(first.state, "COMPLETED")
        first_corrected = first.active_implementer_handoff_path
        first_prompt_count = len(list(paths.prompts.glob("implementer-evidence-handoff-*.txt")))
        # Force a second resume attempt from escalation with corrected artifact already present.
        state = StateStore(paths.state).load()
        state.state = RunStateName.OPERATOR_ESCALATION.value
        state.controller_sha = "c" * 40
        state.pending_implementer_prompt_path = ""
        state.last_error = (
            "unapproved verification executable/arguments: ['Fresh', 'disposable']"
        )
        StateStore(paths.state).save(state)
        EventLog(paths.events).emit(
            run_id=run_id,
            role="ORCHA",
            state=RunStateName.OPERATOR_ESCALATION.value,
            event="agent_failure",
            message=state.last_error,
        )
        second_repair = "f" * 40
        second_boundary = GitBoundary(
            branch="buildwithAgent",
            head=second_repair,
            clean=True,
            default_branch_is_ancestor=True,
            bootstrap_base_is_ancestor=True,
            excluded_ancestors=(),
        )
        prior_requests = len(adapters["implementer"].requests)
        with (
            mock.patch.object(controller, "_verify_repository_boundary", return_value=second_boundary),
            mock.patch.object(
                controller.repository,
                "head",
                side_effect=lambda cwd=None: RESULT if cwd else second_repair,
            ),
            mock.patch.object(controller.repository, "run", side_effect=fake_run),
        ):
            second = controller.execute(run_id, resume=True)
        self.assertEqual(second.state, "COMPLETED")
        self.assertEqual(second.correction_cycles, 2)
        self.assertEqual(len(adapters["implementer"].requests), prior_requests)
        self.assertEqual(
            len(list(paths.prompts.glob("implementer-evidence-handoff-*.txt"))),
            first_prompt_count,
        )
        self.assertEqual(
            len(list(paths.handoffs.glob("implementer-02-evidence-corrected-*.json"))),
            1,
        )
        self.assertEqual(rejected_path.read_bytes(), rejected_bytes)
        self.assertEqual(second.active_implementer_handoff_path, first_corrected)
        events = [json.loads(line)["event"] for line in paths.events.read_text().splitlines()]
        self.assertIn("implementer_evidence_handoff_retry_idempotent", events)

    def test_evidence_handoff_reuse_rejects_forged_corrected_artifacts(self):
        cases = {
            "wrong_base_sha": {"base_sha": "1" * 40, "match": "base_sha"},
            "wrong_result_sha": {"result_sha": CORRECTED, "match": "result_sha"},
            "non_implemented": {"status": "BLOCKED", "result_sha": "", "match": "IMPLEMENTED"},
        }
        for name, options in cases.items():
            with self.subTest(name=name):
                controller, adapters = self.make_controller(
                    implementer_responses=[implementer()],
                    reviewer_responses=[audit()],
                )
                run_id, paths, implementation, rejected_path, rejected_bytes = (
                    self._prepare_evidence_handoff_escalation(
                        controller,
                        run_id=f"m3a-evidence-forge-{name}",
                    )
                )
                forged = implementer(result_sha=options.get("result_sha", RESULT))
                if "base_sha" in options:
                    forged["base_sha"] = options["base_sha"]
                if "status" in options:
                    forged["status"] = options["status"]
                    forged["result_sha"] = options.get("result_sha", "")
                    forged["questions"] = []
                    if options["status"] == "BLOCKED":
                        forged["summary"] = "blocked"
                _atomic_json(
                    paths.handoffs / "implementer-02-evidence-corrected-01.json",
                    forged,
                )
                boundary = GitBoundary(
                    branch="buildwithAgent",
                    head=REPAIRED,
                    clean=True,
                    default_branch_is_ancestor=True,
                    bootstrap_base_is_ancestor=True,
                    excluded_ancestors=(),
                )

                def fake_run(*args, cwd=None):
                    if args[:2] == ("diff", "--name-only"):
                        return "tools/dev_orchestrator/controller.py"
                    return ""

                with (
                    mock.patch.object(
                        controller, "_verify_repository_boundary", return_value=boundary
                    ),
                    mock.patch.object(
                        controller.repository,
                        "head",
                        side_effect=lambda cwd=None: RESULT if cwd else REPAIRED,
                    ),
                    mock.patch.object(controller.repository, "run", side_effect=fake_run),
                ):
                    resumed = controller.execute(run_id, resume=True)

                self.assertEqual(resumed.state, "OPERATOR_ESCALATION")
                self.assertIn(options["match"], resumed.last_error)
                self.assertEqual(adapters["implementer"].requests, [])
                self.assertEqual(rejected_path.read_bytes(), rejected_bytes)
                self.assertFalse(resumed.active_implementer_handoff_path)
                events = [
                    json.loads(line)["event"] for line in paths.events.read_text().splitlines()
                ]
                self.assertNotIn("implementer_evidence_handoff_retry_idempotent", events)
                self.assertNotIn("implementer_evidence_handoff_corrected", events)

    def test_evidence_handoff_retry_fail_closed_preconditions(self):
        cases = {
            "dirty_worktree": {"status": [" M candidate_memory/models.py"]},
            "missing_worktree": {"create_worktree": False},
            "unregistered_worktree": {"register_worktree": False},
            "absent_session": {"session_id": ""},
            "head_mismatch": {"implementation_head": CORRECTED},
            "returned_sha_mismatch": {"returned_sha": CORRECTED},
            "unrelated_evidence_error": {
                "failure_message": "worktree dirty: M file",
                "expect_no_invoke": True,
            },
            "no_tooling_repair": {"repair_head": "c" * 40},
        }
        for name, options in cases.items():
            with self.subTest(name=name):
                returned_sha = options.get("returned_sha", RESULT)
                controller, adapters = self.make_controller(
                    implementer_responses=[implementer(result_sha=returned_sha)],
                    reviewer_responses=[audit()],
                )
                run_id, paths, implementation, rejected_path, rejected_bytes = (
                    self._prepare_evidence_handoff_escalation(
                        controller,
                        run_id=f"m3a-evidence-{name}",
                        session_id=options.get("session_id", "persisted-session"),
                        register_worktree=options.get("register_worktree", True),
                        failure_message=options.get(
                            "failure_message",
                            "unapproved verification executable/arguments: ['Fresh']",
                        ),
                    )
                )
                if options.get("create_worktree", True) is False:
                    implementation.rmdir()
                    controller.repository.registered_worktrees = [
                        path
                        for path in controller.repository.registered_worktrees  # type: ignore[attr-defined]
                        if path != implementation
                    ]
                repair_head = options.get("repair_head", REPAIRED)
                implementation_head = options.get("implementation_head", RESULT)
                boundary = GitBoundary(
                    branch="buildwithAgent",
                    head=repair_head,
                    clean=True,
                    default_branch_is_ancestor=True,
                    bootstrap_base_is_ancestor=True,
                    excluded_ancestors=(),
                )
                status_value = options.get("status", [])

                def fake_run(*args, cwd=None):
                    if args[:2] == ("diff", "--name-only"):
                        if cwd is not None:
                            return "candidate_memory/models.py"
                        return "tools/dev_orchestrator/controller.py"
                    return ""

                with (
                    mock.patch.object(
                        controller, "_verify_repository_boundary", return_value=boundary
                    ),
                    mock.patch.object(
                        controller.repository,
                        "head",
                        side_effect=lambda cwd=None: (
                            implementation_head if cwd else repair_head
                        ),
                    ),
                    mock.patch.object(
                        controller.repository,
                        "status",
                        side_effect=lambda cwd=None: list(status_value),
                    ),
                    mock.patch.object(controller.repository, "run", side_effect=fake_run),
                ):
                    resumed = controller.execute(run_id, resume=True)

                self.assertEqual(resumed.state, "OPERATOR_ESCALATION")
                self.assertEqual(rejected_path.read_bytes(), rejected_bytes)
                events = [
                    json.loads(line)["event"] for line in paths.events.read_text().splitlines()
                ]
                self.assertNotIn("implementer_evidence_handoff_corrected", events)
                if options.get("expect_no_invoke"):
                    self.assertEqual(adapters["implementer"].requests, [])
                    self.assertNotIn("implementer_evidence_handoff_retry", events)
                elif name == "returned_sha_mismatch":
                    self.assertEqual(len(adapters["implementer"].requests), 1)
                    self.assertIn("pinned", resumed.last_error)
                else:
                    self.assertEqual(adapters["implementer"].requests, [])
                    self.assertTrue(resumed.last_error)

    def test_plan_creates_supplemental_audits_directory(self):
        import shutil

        controller, _ = self.make_controller(implementer_responses=[], reviewer_responses=[])
        contracts = self.runtime / "contracts"
        contracts.mkdir(parents=True)
        shutil.copy(
            REPOSITORY_ROOT / ".orchestration/contracts/M3A.json",
            contracts / "M3A.json",
        )
        boundary = GitBoundary(
            branch="buildwithAgent",
            head=BASE,
            clean=True,
            default_branch_is_ancestor=True,
            bootstrap_base_is_ancestor=True,
            excluded_ancestors=(),
        )
        with mock.patch.object(controller, "_verify_repository_boundary", return_value=boundary):
            run_id, _contract = controller.plan("M3A", dry_run=True)
        paths = controller.paths(run_id)
        self.assertTrue(paths.handoffs.is_dir())
        self.assertTrue(paths.supplemental_audits.is_dir())
        self.assertEqual(paths.supplemental_audits.name, "supplemental-audits")
        self.assertEqual(paths.supplemental_audits.parent, paths.root)
        self.assertNotEqual(paths.supplemental_audits, paths.handoffs)

    def test_supplemental_audits_are_outside_handoffs_and_ignored_by_pipeline_loading(self):
        controller, _ = self.make_controller(implementer_responses=[], reviewer_responses=[])
        run_id = self.prepare_run(controller)
        paths = controller.paths(run_id)
        pipeline = controller.pipeline_audit_handoff_path(paths, 0)
        _atomic_json(pipeline, audit())
        verification = paths.handoffs / "audit-verification-00.json"
        _atomic_json(
            verification,
            {"commands": [], "failed_reviewer_commands": [], "implementation_evidence_path": ""},
        )
        supplemental = controller.supplemental_audit_path(paths, "claude-extra-audit.json")
        _atomic_json(
            supplemental,
            {
                "schema": "claude-supplemental-v1",
                "verdict": "NOTE",
                "notes": "intentionally not validate_audit_response compatible",
            },
        )
        listed = controller.list_pipeline_audit_handoffs(paths)
        self.assertEqual(listed, [pipeline])
        self.assertNotIn(verification, listed)
        self.assertTrue(supplemental.exists())
        self.assertTrue(supplemental.is_relative_to(paths.supplemental_audits))
        self.assertFalse(supplemental.is_relative_to(paths.handoffs))
        with self.assertRaises(ControllerError):
            controller.supplemental_audit_path(paths, "../escape.json")
        with self.assertRaises(ControllerError):
            controller.supplemental_audit_path(paths, "nested/dir.json")
        # Loading only the pipeline audit name remains schema-valid; verification is not listed.
        validate_audit_response(json.loads(listed[0].read_text(encoding="utf-8")))

    def test_record_supplemental_audit_success_preserves_state_and_emits_event(self):
        controller, _ = self.make_controller(implementer_responses=[], reviewer_responses=[])
        run_id = self.prepare_run(controller, state_name=RunStateName.OPERATOR_ESCALATION)
        paths = controller.paths(run_id)
        before_state = paths.state.read_bytes()
        before_state_obj = StateStore(paths.state).load()
        source = Path(self.temp.name) / "claude-extra-audit.json"
        payload = {
            "schema": "claude-supplemental-v1",
            "verdict": "NOTE",
            "notes": "not validate_audit_response compatible",
            "findings": [{"id": "NOTE-1", "detail": "observation only"}],
        }
        source.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(SchemaError):
            validate_audit_response(payload)

        destination = controller.record_supplemental_audit(run_id, source)

        self.assertEqual(destination, paths.supplemental_audits / "claude-extra-audit.json")
        self.assertTrue(destination.is_relative_to(paths.supplemental_audits))
        self.assertFalse(destination.is_relative_to(paths.handoffs))
        self.assertEqual(json.loads(destination.read_text(encoding="utf-8")), payload)
        self.assertEqual(paths.state.read_bytes(), before_state)
        after_state = StateStore(paths.state).load()
        self.assertEqual(after_state.state, before_state_obj.state)
        self.assertEqual(after_state.correction_cycles, before_state_obj.correction_cycles)
        events = [
            event
            for event in EventLog(paths.events).read()
            if event["event"] == "supplemental_audit_recorded"
        ]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["state"], before_state_obj.state)
        self.assertEqual(
            events[0]["data"]["artifact_path"],
            "supplemental-audits/claude-extra-audit.json",
        )
        self.assertEqual(controller.list_pipeline_audit_handoffs(paths), [])

    def test_record_supplemental_audit_rejection_cases(self):
        controller, _ = self.make_controller(implementer_responses=[], reviewer_responses=[])
        run_id = self.prepare_run(controller)
        paths = controller.paths(run_id)
        source_dir = Path(self.temp.name) / "sources"
        source_dir.mkdir()

        missing_run_source = source_dir / "ok.json"
        missing_run_source.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ControllerError, "not found or missing durable state"):
            controller.record_supplemental_audit("m3a-missing", missing_run_source)

        mismatched = StateStore(paths.state).load()
        mismatched.run_id = "other-run-id"
        StateStore(paths.state).save(mismatched)
        with self.assertRaisesRegex(ControllerError, "does not match run directory"):
            controller.record_supplemental_audit(run_id, missing_run_source)
        fixed = StateStore(paths.state).load()
        fixed.run_id = run_id
        StateStore(paths.state).save(fixed)

        unsafe = source_dir / "not-json.txt"
        unsafe.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ControllerError, "single safe .json filename"):
            controller.record_supplemental_audit(run_id, unsafe)

        with self.assertRaisesRegex(ControllerError, "single safe .json filename"):
            controller.supplemental_audit_path(paths, "../escape.json")
        with self.assertRaisesRegex(ControllerError, "single safe .json filename"):
            controller.supplemental_audit_path(paths, "nested/escape.json")

        array_source = source_dir / "array.json"
        array_source.write_text("[1, 2]", encoding="utf-8")
        with self.assertRaisesRegex(ControllerError, "must be an object"):
            controller.record_supplemental_audit(run_id, array_source)

        outside = Path(self.temp.name) / "outside-target.json"
        outside.write_text("{}", encoding="utf-8")
        escape_link = paths.supplemental_audits / "escape-link.json"
        escape_link.symlink_to(outside)
        escape_source = source_dir / "escape-link.json"
        escape_source.write_text('{"ok": true}', encoding="utf-8")
        with self.assertRaisesRegex(ControllerError, "outside supplemental-audits|already exists"):
            controller.record_supplemental_audit(run_id, escape_source)
        escape_link.unlink()

        good = source_dir / "once.json"
        good.write_text('{"schema": "claude-supplemental-v1", "ok": true}', encoding="utf-8")
        first = controller.record_supplemental_audit(run_id, good)
        self.assertTrue(first.exists())
        with self.assertRaisesRegex(ControllerError, "already exists"):
            controller.record_supplemental_audit(run_id, good)

    def test_startup_identity_uses_persisted_state_not_caller_memory(self):
        controller, _ = self.make_controller(implementer_responses=[], reviewer_responses=[])
        run_id = self.prepare_run(controller, state_name=RunStateName.IMPLEMENTING)
        paths = controller.paths(run_id)
        implementation = paths.worktrees / "implementation"
        implementation.mkdir(parents=True)
        self.seed_worktree_current_state(implementation, content="persisted-state fixture\n")
        durable = StateStore(paths.state).load()
        durable.state = RunStateName.CORRECTING.value
        durable.correction_cycles = 2
        StateStore(paths.state).save(durable)
        request = controller._request(
            "implementer",
            "body-only prompt",
            implementation,
            paths,
            "implementer-response.schema.json",
        )
        self.assertIn("`CORRECTING`", request.prompt)
        self.assertIn(f"`{run_id}`", request.prompt)
        self.assertNotIn("`IMPLEMENTING`", request.prompt)

        mismatched = StateStore(paths.state).load()
        mismatched.run_id = "other-run-id"
        StateStore(paths.state).save(mismatched)
        with self.assertRaisesRegex(ControllerError, "does not match run directory"):
            controller._request("implementer", "body", implementation, paths, None)

        paths.state.write_text("{not-json", encoding="utf-8")
        with self.assertRaisesRegex(ControllerError, "unable to load durable run state"):
            controller._request("implementer", "body", implementation, paths, None)

    def test_startup_identity_rejects_current_state_symlink_escape(self):
        controller, _ = self.make_controller(implementer_responses=[], reviewer_responses=[])
        run_id = self.prepare_run(controller)
        paths = controller.paths(run_id)
        implementation = paths.worktrees / "implementation"
        docs = implementation / "docs"
        docs.mkdir(parents=True)
        outside_dir = Path(self.temp.name) / "outside-checkout"
        outside_dir.mkdir()
        outside = outside_dir / "CURRENT_STATE.md"
        outside.write_text("escaped root-like CURRENT_STATE\n", encoding="utf-8")
        (docs / "CURRENT_STATE.md").symlink_to(outside)
        with self.assertRaisesRegex(ControllerError, "outside the request worktree|path escape"):
            controller._request(
                "implementer",
                "body",
                implementation,
                paths,
                "implementer-response.schema.json",
            )

    def test_startup_identity_for_implementation_and_detached_audit_worktrees(self):
        controller, adapters = self.make_controller(
            implementer_responses=[implementer()],
            reviewer_responses=[audit()],
        )
        run_id = self.prepare_run(controller)
        paths = controller.paths(run_id)
        implementation = paths.worktrees / "implementation"
        implementation.mkdir(parents=True)
        impl_state = self.seed_worktree_current_state(
            implementation, content="implementation CURRENT_STATE fixture\n"
        )
        audit_worktree = paths.worktrees / "audit-00"
        audit_worktree.mkdir(parents=True)
        audit_state = self.seed_worktree_current_state(
            audit_worktree, content="detached audit CURRENT_STATE fixture\n"
        )
        impl_hash = __import__("hashlib").sha256(impl_state.read_bytes()).hexdigest()
        audit_hash = __import__("hashlib").sha256(audit_state.read_bytes()).hexdigest()

        def branch(cwd=None):
            if cwd and Path(cwd).name.startswith("audit-"):
                return ""  # detached
            if cwd and Path(cwd).name == "implementation":
                return "agent/m3a-test/implementation"
            return "buildwithAgent"

        def head(cwd=None):
            if cwd and Path(cwd).name.startswith("audit-"):
                return RESULT
            if cwd and Path(cwd).name == "implementation":
                return RESULT
            return BASE

        with (
            mock.patch.object(controller.repository, "branch", side_effect=branch),
            mock.patch.object(controller.repository, "head", side_effect=head),
        ):
            state = controller.execute(run_id)

        self.assertEqual(state.state, "COMPLETED")
        implementer_prompt = adapters["implementer"].requests[0].prompt
        reviewer_prompt = adapters["reviewer"].requests[0].prompt
        self.assertIn("## Startup identity (verify before acting)", implementer_prompt)
        self.assertIn("`agent/m3a-test/implementation`", implementer_prompt)
        self.assertIn(f"`{RESULT}`", implementer_prompt)
        self.assertIn(f"`{run_id}`", implementer_prompt)
        self.assertIn("`IMPLEMENTING`", implementer_prompt)
        self.assertIn(str(impl_state.resolve()), implementer_prompt)
        self.assertIn(impl_hash, implementer_prompt)
        self.assertIn("Do not substitute the root checkout copy", implementer_prompt)
        self.assertIn("`DETACHED`", reviewer_prompt)
        self.assertIn(str(audit_state.resolve()), reviewer_prompt)
        self.assertIn(audit_hash, reviewer_prompt)
        self.assertIn("`AUDITING`", reviewer_prompt)
        root_current = (REPOSITORY_ROOT / "docs" / "CURRENT_STATE.md").resolve()
        self.assertNotIn(str(root_current), implementer_prompt)
        self.assertNotIn(str(root_current), reviewer_prompt)

    def test_startup_identity_fails_closed_when_current_state_missing(self):
        controller, adapters = self.make_controller(
            implementer_responses=[implementer()],
            reviewer_responses=[],
        )
        run_id = self.prepare_run(controller)

        class BareWorktreeController(TestController):
            def _prepare_worktrees(self, state, paths):
                del state
                worktree = paths.worktrees / "implementation"
                worktree.mkdir(parents=True, exist_ok=True)
                return worktree, None

        bare = BareWorktreeController(
            repository_root=REPOSITORY_ROOT,
            runtime_root=self.runtime,
            config=self.config,
            adapters=adapters,
            evidence_collector=AcceptEvidence(),
        )
        bare.repository = FakeRepository()
        state = bare.execute(run_id)
        self.assertEqual(state.state, "OPERATOR_ESCALATION")
        self.assertIn("CURRENT_STATE.md is missing", state.last_error)
        self.assertEqual(adapters["implementer"].requests, [])

    def test_codex_output_schemas_give_const_and_enum_nodes_explicit_types(self):
        def walk(value):
            if isinstance(value, dict):
                if "const" in value or "enum" in value:
                    self.assertIn("type", value)
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        for schema_path in (REPOSITORY_ROOT / "tools/dev_orchestrator/schemas").glob("*.json"):
            walk(json.loads(schema_path.read_text(encoding="utf-8")))


class ValidationAndSafetyTests(unittest.TestCase):
    def test_invocation_metrics_normalize_codex_events(self):
        metrics = InvocationMetrics()
        metrics.observe({"type": "thread.started", "thread_id": "codex-task"})
        metrics.observe(
            {"type": "item.started", "item": {"id": "tool-1", "type": "command_execution"}}
        )
        metrics.observe(
            {
                "type": "item.completed",
                "item": {
                    "id": "tool-1",
                    "type": "command_execution",
                    "aggregated_output": "åbc",
                },
            }
        )
        metrics.observe(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 80,
                    "output_tokens": 20,
                    "reasoning_output_tokens": 7,
                },
            }
        )

        self.assertEqual(metrics.task_id, "codex-task")
        self.assertEqual(metrics.input_tokens, 100)
        self.assertEqual(metrics.cached_input_tokens, 80)
        self.assertEqual(metrics.output_tokens, 20)
        self.assertEqual(metrics.reasoning_output_tokens, 7)
        self.assertEqual(metrics.tool_call_count, 1)
        self.assertEqual(metrics.tool_output_bytes, len("åbc".encode("utf-8")))

    def test_invocation_metrics_normalize_cursor_events_without_double_counting_tools(self):
        metrics = InvocationMetrics()
        started = {
            "type": "tool_call",
            "subtype": "started",
            "call_id": "cursor-tool",
            "tool_call": {"readToolCall": {"args": {"path": "README.md"}}},
        }
        completed = {
            **started,
            "subtype": "completed",
            "tool_call": {
                "readToolCall": {"args": {"path": "README.md"}},
                "result": {"success": {"content": "hello"}},
            },
        }
        metrics.observe(started)
        metrics.observe(completed)
        metrics.observe(completed)
        metrics.observe(
            {
                "type": "result",
                "request_id": "cursor-task",
                "usage": {"inputTokens": 30, "cacheReadTokens": 25, "outputTokens": 5},
            }
        )

        self.assertEqual(metrics.task_id, "cursor-task")
        self.assertEqual(metrics.input_tokens, 30)
        self.assertEqual(metrics.cached_input_tokens, 25)
        self.assertEqual(metrics.output_tokens, 5)
        self.assertEqual(metrics.reasoning_output_tokens, 0)
        self.assertEqual(metrics.tool_call_count, 1)
        self.assertEqual(
            metrics.tool_output_bytes,
            len('{"success":{"content":"hello"}}'.encode("utf-8")),
        )

    def test_invoke_records_one_passive_metrics_event(self):
        class EventAdapter:
            def start(self, request, event_callback):
                del request
                event_callback("stdout", {"type": "thread.started", "thread_id": "task-1"})
                event_callback(
                    "stdout",
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 12,
                            "cached_input_tokens": 9,
                            "output_tokens": 3,
                            "reasoning_output_tokens": 2,
                        },
                    },
                )
                return AdapterResult(
                    status="COMPLETED",
                    exit_code=0,
                    handoff={"candidate_sha": RESULT},
                )

        request = AdapterRequest(
            role="reviewer",
            prompt="full prompt å",
            workdir=REPOSITORY_ROOT,
            model="test",
            reasoning_effort=None,
            permission_profile="read_only",
            timeout_seconds=10,
            heartbeat_seconds=1,
            log_dir=REPOSITORY_ROOT / ".orchestration" / "test-logs",
            packet_bytes=7,
            retry_reason="reviewer_correction",
        )
        state = mock.Mock(
            run_id="run-1",
            state="AUDITING",
            result_sha="",
            correction_cycles=2,
        )
        with tempfile.TemporaryDirectory() as directory:
            events = EventLog(Path(directory) / "events.jsonl")
            result = OrchestrationController._invoke(
                EventAdapter(),
                request,
                state=state,
                events=events,
            )
            self.assertTrue(result.succeeded)
            records = list(events.read())

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["event"], "agent_invocation_metrics")
        self.assertEqual(records[0]["run_id"], "run-1")
        self.assertEqual(records[0]["role"], "REVIEWER")
        self.assertEqual(
            records[0]["data"],
            {
                "cached_input_tokens": 9,
                "candidate_sha": RESULT,
                "correction_count": 2,
                "input_tokens": 12,
                "output_tokens": 3,
                "packet_bytes": 7,
                "prompt_bytes": len("full prompt å".encode("utf-8")),
                "reasoning_output_tokens": 2,
                "retry_reason": "reviewer_correction",
                "task_id": "task-1",
                "tool_call_count": 0,
                "tool_output_bytes": 0,
            },
        )

    def test_operator_decision_schema_is_strict(self):
        decision = {
            "run_id": "m3a-test",
            "decision": "APPROVED",
            "reason": "milestone boundary advanced",
            "additional_allowed_paths": ["job_applications/tests/test_settings.py"],
            "constraints": ["Preserve unrelated assertions."],
        }
        self.assertEqual(validate_operator_decision(decision), decision)
        with self.assertRaises(SchemaError):
            validate_operator_decision({**decision, "unexpected": True})
        with self.assertRaises(SchemaError):
            validate_operator_decision({**decision, "additional_allowed_paths": ["../outside"]})

    def test_cursor_durable_assistant_handoff_survives_truncated_result(self):
        handoff = implementer("QUESTION")
        assistant = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": f"```json\n{json.dumps(handoff)}\n```"}
                    ]
                },
                "session_id": "durable-session",
            }
        )
        truncated = json.dumps(
            {"type": "result", "subtype": "success", "result": "oversized…[TRUNCATED]"}
        )

        class ScriptedCursor(CursorAdapter):
            def build_command(self, request, final_output_path):
                del request, final_output_path
                script = f"print({assistant!r}); print({truncated!r})"
                return [sys.executable, "-c", script]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = ScriptedCursor("unused").start(
                AdapterRequest(
                    role="implementer",
                    prompt="prompt",
                    workdir=root,
                    model="auto",
                    reasoning_effort=None,
                    permission_profile="implementation_worktree",
                    timeout_seconds=10,
                    heartbeat_seconds=1,
                    log_dir=root / "logs",
                    allow_write=True,
                    safety_verified=True,
                )
            )
            self.assertTrue(result.succeeded)
            self.assertEqual(result.handoff, handoff)
            final_path = root / "logs" / "implementer-001.final.json"
            self.assertEqual(json.loads(final_path.read_text(encoding="utf-8")), handoff)
            for line in (root / "logs" / "implementer-001.stdout.log").read_text().splitlines():
                json.loads(line)

    def test_cursor_malformed_actual_handoff_remains_rejected(self):
        malformed = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "```json\n{bad}\n```"}]},
            }
        )
        truncated = json.dumps({"type": "result", "result": "{…[TRUNCATED]"})

        class ScriptedCursor(CursorAdapter):
            def build_command(self, request, final_output_path):
                del request, final_output_path
                return [sys.executable, "-c", f"print({malformed!r}); print({truncated!r})"]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = ScriptedCursor("unused").start(
                AdapterRequest(
                    role="implementer",
                    prompt="prompt",
                    workdir=root,
                    model="auto",
                    reasoning_effort=None,
                    permission_profile="implementation_worktree",
                    timeout_seconds=10,
                    heartbeat_seconds=1,
                    log_dir=root / "logs",
                )
            )
            self.assertFalse(result.succeeded)
            self.assertIn("valid structured final output", result.error)

    def test_secret_redaction_is_recursive_and_event_log_is_sanitized(self):
        database_url = "postgresql://user:database-secret@db.invalid/app"  # pragma: allowlist secret
        value = redact(
            {
                "authorization": "Bearer sample-secret",
                "nested": "token=abc123",
                "refresh_token": "refresh-secret",  # pragma: allowlist secret
                "DATABASE_URL": database_url,
                "PGPASSWORD": "postgres-password",  # pragma: allowlist secret
            }
        )
        self.assertEqual(value["authorization"], REDACTED)
        self.assertEqual(value["refresh_token"], REDACTED)
        self.assertEqual(value["DATABASE_URL"], REDACTED)
        self.assertEqual(value["PGPASSWORD"], REDACTED)
        self.assertNotIn("abc123", value["nested"])
        with tempfile.TemporaryDirectory() as directory:
            log = EventLog(Path(directory) / "events.jsonl")
            log.emit(
                run_id="run",
                role="ORCHA",
                state="PREPARING",
                event="test",
                message="api_key=sample-secret",
            )
            self.assertNotIn("sample-secret", log.path.read_text(encoding="utf-8"))

    def test_database_credentials_are_redacted_from_process_logs_and_events(self):
        database_url = "postgresql://user:database-secret@db.invalid/app"  # pragma: allowlist secret
        pgpassword = "postgres-password"  # pragma: allowlist secret
        handoff = implementer("QUESTION")
        final_event = json.dumps({"type": "result", "result": json.dumps(handoff)})
        script = (
            "import sys; "
            f"print('DATABASE_URL={database_url} PGPASSWORD={pgpassword}'); "
            f"print('PGPASSWORD={pgpassword}', file=sys.stderr); "
            f"print({final_event!r})"
        )

        class ScriptedAdapter(ProcessAdapter):
            def build_command(self, request, final_output_path):
                del request, final_output_path
                return [sys.executable, "-c", script]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = EventLog(root / "events.jsonl")

            def capture(stream, event):
                events.emit(
                    run_id="run",
                    role="IMPLEMENTER",
                    state="IMPLEMENTING",
                    event="agent_output",
                    message=stream,
                    payload=event,
                )

            result = ScriptedAdapter("unused").start(
                AdapterRequest(
                    role="implementer",
                    prompt="prompt",
                    workdir=root,
                    model="unused",
                    reasoning_effort=None,
                    permission_profile="implementation_worktree",
                    timeout_seconds=10,
                    heartbeat_seconds=1,
                    log_dir=root / "logs",
                ),
                capture,
            )

            self.assertTrue(result.succeeded)
            captured = "\n".join(
                [
                    Path(result.stdout_log).read_text(encoding="utf-8"),
                    Path(result.stderr_log).read_text(encoding="utf-8"),
                    events.path.read_text(encoding="utf-8"),
                ]
            )
            self.assertNotIn(database_url, captured)
            self.assertNotIn(pgpassword, captured)
            self.assertIn(REDACTED, captured)

    def test_disabled_claude_never_resolves_or_launches_binary(self):
        adapter = ClaudeAdapter("/definitely/missing/claude", enabled=False)
        result = adapter.start(None)  # type: ignore[arg-type]
        self.assertEqual(result.status, "DISABLED")

    def test_tmux_fixture_rendering(self):
        line = render_event(
            {
                "role": "reviewer",
                "message": "AUD-001 immutable lineage violation",
                "data": {"severity": "HIGH", "finding_id": "AUD-001"},
            }
        )
        self.assertEqual(
            line,
            "[REVIEWER] AUD-001 immutable lineage violation (finding_id=AUD-001, severity=HIGH)",
        )

    def test_unknown_state_is_rejected(self):
        with self.assertRaises(ValueError):
            RunState.from_dict({"run_id": "x", "phase": "M3A", "state": "SURPRISE"})

    def test_unknown_adapter_and_invalid_limit_are_rejected(self):
        raw = yaml.safe_load((REPOSITORY_ROOT / ".orchestration/config.yaml").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            raw["agents"]["orcha"]["adapter"] = "mystery"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(path)
            raw["agents"]["orcha"]["adapter"] = "codex"
            raw["limits"]["max_correction_cycles"] = 0
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(path)
            raw["limits"]["max_correction_cycles"] = 3
            raw["operator_gates"]["approve_merge"] = False
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(path)

    def test_phase_and_handoff_schema_reject_unknown_fields(self):
        contract = json.loads(
            (REPOSITORY_ROOT / ".orchestration/contracts/M3A.json").read_text(encoding="utf-8")
        )
        validate_phase_contract(contract)
        contract["surprise"] = True
        with self.assertRaises(SchemaError):
            validate_phase_contract(contract)
        malformed = implementer()
        malformed["extra"] = True
        with self.assertRaises(SchemaError):
            validate_implementer_response(malformed)

    def test_audit_duplicate_finding_ids_rejected(self):
        with self.assertRaises(SchemaError):
            validate_audit_response(audit("CORRECTION_REQUIRED", [finding(), finding()]))

    def test_atomic_state_interruption_preserves_prior_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            store = StateStore(path)
            original = RunState(run_id="x", phase="M3A")
            store.save(original)
            (Path(directory) / ".state.json.interrupted").write_text("{", encoding="utf-8")
            restored = store.load()
            self.assertEqual(restored.state, "PREPARING")

    def test_incorrect_git_base_or_ancestry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(
                ["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True
            )
            subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
            (root / "one.txt").write_text("one", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "one.txt"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "one"], check=True)
            repository = GitRepository(root)
            self.assertFalse(repository.is_ancestor("f" * 40))
            self.assertFalse(repository.commit_exists("f" * 40))

    def test_worktree_creation_bootstraps_clean_environment_links(self):
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory)
            root = container / "repository"
            root.mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(
                ["git", "-C", str(root), "config", "user.email", "test@example.invalid"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(root), "config", "user.name", "Test"], check=True
            )
            (root / ".gitignore").write_text(".env\n.venv\n", encoding="utf-8")
            (root / "tracked.txt").write_text("tracked\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)
            (root / ".venv").mkdir()
            (root / ".env").write_text("DATABASE_URL=postgresql://example.invalid/db\n")

            repository = GitRepository(root)
            candidate_sha = repository.head()
            audit = container / "audit"
            implementation = container / "implementation"
            repository.create_audit_worktree(audit, candidate_sha)
            repository.create_implementation_worktree(
                implementation, "agent/test/implementation", candidate_sha
            )

            for worktree in (audit, implementation):
                self.assertTrue((worktree / ".venv").is_symlink())
                self.assertTrue((worktree / ".env").is_symlink())
                self.assertEqual((worktree / ".venv").resolve(), (root / ".venv").resolve())
                self.assertEqual((worktree / ".env").resolve(), (root / ".env").resolve())
                self.assertEqual(repository.status(worktree), [])

    def test_worktree_creation_fails_before_add_when_root_environment_is_missing(self):
        for missing in (".venv", ".env"):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as directory:
                container = Path(directory)
                root = container / "repository"
                root.mkdir()
                subprocess.run(["git", "init", "-q", str(root)], check=True)
                subprocess.run(
                    ["git", "-C", str(root), "config", "user.email", "test@example.invalid"],
                    check=True,
                )
                subprocess.run(
                    ["git", "-C", str(root), "config", "user.name", "Test"], check=True
                )
                (root / ".gitignore").write_text(".env\n.venv\n", encoding="utf-8")
                (root / "tracked.txt").write_text("tracked\n", encoding="utf-8")
                subprocess.run(["git", "-C", str(root), "add", "."], check=True)
                subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)
                if missing != ".venv":
                    (root / ".venv").mkdir()
                if missing != ".env":
                    (root / ".env").write_text("DATABASE_URL=postgresql://example.invalid/db\n")

                worktree = container / "audit"
                with self.assertRaisesRegex(GitSafetyError, "root (virtualenv|environment file)"):
                    GitRepository(root).create_audit_worktree(
                        worktree, GitRepository(root).head()
                    )
                self.assertFalse(worktree.exists())

    def test_redaction_handles_bearer_and_query_secret(self):
        safe = redact_text(
            "Bearer abc123 https://x.invalid?a=1&token=query-secret "
            '{"api_key":"json-secret"} refresh_token=refresh-secret'  # pragma: allowlist secret
        )
        self.assertNotIn("abc123", safe)
        self.assertNotIn("query-secret", safe)
        self.assertNotIn("json-secret", safe)
        self.assertNotIn("refresh-secret", safe)
        self.assertIn(REDACTED, safe)

    def test_agent_subprocess_environment_excludes_credentials(self):
        database_url = "postgresql://user:password@db.invalid/app"  # pragma: allowlist secret
        with mock.patch.dict(
            "os.environ",
            {
                "PATH": "/bin",
                "HOME": "/tmp/home",
                "OPENAI_API_KEY": "secret",  # pragma: allowlist secret
                "PGHOST": "db.invalid",
                "PGPORT": "5432",
                "PGUSER": "cvbuilder",
                "PGPASSWORD": "postgres-password",  # pragma: allowlist secret
                "PGDATABASE": "cvbuilder",
                "DATABASE_URL": database_url,
            },
            clear=True,
        ):
            environment = ProcessAdapter.safe_environment()
        self.assertEqual(
            environment,
            {
                "PATH": "/bin",
                "HOME": "/tmp/home",
                "PGHOST": "db.invalid",
                "PGPORT": "5432",
                "PGUSER": "cvbuilder",
                "PGPASSWORD": "postgres-password",  # pragma: allowlist secret
                "PGDATABASE": "cvbuilder",
                "DATABASE_URL": database_url,
            },
        )

    def test_nested_reasoning_event_is_classified_for_omission(self):
        self.assertTrue(
            ProcessAdapter.contains_hidden_reasoning(
                {"type": "item.completed", "item": {"type": "reasoning", "text": "private"}}
            )
        )


class EvidenceCollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(
            ["git", "-C", str(self.root), "config", "user.email", "test@example.invalid"],
            check=True,
        )
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "Test"], check=True)
        (self.root / "candidate_memory").mkdir()
        (self.root / "candidate_memory/models.py").write_text("BASE = True\n", encoding="utf-8")
        (self.root / "candidate_memory/tests").mkdir()
        (self.root / "candidate_memory/tests/test_models.py").write_text(
            "def test_one():\n    assert True\n    assert 1 == 1\n", encoding="utf-8"
        )
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "base"], check=True)
        self.repository = GitRepository(self.root)
        self.base = self.repository.head()
        self.collector = EvidenceCollector(self.repository)

    def commit(self, relative: str, content: str) -> str:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", relative], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "candidate"], check=True)
        return self.repository.head()

    def collect(self, result: str, **overrides):
        values = {
            "worktree": self.root,
            "base_sha": self.base,
            "result_sha": result,
            "allowed_paths": ["candidate_memory/**"],
            "prohibited_paths": ["requirements.md"],
            "test_commands": [{"command": "git status --short", "exit_code": 0}],
        }
        values.update(overrides)
        return self.collector.collect_and_validate(**values)

    def test_collects_clean_accepted_commit_evidence(self):
        result = self.commit("candidate_memory/models.py", "BASE = True\nM3A = True\n")
        report = self.collect(result)
        self.assertTrue(report.accepted)
        self.assertEqual(report.changed_files, ("candidate_memory/models.py",))

    def test_rejects_real_dirty_worktree(self):
        result = self.commit("candidate_memory/models.py", "M3A = True\n")
        (self.root / "candidate_memory/models.py").write_text("DIRTY = True\n", encoding="utf-8")
        with self.assertRaisesRegex(EvidenceError, "worktree dirty"):
            self.collect(result)

    def test_rejects_real_invalid_result_sha(self):
        with self.assertRaisesRegex(EvidenceError, "does not exist"):
            self.collect("f" * 40)

    def test_rejects_real_unauthorized_path(self):
        result = self.commit("unexpected.py", "BAD = True\n")
        with self.assertRaisesRegex(EvidenceError, "unauthorized"):
            self.collect(result)

    def test_rejects_real_requirements_change(self):
        result = self.commit("requirements.md", "changed\n")
        with self.assertRaisesRegex(EvidenceError, "requirements/decisions"):
            self.collect(
                result,
                allowed_paths=["requirements.md"],
                prohibited_paths=[],
            )

    def test_rejects_failed_verification_evidence(self):
        result = self.commit("candidate_memory/models.py", "M3A = True\n")
        collector = EvidenceCollector(self.repository, command_runner=lambda argv, cwd: 2)
        with self.assertRaisesRegex(EvidenceError, "verification commands failed"):
            collector.collect_and_validate(
                worktree=self.root,
                base_sha=self.base,
                result_sha=result,
                allowed_paths=["candidate_memory/**"],
                prohibited_paths=[],
                test_commands=[{"command": "make verify", "exit_code": 2}],
            )

    def test_rejects_unsafe_verification_command(self):
        result = self.commit("candidate_memory/models.py", "M3A = True\n")
        with self.assertRaisesRegex(EvidenceError, "unapproved"):
            self.collect(
                result,
                test_commands=[{"command": f"{sys.executable} -c pass", "exit_code": 0}],
            )

    def test_accepts_and_executes_valid_git_diff_check_forms(self):
        self.commit("candidate_memory/models.py", "M3A = True\n")
        executed: list[list[str]] = []

        def runner(argv: list[str], cwd: Path) -> int:
            executed.append(list(argv))
            return EvidenceCollector._run_command(argv, cwd)

        collector = EvidenceCollector(self.repository, command_runner=runner)
        cases = (
            ("git diff --check", ("git", "diff", "--check")),
            ("git diff --check HEAD", ("git", "diff", "--check", "HEAD", "--")),
            (
                f"git diff --check {self.base} HEAD",
                ("git", "diff", "--check", self.base, "HEAD", "--"),
            ),
            (
                f"git diff --check {self.base}..HEAD",
                ("git", "diff", "--check", f"{self.base}..HEAD", "--"),
            ),
            (
                f"git diff --check {self.base}...HEAD",
                ("git", "diff", "--check", f"{self.base}...HEAD", "--"),
            ),
        )
        for command, expected_argv in cases:
            with self.subTest(command=command):
                executed.clear()
                observed = collector.validate_test_commands(
                    self.root,
                    [{"command": command, "exit_code": 0}],
                )
                self.assertEqual(observed, ((command, 0),))
                self.assertEqual(executed, [list(expected_argv)])

    def test_rejects_pathspec_smuggling_as_diff_check_endpoint(self):
        self.commit("candidate_memory/models.py", "M3A = True\n")
        path = "candidate_memory/models.py"
        # Git treats a filesystem path as a pathspec and can exit 0 (false-clean).
        probe = subprocess.run(
            ["git", "diff", "--check", "HEAD", path],
            cwd=self.root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        self.assertEqual(probe.returncode, 0)
        with self.assertRaisesRegex(EvidenceError, "does not resolve to a commit"):
            self.collector.validate_test_commands(
                self.root,
                [{"command": f"git diff --check HEAD {path}", "exit_code": 0}],
            )

    def test_rejects_nonexistent_git_diff_check_revision(self):
        missing = "a" * 40
        with self.assertRaisesRegex(EvidenceError, "does not resolve to a commit"):
            self.collector.validate_test_commands(
                self.root,
                [{"command": f"git diff --check {missing} HEAD", "exit_code": 0}],
            )

    def test_observes_nonzero_git_diff_check_exit(self):
        self.commit("candidate_memory/models.py", "TRAILING = 1   \n")
        command = f"git diff --check {self.base} HEAD"
        with self.assertRaisesRegex(EvidenceError, "observed"):
            self.collector.validate_test_commands(
                self.root,
                [{"command": command, "exit_code": 0}],
            )

    def test_rejects_malformed_and_unsafe_git_diff_check_forms(self):
        for command in (
            "git diff --check --cached",
            "git diff --check -n",
            "git diff --check HEAD -- candidate_memory/models.py",
            f"git diff --check {self.base} HEAD extra",
            "git diff --check ..HEAD",
            "git diff --check HEAD..",
            "git diff --check HEAD..HEAD..HEAD",
            "git diff --check HEAD....HEAD",
            f"git diff --check {self.base}..HEAD HEAD",
            f"git diff --check HEAD {self.base}..HEAD",
            f"git diff --check {self.base}..HEAD {self.base}..HEAD",
            "git diff --check 'HEAD;rm'",
            "git checkout HEAD",
            "git commit -am x",
        ):
            with self.subTest(command=command):
                with self.assertRaisesRegex(EvidenceError, "unapproved"):
                    self.collector.validate_test_commands(
                        self.root,
                        [{"command": command, "exit_code": 0}],
                    )

    def test_rejects_suspicious_test_weakening(self):
        result = self.commit(
            "candidate_memory/tests/test_models.py",
            "def test_one():\n    assert True\n",
        )
        with self.assertRaisesRegex(EvidenceError, "test files deleted or weakened"):
            self.collect(
                result,
                allowed_paths=["candidate_memory/**"],
                prohibited_paths=[],
            )

    def test_explicit_operator_authorization_allows_bounded_test_change_for_audit(self):
        result = self.commit(
            "candidate_memory/tests/test_models.py",
            "def test_one():\n    assert True\n",
        )
        report = self.collect(
            result,
            allowed_paths=["candidate_memory/**"],
            prohibited_paths=[],
            authorized_test_changes=["candidate_memory/tests/test_models.py"],
        )
        self.assertEqual(report.suspicious_test_changes, ())
