"""Explicit deterministic controller for supervised development-agent runs."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .adapters import (
    AdapterRequest,
    AgentAdapter,
    ClaudeAdapter,
    CodexAdapter,
    CursorAdapter,
)
from .config import OrchestratorConfig
from .events import EventLog
from .evidence import EvidenceCollector, EvidenceError
from .git_safety import GitRepository, GitSafetyError
from .schemas import (
    SchemaError,
    validate_audit_response,
    validate_closure_response,
    validate_implementer_response,
    validate_operator_decision,
    validate_phase_contract,
)
from .state import TERMINAL_STATES, RunState, RunStateName, StateStore, utc_now


class ControllerError(RuntimeError):
    pass


@dataclasses.dataclass(frozen=True)
class RunPaths:
    root: Path
    manifest: Path
    state: Path
    contract_json: Path
    contract_markdown: Path
    context_manifest: Path
    events: Path
    handoffs: Path
    prompts: Path
    logs: Path
    evidence: Path
    worktrees: Path

    @classmethod
    def for_run(cls, runtime_root: Path, run_id: str) -> "RunPaths":
        root = runtime_root / "runs" / run_id
        return cls(
            root=root,
            manifest=root / "manifest.json",
            state=root / "state.json",
            contract_json=root / "phase-contract.json",
            contract_markdown=root / "phase-contract.md",
            context_manifest=root / "context-manifest.json",
            events=root / "events.jsonl",
            handoffs=root / "handoffs",
            prompts=root / "prompts",
            logs=root / "logs",
            evidence=root / "evidence.json",
            worktrees=runtime_root / "worktrees" / run_id,
        )


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=True) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _contract_markdown(contract: dict) -> str:
    lines = [
        f"# {contract['milestone']} Phase Contract",
        "",
        f"Run ID: `{contract['run_id']}`  ",
        f"Product baseline: `{contract['product_baseline_sha']}`  ",
        "Orchestration tooling SHA: "
        f"`{contract['orchestration_tooling_sha'] or 'pending bootstrap commit'}`  ",
        f"Future implementation base: `{contract['future_implementation_base_sha']}`",
        "",
        "## Objective",
        "",
        contract["objective"],
    ]
    for title, key in (
        ("Requirements", "requirement_ids"),
        ("Governing decisions", "governing_decision_ids"),
        ("Dependencies", "dependencies"),
        ("In scope", "in_scope"),
        ("Out of scope", "out_of_scope"),
        ("Allowed paths", "allowed_paths"),
        ("Prohibited paths", "prohibited_paths"),
        ("Acceptance criteria", "acceptance_criteria"),
        ("Required tests", "required_tests"),
        ("Documentation updates", "required_documentation_updates"),
        ("Operator gates", "operator_gates"),
    ):
        lines.extend(["", f"## {title}", ""])
        lines.extend(f"- {item}" for item in contract[key])
    return "\n".join(lines) + "\n"


class OrchestrationController:
    def __init__(
        self,
        *,
        repository_root: Path,
        runtime_root: Path,
        config: OrchestratorConfig,
        adapters: dict[str, AgentAdapter] | None = None,
        evidence_collector: Any | None = None,
    ):
        self.repository_root = repository_root.resolve()
        self.runtime_root = runtime_root.resolve()
        self.config = config
        self.repository = GitRepository(self.repository_root)
        self._adapters = adapters
        self.evidence_collector = evidence_collector or EvidenceCollector(self.repository)

    def _adapter(self, role: str) -> AgentAdapter:
        if self._adapters is not None:
            try:
                return self._adapters[role]
            except KeyError as exc:
                raise ControllerError(f"no adapter supplied for role {role}") from exc
        agent = self.config.agents[role]
        if agent.adapter == "codex":
            return CodexAdapter(agent.binary)
        if agent.adapter == "cursor":
            return CursorAdapter(agent.binary)
        if agent.adapter == "claude":
            return ClaudeAdapter(agent.binary, enabled=agent.enabled)
        raise ControllerError("fake adapters must be injected explicitly")

    def paths(self, run_id: str) -> RunPaths:
        return RunPaths.for_run(self.runtime_root, run_id)

    def _verify_repository_boundary(self, *, require_clean: bool):
        return self.repository.verify_bootstrap_boundary(
            default_branch=self.config.repository.default_branch,
            bootstrap_branch=self.config.repository.bootstrap_branch,
            bootstrap_base_sha=self.config.repository.bootstrap_base_sha,
            require_clean=require_clean,
        )

    @staticmethod
    def _effective_contract(paths: RunPaths, contract: dict) -> dict:
        """Rebuild approved clarification/correction context after an interrupted controller."""
        result = dict(contract)
        result["allowed_paths"] = list(contract["allowed_paths"])
        additions = []
        for path in sorted(paths.handoffs.glob("operator-decision-*.json")):
            payload = validate_operator_decision(json.loads(path.read_text(encoding="utf-8")))
            for allowed_path in payload["additional_allowed_paths"]:
                if allowed_path not in result["allowed_paths"]:
                    result["allowed_paths"].append(allowed_path)
            additions.append(
                "Approved operator amendment: "
                + payload["reason"]
                + " Constraints: "
                + " ".join(payload["constraints"])
            )
        for path in sorted(paths.handoffs.glob("orcha-question-*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("decision") == "ANSWER":
                additions.append(f"Approved clarification: {payload['answer']}")
        for path in sorted(paths.handoffs.glob("correction-*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("decision") == "CORRECT":
                additions.append(f"Correction: {payload['prompt']}")
        if additions:
            result["objective"] += "\n\n" + "\n\n".join(additions)
        return result

    @staticmethod
    def _next_artifact_path(directory: Path, stem: str, suffix: str = ".json") -> Path:
        index = 1
        while (directory / f"{stem}-{index:02d}{suffix}").exists():
            index += 1
        return directory / f"{stem}-{index:02d}{suffix}"

    @staticmethod
    def _relative_artifact(paths: RunPaths, path: Path) -> str:
        return str(path.relative_to(paths.root))

    @staticmethod
    def _append_only_json(path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(value, indent=2, sort_keys=True) + "\n"
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.write(descriptor, payload.encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _recover_implementer_handoff(paths: RunPaths) -> tuple[dict, str]:
        for log_path in sorted(paths.logs.glob("implementer-*.stdout.log"), reverse=True):
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            adapter = CursorAdapter("cursor")
            for line_number in range(len(lines), 0, -1):
                try:
                    event = json.loads(lines[line_number - 1])
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                message = adapter.final_message_from_event(event)
                handoff = adapter._parse_handoff(message)
                if handoff is None:
                    continue
                try:
                    return validate_implementer_response(handoff), f"{log_path.name}:{line_number}"
                except SchemaError:
                    continue
        raise ControllerError("no complete schema-valid implementer handoff is recoverable from logs")

    def _verify_repair_checkout(self, state: RunState, boundary) -> None:
        if boundary.head == state.base_sha:
            return
        if not self.repository.is_ancestor(state.base_sha, boundary.head):
            raise ControllerError("orchestration repair HEAD is not descended from the approved run base")
        changed = {
            line
            for line in self.repository.run(
                "diff", "--name-only", f"{state.base_sha}..{boundary.head}"
            ).splitlines()
            if line
        }
        permitted = {
            path
            for path in changed
            if path.startswith("tools/dev_orchestrator/")
            or path in {
                "docs/DEVELOPMENT_ORCHESTRATION.md",
                "docs/DEVELOPMENT_RUNBOOK.md",
            }
        }
        if changed != permitted:
            raise ControllerError(
                "post-escalation checkout contains changes beyond the bounded orchestration repair: "
                f"{sorted(changed - permitted)}"
            )

    def _verify_run_controller_head(self, state: RunState, boundary) -> None:
        expected = state.controller_sha or state.base_sha
        if boundary.head != expected:
            raise ControllerError(
                f"repository HEAD {boundary.head} differs from recorded controller HEAD {expected}"
            )

    def _retry_reviewer_after_tooling_repair(
        self,
        state: RunState,
        paths: RunPaths,
        store: StateStore,
        events: EventLog,
    ) -> bool:
        """Resume a failed reviewer invocation without repeating implementation validation."""
        if not state.result_sha or state.pending_operator_decision_path:
            return False
        records = list(events.read())
        if not records:
            return False
        last = records[-1]
        if last.get("event") != "agent_failure" or last.get("role") != "REVIEWER":
            return False
        if (paths.handoffs / f"audit-{state.correction_cycles:02d}.json").exists():
            return False
        implementation = paths.worktrees / "implementation"
        audit = paths.worktrees / f"audit-{state.correction_cycles:02d}"
        registered = {Path(item["worktree"]).resolve() for item in self.repository.worktrees()}
        if (
            not implementation.exists()
            or not audit.exists()
            or implementation.resolve() not in registered
            or audit.resolve() not in registered
            or self.repository.head(implementation) != state.result_sha
            or self.repository.head(audit) != state.result_sha
            or self.repository.status(implementation)
            or self.repository.status(audit)
            or not paths.evidence.exists()
        ):
            raise ControllerError("reviewer retry prerequisites do not preserve the validated candidate")
        boundary = self._verify_repository_boundary(require_clean=True)
        if boundary.head == state.controller_sha:
            raise ControllerError("reviewer retry requires a committed orchestration tooling repair")
        self._verify_repair_checkout(state, boundary)
        state.controller_sha = boundary.head
        state.last_error = ""
        state.transition(RunStateName.AUDITING)
        store.save(state)
        events.emit(
            run_id=state.run_id,
            role="REVIEWER",
            state=state.state,
            event="reviewer_recovery_retry",
            message="Retrying independent audit after a bounded orchestration tooling repair",
            result_sha=state.result_sha,
        )
        return True

    @staticmethod
    def _is_implementer_structured_output_failure(message: str) -> bool:
        text = (message or "").lower()
        return "structured final output" in text or "implementer_response" in text

    @staticmethod
    def _build_implementer_strict_output_prompt(
        *,
        base_sha: str,
        result_sha: str,
        files_changed: list[str],
        required_tests: list[str],
    ) -> str:
        file_lines = "\n".join(f"- {path}" for path in files_changed) or "- (none)"
        test_lines = "\n".join(f"- {command}" for command in required_tests) or "- (none)"
        return (
            "Do not modify any files and do not create commits. The implementation worktree is already "
            f"clean at commit `{result_sha}`.\n"
            "Return only one JSON object that matches the implementer response schema. Do not include "
            "prose, markdown fences, or any text before or after the JSON.\n"
            f"Set status to IMPLEMENTED, base_sha to `{base_sha}`, and result_sha to `{result_sha}`.\n"
            "files_changed must be exactly this full base-to-result path list:\n"
            f"{file_lines}\n"
            "test_commands must include these required tests with accurate exit codes:\n"
            f"{test_lines}\n"
        )

    def _retry_implementer_strict_output_after_tooling_repair(
        self,
        state: RunState,
        paths: RunPaths,
        store: StateStore,
        events: EventLog,
    ) -> bool:
        """Resume Cursor only to obtain a schema-only handoff for an already-committed HEAD."""
        if (
            not state.result_sha
            or state.pending_operator_decision_path
            or state.pending_implementer_prompt_path
        ):
            return False
        records = list(events.read())
        if not records:
            return False
        last = records[-1]
        failure_message = str(last.get("message") or state.last_error or "")
        if (
            last.get("event") != "agent_failure"
            or last.get("role") != "IMPLEMENTER"
            or not self._is_implementer_structured_output_failure(failure_message)
        ):
            return False
        handoff_path = paths.handoffs / f"implementer-{state.correction_cycles:02d}.json"
        if handoff_path.exists():
            raise ControllerError(
                "implementer strict-output retry conflicts with an existing handoff for this cycle"
            )
        session_id = state.sessions.get("implementer", "")
        if not session_id:
            raise ControllerError("implementer strict-output retry requires the saved Cursor session")
        implementation = paths.worktrees / "implementation"
        registered = {Path(item["worktree"]).resolve() for item in self.repository.worktrees()}
        if not implementation.exists() or implementation.resolve() not in registered:
            raise ControllerError(
                "implementer strict-output retry requires a registered implementation worktree"
            )
        if self.repository.status(implementation):
            raise ControllerError("implementer strict-output retry requires a clean implementation worktree")
        head = self.repository.head(implementation)
        if head == state.result_sha:
            raise ControllerError(
                "implementer strict-output retry requires implementation HEAD to differ from the prior result"
            )
        if not self.repository.is_ancestor(state.base_sha, head):
            raise ControllerError(
                "implementer strict-output retry requires implementation HEAD to "
                "descend from the approved base"
            )
        boundary = self._verify_repository_boundary(require_clean=True)
        if boundary.head == state.controller_sha:
            raise ControllerError(
                "implementer strict-output retry requires a committed orchestration tooling repair"
            )
        self._verify_repair_checkout(state, boundary)
        files_changed = [
            line
            for line in self.repository.run(
                "diff", "--name-only", f"{state.base_sha}..{head}", cwd=implementation
            ).splitlines()
            if line
        ]
        contract = validate_phase_contract(json.loads(paths.contract_json.read_text(encoding="utf-8")))
        prompt = self._build_implementer_strict_output_prompt(
            base_sha=state.base_sha,
            result_sha=head,
            files_changed=files_changed,
            required_tests=list(contract["required_tests"]),
        )
        paths.prompts.mkdir(parents=True, exist_ok=True)
        prompt_path = self._next_artifact_path(paths.prompts, "implementer-strict-output", ".txt")
        _atomic_text(prompt_path, prompt)
        prompt_hash = _sha256_bytes(prompt.encode("utf-8"))
        target = (
            RunStateName.CORRECTING if state.correction_cycles > 0 else RunStateName.IMPLEMENTING
        )
        state.controller_sha = boundary.head
        state.last_error = ""
        state.latest_orcha_prompt_path = self._relative_artifact(paths, prompt_path)
        state.latest_orcha_prompt_sha256 = prompt_hash
        state.pending_implementer_prompt_path = state.latest_orcha_prompt_path
        state.transition(target)
        store.save(state)
        events.emit(
            run_id=state.run_id,
            role="IMPLEMENTER",
            state=state.state,
            event="implementer_strict_output_retry",
            message=(
                "Retrying saved Cursor session for a schema-only implementer handoff after a "
                "bounded orchestration tooling repair"
            ),
            result_sha=head,
            prior_result_sha=state.result_sha,
            prompt_path=state.pending_implementer_prompt_path,
            prompt_sha256=prompt_hash,
            session_id=session_id,
        )
        return True

    def record_operator_decision(self, run_id: str, value: dict) -> tuple[RunState, Path]:
        paths = self.paths(run_id)
        store = StateStore(paths.state)
        state = store.load()
        if state.state != RunStateName.OPERATOR_ESCALATION.value:
            raise ControllerError("operator decisions may only amend an OPERATOR_ESCALATION run")
        decision = validate_operator_decision(value)
        if decision["run_id"] != run_id:
            raise ControllerError("operator decision run_id does not match the target run")
        contract_bytes = paths.contract_json.read_bytes()
        contract = validate_phase_contract(json.loads(contract_bytes))
        prohibited = decision["additional_allowed_paths"]
        if any(EvidenceCollector._matches(path, contract["prohibited_paths"]) for path in prohibited):
            raise ControllerError("operator decision cannot allow a path prohibited by the phase contract")
        try:
            boundary = self._verify_repository_boundary(require_clean=True)
            self._verify_repair_checkout(state, boundary)
        except (GitSafetyError, ControllerError) as exc:
            raise ControllerError(str(exc)) from exc
        implementation = paths.worktrees / "implementation"
        registered = {Path(item["worktree"]).resolve() for item in self.repository.worktrees()}
        if not implementation.exists() or implementation.resolve() not in registered:
            raise ControllerError("existing implementation worktree is missing or unregistered")
        if self.repository.head(implementation) != state.base_sha or state.result_sha:
            raise ControllerError("implementation worktree no longer matches the recoverable escalated state")

        handoff, source = self._recover_implementer_handoff(paths)
        recovered_path = paths.handoffs / f"implementer-{state.correction_cycles:02d}.json"
        if recovered_path.exists():
            existing = validate_implementer_response(json.loads(recovered_path.read_text(encoding="utf-8")))
            if existing != handoff:
                raise ControllerError("existing recovered handoff conflicts with the durable raw log")
        else:
            self._append_only_json(recovered_path, handoff)

        decision_path = self._next_artifact_path(paths.handoffs, "operator-decision")
        self._append_only_json(decision_path, decision)
        contract_hash = _sha256_bytes(contract_bytes)
        state.controller_sha = boundary.head
        state.original_contract_sha256 = state.original_contract_sha256 or contract_hash
        if state.original_contract_sha256 != contract_hash:
            raise ControllerError("original phase contract changed before operator decision recording")
        state.operator_decision_count += 1
        state.pending_operator_decision_path = self._relative_artifact(paths, decision_path)
        state.recovered_handoff_path = self._relative_artifact(paths, recovered_path)
        state.transition(RunStateName.ORCHA_DECISION)
        store.save(state)
        events = EventLog(paths.events)
        events.emit(
            run_id=run_id,
            role="ORCHA",
            state=state.state,
            event="implementer_handoff_recovered",
            message="Recovered complete schema-valid implementer handoff from durable Cursor output",
            source=source,
            handoff_path=state.recovered_handoff_path,
        )
        events.emit(
            run_id=run_id,
            role="ORCHA",
            state=state.state,
            event="operator_decision_recorded",
            message="Append-only operator scope amendment recorded",
            decision_path=state.pending_operator_decision_path,
            contract_sha256=contract_hash,
        )
        return state, decision_path

    def plan(self, phase: str, *, dry_run: bool = False) -> tuple[str, dict]:
        if phase != "M3A":
            raise ControllerError(f"unknown phase: {phase}")
        try:
            boundary = self._verify_repository_boundary(
                require_clean=self.config.repository.require_clean_base and not dry_run
            )
        except GitSafetyError as exc:
            raise ControllerError(str(exc)) from exc
        run_id = f"{phase.lower()}-{uuid.uuid4().hex[:12]}"
        paths = self.paths(run_id)
        template_path = self.runtime_root / "contracts" / f"{phase}.json"
        try:
            contract = json.loads(template_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ControllerError(f"unable to load phase template {template_path}: {exc}") from exc
        head = boundary.head
        contract.update(
            {
                "run_id": run_id,
                "base_sha": head,
                "product_baseline_sha": self.config.repository.bootstrap_base_sha,
                "orchestration_tooling_sha": (
                    head if head != self.config.repository.bootstrap_base_sha else ""
                ),
                "future_implementation_base_sha": head,
            }
        )
        validate_phase_contract(contract)
        state = RunState(run_id=run_id, phase=phase, base_sha=head)
        paths.root.mkdir(parents=True, exist_ok=False)
        paths.handoffs.mkdir()
        paths.prompts.mkdir()
        paths.logs.mkdir()
        _atomic_json(paths.contract_json, contract)
        paths.contract_markdown.write_text(_contract_markdown(contract), encoding="utf-8")
        context = {
            "schema_version": 1,
            "authoritative_documents": [
                "requirements.md",
                "AGENTS.md",
                "CLAUDE.md",
                "docs/ENGINEERING_RULES.md",
                "docs/DECISIONS.md",
                "docs/ARCHITECTURE.md",
                "docs/IMPLEMENTATION_PLAN.md",
                "docs/TEST_STRATEGY.md",
                "docs/REQUIREMENT_TRACEABILITY.md",
                "docs/CURRENT_STATE.md",
                "docs/HANDOVER_PROTOCOL.md",
                "docs/MILESTONE_COMPLETION_CHECKLIST.md",
                "docs/QUALITY_BENCHMARK.md",
                "docs/RESUME_OUTPUT_STRUCTURE.md",
                "docs/V2_REUSE_AUDIT.md",
                "docs/CANDIDATE_MEMORY_SNAPSHOT.md",
            ],
            "excluded_sources": ["m3a-candidate-memory-foundation", "02bbc5c"],
            "dry_run": dry_run,
        }
        _atomic_json(paths.context_manifest, context)
        _atomic_json(
            paths.manifest,
            {
                "schema_version": 1,
                "run_id": run_id,
                "phase": phase,
                "product_baseline_sha": self.config.repository.bootstrap_base_sha,
                "orchestration_tooling_sha": contract["orchestration_tooling_sha"],
                "future_implementation_base_sha": head,
                "automatic_merge": False,
                "dry_run": dry_run,
                "live_agents_invoked": False,
            },
        )
        store = StateStore(paths.state)
        store.save(state)
        state.transition(RunStateName.AWAITING_PHASE_APPROVAL)
        store.save(state)
        EventLog(paths.events).emit(
            run_id=run_id,
            role="ORCHA",
            state=state.state,
            event="phase_contract_prepared",
            message=f"{phase} contract prepared; awaiting operator approval",
            dry_run=dry_run,
        )
        return run_id, contract

    def approve(self, run_id: str) -> RunState:
        paths = self.paths(run_id)
        store = StateStore(paths.state)
        state = store.load()
        if state.state != RunStateName.AWAITING_PHASE_APPROVAL.value:
            raise ControllerError(f"run {run_id} is not awaiting phase approval")
        contract = json.loads(paths.contract_json.read_text(encoding="utf-8"))
        validate_phase_contract(contract)
        manifest = json.loads(paths.manifest.read_text(encoding="utf-8"))
        if manifest.get("dry_run") is not False:
            raise ControllerError("dry-run plans cannot be approved or launched; create a live plan")
        try:
            boundary = self._verify_repository_boundary(require_clean=True)
        except GitSafetyError as exc:
            raise ControllerError(str(exc)) from exc
        if contract["future_implementation_base_sha"] != boundary.head:
            raise ControllerError("Git HEAD changed after planning; re-plan instead of approving stale scope")
        state.transition(RunStateName.IMPLEMENTING)
        store.save(state)
        EventLog(paths.events).emit(
            run_id=run_id,
            role="ORCHA",
            state=state.state,
            event="phase_contract_approved",
            message="Operator approved phase contract",
        )
        return state

    def _request(
        self,
        role: str,
        prompt: str,
        workdir: Path,
        paths: RunPaths,
        schema_name: str | None,
        *,
        session_id: str = "",
        allow_write: bool = False,
        safety_verified: bool = False,
    ) -> AdapterRequest:
        agent = self.config.agents[role]
        schema = (
            self.repository_root / "tools" / "dev_orchestrator" / "schemas" / schema_name
            if schema_name
            else None
        )
        return AdapterRequest(
            role=role,
            prompt=prompt,
            workdir=workdir,
            model=agent.model,
            reasoning_effort=agent.reasoning_effort,
            permission_profile=agent.permission_profile,
            timeout_seconds=self.config.limits.max_agent_runtime_minutes * 60,
            heartbeat_seconds=self.config.limits.heartbeat_seconds,
            log_dir=paths.logs,
            output_schema=schema,
            session_id=session_id,
            allow_write=allow_write,
            safety_verified=safety_verified,
        )

    @staticmethod
    def _invoke(
        adapter: AgentAdapter,
        request: AdapterRequest,
        *,
        resume: bool = False,
        event_callback=None,
    ):
        return adapter.resume(request, event_callback) if resume else adapter.start(request, event_callback)

    def _save_handoff(self, paths: RunPaths, name: str, handoff: dict) -> None:
        _atomic_json(paths.handoffs / name, handoff)

    def _fail_agent(self, state: RunState, store: StateStore, events: EventLog, role: str, error: str):
        if RunStateName(state.state) != RunStateName.OPERATOR_ESCALATION:
            state.transition(RunStateName.OPERATOR_ESCALATION, error)
        else:
            state.last_error = error
            state.updated_at = utc_now()
        store.save(state)
        events.emit(
            run_id=state.run_id,
            role=role.upper(),
            state=state.state,
            event="agent_failure",
            message=error,
        )
        return state

    def _prepare_worktrees(self, state: RunState, paths: RunPaths) -> tuple[Path, Path | None]:
        implementation = paths.worktrees / "implementation"
        if implementation.exists():
            if self.repository.head(implementation) != state.base_sha and not state.result_sha:
                raise GitSafetyError("existing implementation worktree changed from approved base")
            return implementation, None
        branch = f"agent/{state.run_id}/implementation"
        self.repository.create_implementation_worktree(implementation, branch, state.base_sha)
        return implementation, None

    def execute(self, run_id: str, *, resume: bool = False) -> RunState:
        paths = self.paths(run_id)
        store = StateStore(paths.state)
        state = store.load()
        events = EventLog(paths.events)
        if resume and state.state == RunStateName.OPERATOR_ESCALATION.value:
            try:
                self._retry_reviewer_after_tooling_repair(state, paths, store, events)
            except (GitSafetyError, ControllerError, ValueError) as exc:
                return self._fail_agent(state, store, events, "REVIEWER", str(exc))
        if resume and state.state == RunStateName.OPERATOR_ESCALATION.value:
            try:
                self._retry_implementer_strict_output_after_tooling_repair(state, paths, store, events)
            except (GitSafetyError, ControllerError, ValueError) as exc:
                return self._fail_agent(state, store, events, "IMPLEMENTER", str(exc))
        if (
            state.state == RunStateName.OPERATOR_ESCALATION.value
            and state.pending_operator_decision_path
            and not state.pending_implementer_prompt_path
        ):
            try:
                boundary = self._verify_repository_boundary(require_clean=True)
                self._verify_repair_checkout(state, boundary)
            except (GitSafetyError, ControllerError) as exc:
                return self._fail_agent(state, store, events, "ORCHA", str(exc))
            state.controller_sha = boundary.head
            state.transition(RunStateName.ORCHA_DECISION)
            store.save(state)
            events.emit(
                run_id=run_id,
                role="ORCHA",
                state=state.state,
                event="operator_recovery_retry",
                message="Retrying Agent Orcha for the already-recorded operator decision",
                decision_path=state.pending_operator_decision_path,
            )
        if RunStateName(state.state) in TERMINAL_STATES:
            return state
        if state.state == RunStateName.AWAITING_PHASE_APPROVAL.value:
            raise ControllerError("phase contract must be approved before live execution")
        contract = validate_phase_contract(json.loads(paths.contract_json.read_text(encoding="utf-8")))
        contract = self._effective_contract(paths, contract)
        try:
            boundary = self._verify_repository_boundary(require_clean=True)
        except GitSafetyError as exc:
            return self._fail_agent(state, store, events, "ORCHA", str(exc))
        try:
            self._verify_run_controller_head(state, boundary)
        except ControllerError as exc:
            return self._fail_agent(
                state, store, events, "ORCHA", str(exc)
            )
        try:
            implementation_worktree, _ = self._prepare_worktrees(state, paths)
        except GitSafetyError as exc:
            return self._fail_agent(state, store, events, "ORCHA", str(exc))
        if paths.manifest.exists():
            manifest = json.loads(paths.manifest.read_text(encoding="utf-8"))
            manifest["live_agents_invoked"] = True
            _atomic_json(paths.manifest, manifest)

        implementer = self._adapter("implementer")
        reviewer = self._adapter("reviewer")
        orcha = self._adapter("orcha")
        closure_agent = self._adapter("orcha_closure")
        audit_candidate = paths.worktrees / f"audit-{state.correction_cycles:02d}"
        audit_worktree: Path | None = audit_candidate if audit_candidate.exists() else None
        latest_audit: dict | None = None
        latest_audit_path = paths.handoffs / f"audit-{state.correction_cycles:02d}.json"
        if latest_audit_path.exists():
            latest_audit = validate_audit_response(json.loads(latest_audit_path.read_text(encoding="utf-8")))

        def callback(role: str):
            def record(stream: str, event: dict) -> None:
                event_type = str(event.get("type", "agent_event"))
                message = event.get("message") or event.get("text") or event_type
                display_role = "ORCHA" if role.startswith("orcha") else role.upper()
                display_data = {
                    key: event[key]
                    for key in ("elapsed_seconds", "result_sha", "finding_id", "severity", "verdict")
                    if key in event
                }
                events.emit(
                    run_id=run_id,
                    role=display_role,
                    state=state.state,
                    event=event_type,
                    message=str(message),
                    stream=stream,
                    model=self.config.agents[role].model,
                    **display_data,
                )

            return record

        while RunStateName(state.state) not in TERMINAL_STATES:
            try:
                boundary = self._verify_repository_boundary(require_clean=True)
            except GitSafetyError as exc:
                return self._fail_agent(state, store, events, "ORCHA", str(exc))
            try:
                self._verify_run_controller_head(state, boundary)
            except ControllerError as exc:
                return self._fail_agent(
                    state, store, events, "ORCHA", str(exc)
                )
            current = RunStateName(state.state)
            if current in {RunStateName.IMPLEMENTING, RunStateName.CORRECTING}:
                if state.pending_implementer_prompt_path:
                    prompt = (paths.root / state.pending_implementer_prompt_path).read_text(
                        encoding="utf-8"
                    )
                else:
                    prompt = (
                        "Implement only the attached approved phase contract. Return only the required "
                        "structured implementer handoff.\n\n" + json.dumps(contract, indent=2)
                    )
                session_id = state.sessions.get("implementer", "")
                request = self._request(
                    "implementer",
                    prompt,
                    implementation_worktree,
                    paths,
                    "implementer-response.schema.json",
                    session_id=session_id,
                    allow_write=True,
                    safety_verified=True,
                )
                if state.pending_implementer_prompt_path:
                    events.emit(
                        run_id=run_id,
                        role="IMPLEMENTER",
                        state=state.state,
                        event="cursor_resume_started",
                        message="Resuming saved Cursor session with the exact persisted Orcha prompt",
                        session_id=session_id,
                        prompt_path=state.pending_implementer_prompt_path,
                        prompt_sha256=state.latest_orcha_prompt_sha256,
                        implementation_worktree=str(implementation_worktree),
                    )
                result = self._invoke(
                    implementer,
                    request,
                    resume=bool(session_id) or resume,
                    event_callback=callback("implementer"),
                )
                if result.session_id:
                    state.sessions["implementer"] = result.session_id
                    store.save(state)
                if not result.succeeded:
                    return self._fail_agent(state, store, events, "IMPLEMENTER", result.error)
                try:
                    handoff = validate_implementer_response(result.handoff or {})
                except SchemaError as exc:
                    return self._fail_agent(state, store, events, "IMPLEMENTER", str(exc))
                if handoff["base_sha"] != state.base_sha:
                    return self._fail_agent(
                        state,
                        store,
                        events,
                        "IMPLEMENTER",
                        "implementer handoff base SHA differs from approved contract",
                    )
                self._save_handoff(paths, f"implementer-{state.correction_cycles:02d}.json", handoff)
                state.pending_implementer_prompt_path = ""
                state.pending_operator_decision_path = ""
                store.save(state)
                if handoff["status"] == "QUESTION":
                    events.emit(
                        run_id=run_id,
                        role="IMPLEMENTER",
                        state=state.state,
                        event="implementer_question",
                        message="Implementer paused with a structured question",
                    )
                    state.question_cycles += 1
                    if state.question_cycles > self.config.limits.max_question_cycles:
                        return self._fail_agent(
                            state, store, events, "ORCHA", "maximum implementer question cycles reached"
                        )
                    state.transition(RunStateName.IMPLEMENTER_QUESTION)
                    store.save(state)
                    continue
                if handoff["status"] != "IMPLEMENTED":
                    return self._fail_agent(
                        state, store, events, "IMPLEMENTER", f"implementer status {handoff['status']}"
                    )
                state.result_sha = handoff["result_sha"]
                events.emit(
                    run_id=run_id,
                    role="IMPLEMENTER",
                    state=state.state,
                    event="implementation_committed",
                    message="Implementation handoff captured",
                    result_sha=state.result_sha,
                )
                state.transition(RunStateName.VALIDATING_IMPLEMENTATION)
                store.save(state)

            elif current == RunStateName.IMPLEMENTER_QUESTION:
                state.transition(RunStateName.ORCHA_DECISION)
                store.save(state)

            elif current == RunStateName.ORCHA_DECISION:
                if state.pending_operator_decision_path:
                    decision_path = paths.root / state.pending_operator_decision_path
                    operator_decision = validate_operator_decision(
                        json.loads(decision_path.read_text(encoding="utf-8"))
                    )
                    handoff_path = paths.root / state.recovered_handoff_path
                    previous_handoff = validate_implementer_response(
                        json.loads(handoff_path.read_text(encoding="utf-8"))
                    )
                    status = self.repository.status(implementation_worktree)
                    worktree_snapshot = {
                        "path": str(implementation_worktree),
                        "branch": self.repository.branch(implementation_worktree),
                        "head": self.repository.head(implementation_worktree),
                        "status": status,
                        "diff_stat": self.repository.run("diff", "--stat", cwd=implementation_worktree),
                        "existing_implementation_commit": (
                            self.repository.head(implementation_worktree)
                            if self.repository.head(implementation_worktree) != state.base_sha
                            else ""
                        ),
                    }
                    correction_id = f"OPERATOR-DECISION-{state.operator_decision_count:02d}"
                    orcha_context = {
                        "task": (
                            "Generate the exact bounded correction/resume prompt for Cursor. Return "
                            "CORRECT with this finding_ids value and a prompt that preserves completed "
                            "M3A work; applies only the original allowed paths plus the approved added "
                            "path; makes only the candidate_memory milestone-boundary update there; "
                            "preserves all other unimplemented-app assertions; runs the targeted boundary "
                            "test and make verify; fixes causes without weakening tests; commits M3A; "
                            "returns a concise schema-valid handoff; reports IMPLEMENTED only after all "
                            "verification passes and otherwise reports BLOCKED or QUESTION; and forbids "
                            "merge, push, rebase, reset, or main-checkout modification."
                        ),
                        "required_finding_ids": [correction_id],
                        "original_approved_contract": validate_phase_contract(
                            json.loads(paths.contract_json.read_text(encoding="utf-8"))
                        ),
                        "append_only_operator_decision": operator_decision,
                        "previous_implementer_handoff": previous_handoff,
                        "implementation_worktree": worktree_snapshot,
                        "unresolved_controller_error": state.last_error,
                        "applicable_requirement_ids": contract["requirement_ids"],
                        "applicable_decision_ids": contract["governing_decision_ids"],
                        "remaining_acceptance_criteria": contract["acceptance_criteria"],
                        "durable_handoff_instruction": (
                            "Keep the final conversational response short and return only the implementer "
                            "schema JSON (a single json code fence is accepted). The controller captures "
                            "the complete assistant event into its durable final handoff file before any "
                            "terminal presentation truncation."
                        ),
                    }
                    correction = orcha.start(
                        self._request(
                            "orcha",
                            "Generate the bounded operator-authorized recovery prompt from this evidence.\n\n"
                            + json.dumps(orcha_context, indent=2),
                            implementation_worktree,
                            paths,
                            "orcha-correction.schema.json",
                        ),
                        callback("orcha"),
                    )
                    if not correction.succeeded:
                        return self._fail_agent(state, store, events, "ORCHA", correction.error)
                    payload = correction.handoff or {}
                    if (
                        set(payload) != {"decision", "prompt", "finding_ids"}
                        or payload.get("decision") != "CORRECT"
                        or payload.get("finding_ids") != [correction_id]
                    ):
                        return self._fail_agent(
                            state, store, events, "ORCHA", "malformed operator correction contract"
                        )
                    required_prompt_markers = {
                        "job_applications/tests/test_settings.py",
                        "candidate_memory",
                        "targeted",
                        "make verify",
                        "commit",
                        "handoff",
                        "IMPLEMENTED",
                        "BLOCKED",
                        "QUESTION",
                        "merge",
                        "push",
                        "rebase",
                        "reset",
                    }
                    missing_markers = {
                        marker for marker in required_prompt_markers if marker not in payload["prompt"]
                    }
                    main_checkout_boundary = any(
                        marker in payload["prompt"]
                        for marker in ("main checkout", "main-checkout", "modify/check out `main`")
                    )
                    if not main_checkout_boundary:
                        missing_markers.add("main checkout boundary")
                    if missing_markers:
                        return self._fail_agent(
                            state,
                            store,
                            events,
                            "ORCHA",
                            "operator correction prompt omitted required boundaries: "
                            f"{sorted(missing_markers)}",
                        )
                    correction_path = self._next_artifact_path(
                        paths.handoffs, "operator-correction"
                    )
                    self._append_only_json(correction_path, payload)
                    prompt_path = self._next_artifact_path(paths.prompts, "orcha-correction", ".txt")
                    _atomic_text(prompt_path, payload["prompt"])
                    prompt_hash = _sha256_bytes(payload["prompt"].encode("utf-8"))
                    state.latest_orcha_prompt_path = self._relative_artifact(paths, prompt_path)
                    state.latest_orcha_prompt_sha256 = prompt_hash
                    state.pending_implementer_prompt_path = state.latest_orcha_prompt_path
                    state.correction_cycles += 1
                    state.transition(RunStateName.CORRECTING)
                    state.last_error = ""
                    store.save(state)
                    events.emit(
                        run_id=run_id,
                        role="ORCHA",
                        state=state.state,
                        event="orcha_correction_prompt_generated",
                        message="Agent Orcha generated the bounded correction prompt",
                        prompt_path=state.latest_orcha_prompt_path,
                        prompt_sha256=prompt_hash,
                        correction_path=self._relative_artifact(paths, correction_path),
                    )
                    continue
                handoff = validate_implementer_response(
                    json.loads(
                        (paths.handoffs / f"implementer-{state.correction_cycles:02d}.json").read_text(
                            encoding="utf-8"
                        )
                    )
                )
                if handoff["session_id"] and not state.sessions.get("implementer"):
                    state.sessions["implementer"] = handoff["session_id"]
                    store.save(state)
                decision_request = self._request(
                    "orcha",
                    "Answer from existing authoritative documents only; otherwise escalate.\n\n"
                    + json.dumps(handoff, indent=2),
                    implementation_worktree,
                    paths,
                    "orcha-decision.schema.json",
                )
                decision = orcha.start(decision_request, callback("orcha"))
                if not decision.succeeded:
                    return self._fail_agent(state, store, events, "ORCHA", decision.error)
                payload = decision.handoff or {}
                if set(payload) != {"decision", "answer"} or payload.get("decision") not in {
                    "ANSWER",
                    "BLOCKED",
                    "ESCALATE",
                }:
                    return self._fail_agent(state, store, events, "ORCHA", "malformed Orcha decision")
                self._save_handoff(paths, f"orcha-question-{state.question_cycles:02d}.json", payload)
                if payload["decision"] != "ANSWER":
                    return self._fail_agent(state, store, events, "ORCHA", payload.get("answer", ""))
                contract = dict(contract)
                contract["objective"] += f"\n\nApproved clarification: {payload['answer']}"
                state.transition(RunStateName.IMPLEMENTING)
                store.save(state)
                resume = True

            elif current == RunStateName.VALIDATING_IMPLEMENTATION:
                try:
                    implementer_handoff = json.loads(
                        (paths.handoffs / f"implementer-{state.correction_cycles:02d}.json").read_text(
                            encoding="utf-8"
                        )
                    )
                    executable_required_tests = {
                        command
                        for command in contract["required_tests"]
                        if command.startswith(("make ", "git ", ".venv/"))
                    }
                    reported_commands = {item["command"] for item in implementer_handoff["test_commands"]}
                    missing_commands = executable_required_tests - reported_commands
                    if missing_commands:
                        raise EvidenceError(
                            f"required verification commands missing: {sorted(missing_commands)}"
                        )
                    missing_requirements = set(contract["requirement_ids"]) - set(
                        implementer_handoff["requirements_addressed"]
                    )
                    if missing_requirements:
                        raise EvidenceError(
                            f"required requirement coverage missing: {sorted(missing_requirements)}"
                        )
                    missing_documentation = set(contract["required_documentation_updates"]) - set(
                        implementer_handoff["documentation_updated"]
                    )
                    if missing_documentation:
                        raise EvidenceError(
                            f"required documentation updates missing: {sorted(missing_documentation)}"
                        )
                    evidence = self.evidence_collector.collect_and_validate(
                        worktree=implementation_worktree,
                        base_sha=state.base_sha,
                        result_sha=state.result_sha,
                        allowed_paths=contract["allowed_paths"],
                        prohibited_paths=contract["prohibited_paths"],
                        test_commands=implementer_handoff["test_commands"],
                        authorized_test_changes=[
                            path
                            for decision_path in sorted(
                                paths.handoffs.glob("operator-decision-*.json")
                            )
                            for path in validate_operator_decision(
                                json.loads(decision_path.read_text(encoding="utf-8"))
                            )["additional_allowed_paths"]
                        ],
                    )
                    _atomic_json(
                        paths.evidence,
                        dataclasses.asdict(evidence) if dataclasses.is_dataclass(evidence) else evidence,
                    )
                    if dataclasses.is_dataclass(evidence) and sorted(
                        implementer_handoff["files_changed"]
                    ) != sorted(evidence.changed_files):
                        raise EvidenceError(
                            "implementer files_changed does not match controller-collected Git diff"
                        )
                    if dataclasses.is_dataclass(evidence):
                        unchanged_documentation = set(contract["required_documentation_updates"]) - set(
                            evidence.changed_files
                        )
                        if unchanged_documentation:
                            raise EvidenceError(
                                "required documentation files were not changed: "
                                f"{sorted(unchanged_documentation)}"
                            )
                except (EvidenceError, GitSafetyError, OSError, json.JSONDecodeError) as exc:
                    return self._fail_agent(state, store, events, "ORCHA", str(exc))
                events.emit(
                    run_id=run_id,
                    role="ORCHA",
                    state=state.state,
                    event="evidence_validated",
                    message="Deterministic implementation evidence validated",
                    result_sha=state.result_sha,
                )
                audit_worktree = paths.worktrees / f"audit-{state.correction_cycles:02d}"
                if not audit_worktree.exists():
                    try:
                        self.repository.create_audit_worktree(audit_worktree, state.result_sha)
                        self.repository.assert_isolated_worktrees(implementation_worktree, audit_worktree)
                    except GitSafetyError as exc:
                        return self._fail_agent(state, store, events, "ORCHA", str(exc))
                state.transition(RunStateName.AUDITING)
                store.save(state)

            elif current == RunStateName.AUDITING:
                assert audit_worktree is not None
                audit_request = self._request(
                    "reviewer",
                    "Independently audit the contract and candidate. Return only the audit schema.\n\n"
                    + json.dumps(contract, indent=2),
                    audit_worktree,
                    paths,
                    "audit-response.schema.json",
                )
                result = reviewer.start(audit_request, callback("reviewer"))
                if not result.succeeded:
                    return self._fail_agent(state, store, events, "REVIEWER", result.error)
                try:
                    latest_audit = validate_audit_response(result.handoff or {})
                except SchemaError as exc:
                    return self._fail_agent(state, store, events, "REVIEWER", str(exc))
                if (
                    latest_audit["base_sha"] != state.base_sha
                    or latest_audit["candidate_sha"] != state.result_sha
                ):
                    return self._fail_agent(
                        state,
                        store,
                        events,
                        "REVIEWER",
                        "audit response SHA range differs from deterministic candidate range",
                    )
                failed_audit_commands = [
                    item for item in latest_audit["test_commands"] if item["exit_code"] != 0
                ]
                if failed_audit_commands and latest_audit["verdict"] != "CORRECTION_REQUIRED":
                    return self._fail_agent(
                        state,
                        store,
                        events,
                        "REVIEWER",
                        "reviewer reported a failed deterministic test command",
                    )
                try:
                    observed_audit_commands = self.evidence_collector.validate_test_commands(
                        audit_worktree,
                        [
                            item
                            for item in latest_audit["test_commands"]
                            if item["exit_code"] == 0
                        ],
                    )
                except EvidenceError as exc:
                    return self._fail_agent(state, store, events, "REVIEWER", str(exc))
                _atomic_json(
                    paths.handoffs / f"audit-verification-{state.correction_cycles:02d}.json",
                    {
                        "commands": observed_audit_commands,
                        "failed_reviewer_commands": failed_audit_commands,
                        "implementation_evidence_path": str(paths.evidence),
                    },
                )
                self._save_handoff(paths, f"audit-{state.correction_cycles:02d}.json", latest_audit)
                if failed_audit_commands:
                    events.emit(
                        run_id=run_id,
                        role="REVIEWER",
                        state=state.state,
                        event="reviewer_failed_test_recorded",
                        message=(
                            "Reviewer-side test failure retained with CORRECTION_REQUIRED findings; "
                            "it cannot support a PASS verdict"
                        ),
                    )
                events.emit(
                    run_id=run_id,
                    role="REVIEWER",
                    state=state.state,
                    event="audit_verdict",
                    message=latest_audit["summary"],
                    verdict=latest_audit["verdict"],
                )
                for item in latest_audit["findings"]:
                    events.emit(
                        run_id=run_id,
                        role="REVIEWER",
                        state=state.state,
                        event="audit_finding",
                        message=item["explanation"],
                        finding_id=item["finding_id"],
                        severity=item["severity"],
                    )
                if latest_audit["audit_test_requested"]:
                    try:
                        self.repository.create_audit_branch(
                            audit_worktree,
                            f"agent/{state.run_id}/audit-{state.correction_cycles:02d}",
                            state.result_sha,
                        )
                    except GitSafetyError as exc:
                        return self._fail_agent(state, store, events, "REVIEWER", str(exc))
                    events.emit(
                        run_id=run_id,
                        role="REVIEWER",
                        state=state.state,
                        event="audit_test_write_started",
                        message="Safety-verified audit branch enabled for adversarial tests only",
                    )
                    test_result = reviewer.start(
                        self._request(
                            "reviewer",
                            "Add and commit only the adversarial tests requested by this read-only audit. "
                            "Do not modify production code. Return the updated audit schema.\n\n"
                            + json.dumps(latest_audit, indent=2),
                            audit_worktree,
                            paths,
                            "audit-response.schema.json",
                            allow_write=True,
                            safety_verified=True,
                        ),
                        callback("reviewer"),
                    )
                    if not test_result.succeeded:
                        return self._fail_agent(state, store, events, "REVIEWER", test_result.error)
                    try:
                        test_audit = validate_audit_response(test_result.handoff or {})
                        if (
                            test_audit["base_sha"] != state.base_sha
                            or test_audit["candidate_sha"] != state.result_sha
                            or test_audit["audit_test_requested"]
                            or not test_audit["audit_test_commit_sha"]
                            or test_audit["verdict"] != latest_audit["verdict"]
                            or {item["finding_id"] for item in test_audit["findings"]}
                            != {item["finding_id"] for item in latest_audit["findings"]}
                            or not any(
                                item["test_added"]
                                and item["test_commit_sha"] == test_audit["audit_test_commit_sha"]
                                for item in test_audit["findings"]
                            )
                        ):
                            raise SchemaError("audit-test handoff changed scope or omitted its commit")
                        audit_test_files = self.repository.validate_audit_test_commit(
                            audit_worktree,
                            state.result_sha,
                            test_audit["audit_test_commit_sha"],
                        )
                        observed_test_commands = self.evidence_collector.validate_test_commands(
                            audit_worktree, test_audit["test_commands"]
                        )
                    except (SchemaError, GitSafetyError, EvidenceError) as exc:
                        return self._fail_agent(state, store, events, "REVIEWER", str(exc))
                    self._save_handoff(
                        paths,
                        f"audit-test-{state.correction_cycles:02d}.json",
                        test_audit,
                    )
                    _atomic_json(
                        paths.handoffs / f"audit-test-verification-{state.correction_cycles:02d}.json",
                        {"files": audit_test_files, "commands": observed_test_commands},
                    )
                    return self._fail_agent(
                        state,
                        store,
                        events,
                        "REVIEWER",
                        "audit test commit is ready and requires explicit operator transfer approval; "
                        "no transfer performed",
                    )
                if latest_audit["audit_test_commit_sha"]:
                    audit_commit = latest_audit["audit_test_commit_sha"]
                    try:
                        self.repository.validate_audit_test_commit(
                            audit_worktree, state.result_sha, audit_commit
                        )
                    except GitSafetyError as exc:
                        return self._fail_agent(
                            state,
                            store,
                            events,
                            "REVIEWER",
                            str(exc),
                        )
                    return self._fail_agent(
                        state,
                        store,
                        events,
                        "REVIEWER",
                        "audit test commit requires explicit operator transfer approval; "
                        "no transfer performed",
                    )
                if latest_audit["verdict"] == "BLOCKED":
                    state.transition(RunStateName.BLOCKED, latest_audit["summary"])
                    store.save(state)
                    return state
                if latest_audit["verdict"] == "PASS":
                    state.transition(RunStateName.CLOSURE_REVIEW)
                    store.save(state)
                    continue

                open_ids = [
                    item["finding_id"] for item in latest_audit["findings"] if item["status"] == "OPEN"
                ]
                for finding_id in open_ids:
                    state.finding_occurrences[finding_id] = state.finding_occurrences.get(finding_id, 0) + 1
                if self.config.limits.stop_on_repeated_finding and any(
                    state.finding_occurrences[finding_id] >= 3 for finding_id in open_ids
                ):
                    return self._fail_agent(
                        state, store, events, "ORCHA", "same material finding survived two corrections"
                    )
                if state.correction_cycles >= self.config.limits.max_correction_cycles:
                    return self._fail_agent(
                        state, store, events, "ORCHA", "maximum correction cycles reached"
                    )
                state.transition(RunStateName.CORRECTION_REQUIRED)
                store.save(state)

            elif current == RunStateName.CORRECTION_REQUIRED:
                state.transition(RunStateName.ORCHA_CORRECTION_CONTRACT)
                store.save(state)

            elif current == RunStateName.ORCHA_CORRECTION_CONTRACT:
                if latest_audit is None:
                    return self._fail_agent(
                        state, store, events, "ORCHA", "saved audit handoff is unavailable"
                    )
                open_ids = [
                    item["finding_id"] for item in latest_audit["findings"] if item["status"] == "OPEN"
                ]
                correction = orcha.start(
                    self._request(
                        "orcha",
                        "Create a bounded correction prompt from these stable audit findings.\n\n"
                        + json.dumps(latest_audit, indent=2),
                        implementation_worktree,
                        paths,
                        "orcha-correction.schema.json",
                    ),
                    callback("orcha"),
                )
                if not correction.succeeded:
                    return self._fail_agent(state, store, events, "ORCHA", correction.error)
                payload = correction.handoff or {}
                if set(payload) != {"decision", "prompt", "finding_ids"} or payload.get("decision") not in {
                    "CORRECT",
                    "ESCALATE",
                }:
                    return self._fail_agent(state, store, events, "ORCHA", "malformed correction contract")
                if payload["decision"] != "CORRECT":
                    return self._fail_agent(state, store, events, "ORCHA", payload.get("prompt", ""))
                if sorted(payload["finding_ids"]) != sorted(open_ids):
                    return self._fail_agent(
                        state, store, events, "ORCHA", "correction contract changed stable finding IDs"
                    )
                self._save_handoff(paths, f"correction-{state.correction_cycles + 1:02d}.json", payload)
                contract = dict(contract)
                contract["objective"] += f"\n\nCorrection: {payload['prompt']}"
                state.correction_cycles += 1
                state.transition(RunStateName.CORRECTING)
                store.save(state)
                resume = True

            elif current == RunStateName.CLOSURE_REVIEW:
                closure = closure_agent.start(
                    self._request(
                        "orcha_closure",
                        "Evaluate deterministic evidence and audit; do not merge or push.\n\n"
                        + json.dumps(latest_audit or {}, indent=2),
                        audit_worktree or implementation_worktree,
                        paths,
                        "closure-response.schema.json",
                    ),
                    callback("orcha_closure"),
                )
                if not closure.succeeded:
                    return self._fail_agent(state, store, events, "ORCHA", closure.error)
                try:
                    payload = validate_closure_response(closure.handoff or {})
                except SchemaError as exc:
                    return self._fail_agent(state, store, events, "ORCHA", str(exc))
                self._save_handoff(paths, "closure.json", payload)
                if (
                    payload["base_sha"] != state.base_sha
                    or payload["final_sha"] != state.result_sha
                    or not set(contract["requirement_ids"]).issubset(payload["accepted_requirement_ids"])
                    or payload["unresolved_findings"]
                    or payload["merge_recommendation"] != "MERGE"
                ):
                    return self._fail_agent(
                        state, store, events, "ORCHA", "closure response does not support completion"
                    )
                state.transition(RunStateName.COMPLETED)
                store.save(state)
                events.emit(
                    run_id=run_id,
                    role="ORCHA",
                    state=state.state,
                    event="run_completed",
                    message="Closure review passed; operator merge gate remains closed",
                    result_sha=state.result_sha,
                )
                return state
            else:
                return self._fail_agent(
                    state, store, events, "ORCHA", f"cannot automatically resume state {state.state}"
                )
        return state

    def abort(self, run_id: str) -> RunState:
        paths = self.paths(run_id)
        store = StateStore(paths.state)
        state = store.load()
        if RunStateName(state.state) in TERMINAL_STATES:
            return state
        state.transition(RunStateName.ABORTED, "aborted by operator")
        store.save(state)
        EventLog(paths.events).emit(
            run_id=run_id,
            role="ORCHA",
            state=state.state,
            event="run_aborted",
            message="Run aborted by operator; no worktree or branch was deleted",
        )
        return state
