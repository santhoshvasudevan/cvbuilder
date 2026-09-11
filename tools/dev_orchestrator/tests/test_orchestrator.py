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

from tools.dev_orchestrator.adapters import AdapterResult, ClaudeAdapter, FakeAdapter
from tools.dev_orchestrator.adapters.base import ProcessAdapter
from tools.dev_orchestrator.config import ConfigError, load_config
from tools.dev_orchestrator.controller import OrchestrationController, _atomic_json
from tools.dev_orchestrator.events import EventLog
from tools.dev_orchestrator.evidence import EvidenceCollector, EvidenceError
from tools.dev_orchestrator.git_safety import GitBoundary, GitRepository
from tools.dev_orchestrator.redaction import REDACTED, redact, redact_text
from tools.dev_orchestrator.schemas import (
    SchemaError,
    validate_audit_response,
    validate_implementer_response,
    validate_phase_contract,
)
from tools.dev_orchestrator.state import RunState, RunStateName, StateStore
from tools.dev_orchestrator.tmux_ui import render_event

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
BASE = "a" * 40
RESULT = "b" * 40
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

    def prepare_run(self, controller, state_name=RunStateName.IMPLEMENTING, session_id=""):
        run_id = "m3a-test"
        paths = controller.paths(run_id)
        paths.root.mkdir(parents=True)
        paths.handoffs.mkdir()
        paths.logs.mkdir()
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

    def test_implementation_audit_pass_closure(self):
        controller, _ = self.make_controller(
            implementer_responses=[implementer()], reviewer_responses=[audit()]
        )
        state = controller.execute(self.prepare_run(controller))
        self.assertEqual(state.state, "COMPLETED")

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

        resumed = controller.execute(run_id, resume=True)

        self.assertEqual(resumed.state, "COMPLETED")
        self.assertIn('"verdict": "PASS"', adapters["orcha_closure"].requests[0].prompt)


class ValidationAndSafetyTests(unittest.TestCase):
    def test_secret_redaction_is_recursive_and_event_log_is_sanitized(self):
        value = redact(
            {
                "authorization": "Bearer sample-secret",
                "nested": "token=abc123",
                "refresh_token": "refresh-secret",  # pragma: allowlist secret
            }
        )
        self.assertEqual(value["authorization"], REDACTED)
        self.assertEqual(value["refresh_token"], REDACTED)
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
        with mock.patch.dict(
            "os.environ",
            {"PATH": "/bin", "HOME": "/tmp/home", "OPENAI_API_KEY": "secret"},  # pragma: allowlist secret
            clear=True,
        ):
            environment = ProcessAdapter.safe_environment()
        self.assertEqual(environment, {"PATH": "/bin", "HOME": "/tmp/home"})

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
