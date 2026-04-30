"""Tests for Stage 4 — Real LLM Agents and Diagnostic Utility.

All tests use mock LLMs; no real API keys required.
The 49 prior tests (Stages 1-3) must continue to pass unchanged.
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.agents.diagnostic import DiagnosticUtility
from src.agents.nodes import (
    _diagnosis_context,
    _extract_json,
    dev_node,
    qa_node,
    release_engineer_node,
    review_node,
    supervisor_node,
    tech_lead_node,
)
from src.core.llm import BaseLLM, LiteLLMBackend, StubLLM, load_llm
from src.state.schema import SDLCRequirement, SDLCState, ToolEvidence


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class MockLLM:
    """Simple mock LLM that returns a fixed reply per call or from a queue."""

    def __init__(self, reply: str | list[str] = "mock reply") -> None:
        if isinstance(reply, str):
            self._replies = [reply]
        else:
            self._replies = list(reply)
        self._index = 0
        self.calls: list[list[dict]] = []

    def chat(self, messages: list[dict]) -> str:
        self.calls.append(messages)
        if self._index < len(self._replies):
            r = self._replies[self._index]
        else:
            r = self._replies[-1]
        self._index += 1
        return r


def _make_state(**kwargs: Any) -> SDLCState:
    defaults = dict(
        run_id="test-stage4",
        objective="Build a simple calculator",
        context="CLI tool",
        success_criteria=["Addition works", "Subtraction works"],
        requirements=[
            SDLCRequirement(
                id="REQ-001",
                description="Addition",
                acceptance_criteria=["add(1,2) == 3"],
            )
        ],
    )
    defaults.update(kwargs)
    return SDLCState(**defaults)


def _passing_evidence(tool: str) -> ToolEvidence:
    return ToolEvidence(tool_name=tool, passed=True, findings="All good")


def _failing_evidence(tool: str, findings: str = "Error found") -> ToolEvidence:
    return ToolEvidence(tool_name=tool, passed=False, findings=findings)


# ---------------------------------------------------------------------------
# 1. Tests: src/core/llm.py
# ---------------------------------------------------------------------------

class TestLLMModule:
    def test_litellm_backend_implements_protocol(self, tmp_path: Path) -> None:
        backend = LiteLLMBackend(model="deepseek/deepseek-chat", api_key="test-key")
        assert isinstance(backend, BaseLLM)

    def test_stub_llm_implements_protocol(self) -> None:
        stub = StubLLM()
        assert isinstance(stub, BaseLLM)

    def test_load_llm_returns_stub_when_no_api_key(self, tmp_path: Path) -> None:
        import yaml
        cfg = {"main": {"model": "deepseek/deepseek-chat", "api_key_env": "TOTALLY_MISSING_KEY_XYZ"}}
        cfg_path = tmp_path / "llm.yaml"
        cfg_path.write_text(yaml.dump(cfg))
        llm = load_llm("main", config_path=cfg_path)
        assert isinstance(llm, StubLLM)

    def test_load_llm_returns_litellm_backend_when_key_set(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import yaml
        monkeypatch.setenv("MY_TEST_KEY", "sk-test-1234")
        cfg = {"main": {"model": "deepseek/deepseek-chat", "api_key_env": "MY_TEST_KEY"}}
        cfg_path = tmp_path / "llm.yaml"
        cfg_path.write_text(yaml.dump(cfg))
        llm = load_llm("main", config_path=cfg_path)
        assert isinstance(llm, LiteLLMBackend)

    def test_load_llm_missing_section_raises(self, tmp_path: Path) -> None:
        import yaml
        cfg = {"main": {"model": "deepseek/deepseek-chat"}}
        cfg_path = tmp_path / "llm.yaml"
        cfg_path.write_text(yaml.dump(cfg))
        with pytest.raises(KeyError, match="diagnostic"):
            load_llm("diagnostic", config_path=cfg_path)

    def test_load_llm_reads_temperature_and_max_tokens(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import yaml
        monkeypatch.setenv("MY_TEST_KEY2", "sk-real-key")
        cfg = {"main": {"model": "x/y", "api_key_env": "MY_TEST_KEY2", "temperature": 0.7, "max_tokens": 512}}
        cfg_path = tmp_path / "llm.yaml"
        cfg_path.write_text(yaml.dump(cfg))
        llm = load_llm("main", config_path=cfg_path)
        assert isinstance(llm, LiteLLMBackend)
        assert llm.temperature == 0.7
        assert llm.max_tokens == 512

    def test_stub_llm_tech_lead_returns_valid_json(self) -> None:
        stub = StubLLM()
        messages = [
            {"role": "system", "content": "You are a principal engineer. Return JSON with requirements and architecture_doc."},
            {"role": "user", "content": "Project objective: build a calculator"},
        ]
        reply = stub.chat(messages)
        parsed = json.loads(reply)
        assert "requirements" in parsed
        assert "architecture_doc" in parsed

    def test_stub_llm_dev_returns_python_with_tag(self) -> None:
        stub = StubLLM()
        messages = [
            {"role": "system", "content": "You are an expert Python developer. Write production-ready Python code."},
            {"role": "user", "content": "Requirement ID: REQ-042\nDescription: test"},
        ]
        reply = stub.chat(messages)
        assert "# Requirement: REQ-042" in reply
        assert "def stub_req_042" in reply

    def test_stub_llm_qa_returns_test_code(self) -> None:
        stub = StubLLM()
        messages = [
            {"role": "system", "content": "You are a senior QA engineer writing pytest test files."},
            {"role": "user", "content": "Requirement ID: REQ-007\nDescription: thing"},
        ]
        reply = stub.chat(messages)
        assert "# Requirement: REQ-007" in reply
        assert "def test_" in reply


# ---------------------------------------------------------------------------
# 2. Tests: src/agents/diagnostic.py
# ---------------------------------------------------------------------------

class TestDiagnosticUtility:
    def test_diagnose_failing_returns_nonempty(self) -> None:
        llm = MockLLM("Fix the import on line 5.")
        du = DiagnosticUtility(llm)
        ev = _failing_evidence("ruff", "E401 multiple imports on line 5")
        result = du.diagnose(ev)
        assert result == "Fix the import on line 5."
        assert len(llm.calls) == 1

    def test_diagnose_passing_returns_empty(self) -> None:
        llm = MockLLM("Should not be called")
        du = DiagnosticUtility(llm)
        ev = _passing_evidence("ruff")
        result = du.diagnose(ev)
        assert result == ""
        assert len(llm.calls) == 0

    def test_diagnose_prompt_contains_tool_name_and_findings(self) -> None:
        llm = MockLLM("fix it")
        du = DiagnosticUtility(llm)
        ev = _failing_evidence("pytest", "FAILED test_foo.py::test_bar")
        du.diagnose(ev)
        user_msg = llm.calls[0][1]["content"]
        assert "pytest" in user_msg
        assert "FAILED test_foo.py::test_bar" in user_msg


# ---------------------------------------------------------------------------
# 3. Tests: helper functions in nodes.py
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_extract_json_fenced(self) -> None:
        text = '```json\n{"a": 1}\n```'
        assert _extract_json(text) == '{"a": 1}'

    def test_extract_json_unfenced_object(self) -> None:
        text = 'Here is the result: {"b": 2}'
        assert _extract_json(text) == '{"b": 2}'

    def test_extract_json_unfenced_array(self) -> None:
        text = "Result: [1, 2, 3]"
        assert _extract_json(text) == "[1, 2, 3]"

    def test_extract_json_no_json_returns_input(self) -> None:
        text = "no json here"
        assert _extract_json(text) == "no json here"

    def test_diagnosis_context_empty_when_no_failures(self) -> None:
        evidence = [_passing_evidence("ruff"), _passing_evidence("pytest")]
        assert _diagnosis_context(evidence) == ""

    def test_diagnosis_context_empty_when_failures_have_no_diagnosis(self) -> None:
        ev = _failing_evidence("ruff")
        # diagnosis is None by default
        assert _diagnosis_context([ev]) == ""

    def test_diagnosis_context_includes_diagnosis(self) -> None:
        ev = _failing_evidence("ruff")
        ev.diagnosis = "Fix imports"
        result = _diagnosis_context([ev])
        assert "ruff" in result
        assert "Fix imports" in result


# ---------------------------------------------------------------------------
# 4. Tests: tech_lead_node
# ---------------------------------------------------------------------------

class TestTechLeadNode:
    def test_generates_requirements_from_llm_json(self) -> None:
        requirements_json = json.dumps({
            "requirements": [
                {"id": "REQ-001", "description": "Add numbers", "acceptance_criteria": ["1+1==2"]},
                {"id": "REQ-002", "description": "Subtract numbers", "acceptance_criteria": ["3-1==2"]},
            ],
            "architecture_doc": "# Arch\nSimple CLI tool.",
        })
        llm = MockLLM(requirements_json)
        state = _make_state(requirements=[])
        result = tech_lead_node(state, llm=llm)
        assert len(result.requirements) == 2
        assert result.requirements[0].id == "REQ-001"
        assert result.requirements[1].id == "REQ-002"
        assert result.architecture_doc == "# Arch\nSimple CLI tool."
        assert result.current_phase == "implementation"

    def test_falls_back_to_stub_req_on_invalid_json(self) -> None:
        llm = MockLLM("not valid json at all")
        state = _make_state(requirements=[])
        result = tech_lead_node(state, llm=llm)
        assert len(result.requirements) == 1
        assert result.requirements[0].id == "REQ-001"

    def test_risk_level_high_for_security_keyword(self) -> None:
        req_json = json.dumps({
            "requirements": [{"id": "REQ-001", "description": "Auth", "acceptance_criteria": []}],
            "architecture_doc": "arch",
        })
        llm = MockLLM(req_json)
        state = _make_state(requirements=[], objective="Implement auth system")
        result = tech_lead_node(state, llm=llm)
        assert result.risk_level == "high"

    def test_lessons_learned_included_in_prompt(self) -> None:
        req_json = json.dumps({
            "requirements": [{"id": "REQ-001", "description": "x", "acceptance_criteria": []}],
            "architecture_doc": "arch",
        })
        llm = MockLLM(req_json)
        state = _make_state(requirements=[], lessons_learned=["Avoid global state"])
        tech_lead_node(state, llm=llm)
        system_msg = llm.calls[0][0]["content"]
        assert "Avoid global state" in system_msg

    def test_strips_malformed_requirements_from_list(self) -> None:
        req_json = json.dumps({
            "requirements": [
                {"id": "REQ-001", "description": "Good", "acceptance_criteria": []},
                {"id": 999, "description": None},  # malformed — will be skipped
            ],
            "architecture_doc": "",
        })
        llm = MockLLM(req_json)
        state = _make_state(requirements=[])
        result = tech_lead_node(state, llm=llm)
        # Malformed item skipped; but REQ-001 is fine — only 1 valid req
        assert any(r.id == "REQ-001" for r in result.requirements)


# ---------------------------------------------------------------------------
# 5. Tests: dev_node
# ---------------------------------------------------------------------------

class TestDevNode:
    def test_writes_file_with_requirement_tag(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import src.io.workspace as ws
        monkeypatch.setattr(ws, "VOLUMES_DIR", tmp_path)

        code = "def add(a: int, b: int) -> int:\n    return a + b\n"
        llm = MockLLM(code)
        state = _make_state()
        result = dev_node(state, llm=llm)

        assert len(result.files_changed) == 1
        written = (tmp_path / "test-stage4" / "src" / "req_001_impl.py").read_text()
        assert "# Requirement: REQ-001" in written
        assert "def add" in written

    def test_prepends_tag_if_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import src.io.workspace as ws
        monkeypatch.setattr(ws, "VOLUMES_DIR", tmp_path)

        code = "def add(a, b):\n    return a + b\n"  # no tag
        llm = MockLLM(code)
        state = _make_state()
        dev_node(state, llm=llm)
        written = (tmp_path / "test-stage4" / "src" / "req_001_impl.py").read_text()
        assert written.startswith("# Requirement: REQ-001")

    def test_injects_diagnosis_context_in_prompt(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import src.io.workspace as ws
        monkeypatch.setattr(ws, "VOLUMES_DIR", tmp_path)

        llm = MockLLM("# Requirement: REQ-001\npass")
        ev = _failing_evidence("ruff")
        ev.diagnosis = "Remove unused import on line 3"
        state = _make_state(gate_evidence=[ev])
        dev_node(state, llm=llm)
        user_msg = llm.calls[0][1]["content"]
        assert "Remove unused import on line 3" in user_msg

    def test_phase_set_to_testing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import src.io.workspace as ws
        monkeypatch.setattr(ws, "VOLUMES_DIR", tmp_path)
        llm = MockLLM("# Requirement: REQ-001\npass")
        state = _make_state()
        result = dev_node(state, llm=llm)
        assert result.current_phase == "testing"

    def test_parallel_generation_preserves_requirement_order(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import time
        import src.io.workspace as ws

        class ReqAwareLLM:
            def chat(self, messages: list[dict]) -> str:
                content = messages[1]["content"]
                req_id = "REQ-002" if "REQ-002" in content else "REQ-001"
                if req_id == "REQ-001":
                    time.sleep(0.03)
                return f"# Requirement: {req_id}\ndef impl_{req_id.lower().replace('-', '_')}() -> None:\n    pass\n"

        monkeypatch.setattr(ws, "VOLUMES_DIR", tmp_path)
        monkeypatch.setenv("OMEGA_LLM_CONCURRENCY", "2")
        state = _make_state(requirements=[
            SDLCRequirement(id="REQ-001", description="First", acceptance_criteria=[]),
            SDLCRequirement(id="REQ-002", description="Second", acceptance_criteria=[]),
        ])

        result = dev_node(state, llm=ReqAwareLLM())

        assert [fc.requirement_id for fc in result.files_changed] == ["REQ-001", "REQ-002"]
        assert [fc.path for fc in result.files_changed] == ["src/req_001_impl.py", "src/req_002_impl.py"]


# ---------------------------------------------------------------------------
# 6. Tests: qa_node
# ---------------------------------------------------------------------------

class TestQaNode:
    def test_writes_test_file_with_tag(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import src.io.workspace as ws
        monkeypatch.setattr(ws, "VOLUMES_DIR", tmp_path)

        test_code = textwrap.dedent("""\
            import sys
            from pathlib import Path
            sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))
            from req_001_impl import add

            def test_add() -> None:
                assert add(1, 2) == 3
        """)
        llm = MockLLM(test_code)
        state = _make_state()
        result = qa_node(state, llm=llm)

        assert len(result.tests_written) == 1
        written = (tmp_path / "test-stage4" / "tests" / "test_req_001.py").read_text()
        assert "# Requirement: REQ-001" in written
        assert "def test_" in written

    def test_phase_set_to_review(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import src.io.workspace as ws
        monkeypatch.setattr(ws, "VOLUMES_DIR", tmp_path)
        llm = MockLLM("# Requirement: REQ-001\ndef test_x(): pass")
        state = _make_state()
        result = qa_node(state, llm=llm)
        assert result.current_phase == "review"

    def test_parallel_generation_preserves_requirement_order(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import time
        import src.io.workspace as ws

        class ReqAwareLLM:
            def chat(self, messages: list[dict]) -> str:
                content = messages[1]["content"]
                req_id = "REQ-002" if "REQ-002" in content else "REQ-001"
                if req_id == "REQ-001":
                    time.sleep(0.03)
                return f"# Requirement: {req_id}\ndef test_{req_id.lower().replace('-', '_')}() -> None:\n    pass\n"

        monkeypatch.setattr(ws, "VOLUMES_DIR", tmp_path)
        monkeypatch.setenv("OMEGA_LLM_CONCURRENCY", "2")
        state = _make_state(
            requirements=[
                SDLCRequirement(id="REQ-001", description="First", acceptance_criteria=[]),
                SDLCRequirement(id="REQ-002", description="Second", acceptance_criteria=[]),
            ],
            files_changed=[],
        )

        result = qa_node(state, llm=ReqAwareLLM())

        assert [fc.requirement_id for fc in result.tests_written] == ["REQ-001", "REQ-002"]
        assert [fc.path for fc in result.tests_written] == ["tests/test_req_001.py", "tests/test_req_002.py"]


# ---------------------------------------------------------------------------
# 7. Tests: review_node
# ---------------------------------------------------------------------------

class TestReviewNodeStage4:
    def _passing_tools(self) -> dict:
        return {
            "run_ruff": MagicMock(return_value=_passing_evidence("ruff")),
            "run_pytest": MagicMock(return_value=_passing_evidence("pytest")),
            "run_mypy": MagicMock(return_value=_passing_evidence("mypy")),
            "run_bandit": MagicMock(return_value=_passing_evidence("bandit")),
            "run_pip_audit": MagicMock(return_value=_passing_evidence("pip_audit")),
            "run_complexity_check": MagicMock(return_value=_passing_evidence("radon")),
        }

    def test_diagnosis_called_only_on_failures(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import src.agents.nodes as nodes_mod
        tools = self._passing_tools()
        tools["run_ruff"] = MagicMock(return_value=_failing_evidence("ruff", "E101"))
        for name, mock in tools.items():
            monkeypatch.setattr(nodes_mod, name, mock)

        diag_llm = MockLLM("Fix E101 by sorting imports.")
        state = _make_state()
        result = review_node(state, llm=diag_llm)

        ruff_ev = next(e for e in result.gate_evidence if e.tool_name == "ruff")
        assert ruff_ev.diagnosis == "Fix E101 by sorting imports."
        # Passing tools have no diagnosis
        for ev in result.gate_evidence:
            if ev.passed:
                assert ev.diagnosis is None

    def test_diagnosis_not_called_when_all_pass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import src.agents.nodes as nodes_mod
        for name, mock in self._passing_tools().items():
            monkeypatch.setattr(nodes_mod, name, mock)

        diag_llm = MockLLM("should not be called")
        state = _make_state()
        review_node(state, llm=diag_llm)
        assert diag_llm.calls == []  # no LLM calls for passing evidence

    def test_diagnosis_exception_sets_fallback_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import src.agents.nodes as nodes_mod
        tools = self._passing_tools()
        tools["run_ruff"] = MagicMock(return_value=_failing_evidence("ruff"))
        for name, mock in tools.items():
            monkeypatch.setattr(nodes_mod, name, mock)

        class ExplodingLLM:
            def chat(self, messages: list[dict]) -> str:
                raise RuntimeError("API down")

        state = _make_state()
        result = review_node(state, llm=ExplodingLLM())
        ruff_ev = next(e for e in result.gate_evidence if e.tool_name == "ruff")
        assert ruff_ev.diagnosis is not None
        assert "Diagnosis unavailable" in ruff_ev.diagnosis


# ---------------------------------------------------------------------------
# 8. Tests: release_engineer_node
# ---------------------------------------------------------------------------

class TestReleaseEngineerNodeStage4:
    def test_generates_release_notes_via_llm(self) -> None:
        llm = MockLLM("# Release Notes\n\nAll good.")
        state = _make_state(
            gate_evidence=[_passing_evidence("ruff"), _passing_evidence("pytest")],
        )
        result = release_engineer_node(state, llm=llm)
        assert result.release_notes == "# Release Notes\n\nAll good."
        assert result.current_phase == "done"

    def test_uses_fallback_when_llm_fails(self) -> None:
        class ExplodingLLM:
            def chat(self, messages: list[dict]) -> str:
                raise RuntimeError("network error")

        state = _make_state(
            gate_evidence=[_passing_evidence("ruff"), _passing_evidence("pytest")],
        )
        result = release_engineer_node(state, llm=ExplodingLLM())
        assert result.release_notes is not None
        assert state.run_id in result.release_notes
        assert result.current_phase == "done"

    def test_blocks_when_required_gate_fails(self) -> None:
        llm = MockLLM("should not be called")
        state = _make_state(
            gate_evidence=[_failing_evidence("ruff"), _passing_evidence("pytest")],
        )
        result = release_engineer_node(state, llm=llm)
        assert result.current_phase == "implementation"
        assert llm.calls == []


# ---------------------------------------------------------------------------
# 9. Tests: supervisor_node
# ---------------------------------------------------------------------------

class TestSupervisorNodeStage4:
    def test_sets_supervisor_notes(self) -> None:
        llm = MockLLM("Run completed successfully.")
        state = _make_state(gate_evidence=[_passing_evidence("ruff")])
        result = supervisor_node(state, llm=llm)
        assert result.supervisor_notes == "Run completed successfully."

    def test_escalates_on_loop_cap(self) -> None:
        llm = MockLLM("Escalating due to 3 failed loops.")
        state = _make_state(loop_count=3)
        result = supervisor_node(state, llm=llm)
        assert result.current_phase == "human_review"

    def test_escalates_on_critical_risk(self) -> None:
        llm = MockLLM("Critical risk escalation.")
        state = _make_state(risk_level="critical")
        result = supervisor_node(state, llm=llm)
        assert result.current_phase == "human_review"

    def test_does_not_escalate_for_normal_run(self) -> None:
        llm = MockLLM("Clean run. Done.")
        state = _make_state(current_phase="done", gate_evidence=[_passing_evidence("ruff")])
        result = supervisor_node(state, llm=llm)
        assert result.current_phase == "done"

    def test_supervisor_notes_fallback_on_llm_failure(self) -> None:
        class ExplodingLLM:
            def chat(self, messages: list[dict]) -> str:
                raise RuntimeError("timeout")

        state = _make_state()
        result = supervisor_node(state, llm=ExplodingLLM())
        assert result.supervisor_notes is not None
        assert "unavailable" in result.supervisor_notes.lower()


# ---------------------------------------------------------------------------
# 10. Tests: schema additions
# ---------------------------------------------------------------------------

class TestSchemaStage4:
    def test_sdlcstate_has_supervisor_notes(self) -> None:
        state = SDLCState(run_id="x", objective="y")
        assert hasattr(state, "supervisor_notes")
        assert state.supervisor_notes is None

    def test_supervisor_notes_serialises(self) -> None:
        state = SDLCState(run_id="x", objective="y", supervisor_notes="hello")
        dumped = state.model_dump()
        assert dumped["supervisor_notes"] == "hello"


# ---------------------------------------------------------------------------
# 11. End-to-end: full graph with mocked LLM — all gates pass → done
# ---------------------------------------------------------------------------

class TestStage4EndToEnd:
    """Full graph run with all LLMs and tool runners mocked."""

    def _setup_mocks(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        ruff_pass: bool = True,
        pytest_pass: bool = True,
    ) -> None:
        import src.agents.nodes as nodes_mod
        import src.io.workspace as ws

        monkeypatch.setattr(ws, "VOLUMES_DIR", tmp_path)

        monkeypatch.setattr(nodes_mod, "run_ruff", MagicMock(return_value=_passing_evidence("ruff") if ruff_pass else _failing_evidence("ruff")))
        monkeypatch.setattr(nodes_mod, "run_pytest", MagicMock(return_value=_passing_evidence("pytest") if pytest_pass else _failing_evidence("pytest")))
        monkeypatch.setattr(nodes_mod, "run_mypy", MagicMock(return_value=_passing_evidence("mypy")))
        monkeypatch.setattr(nodes_mod, "run_bandit", MagicMock(return_value=_passing_evidence("bandit")))
        monkeypatch.setattr(nodes_mod, "run_pip_audit", MagicMock(return_value=_passing_evidence("pip_audit")))
        monkeypatch.setattr(nodes_mod, "run_complexity_check", MagicMock(return_value=_passing_evidence("radon")))

        tech_lead_reply = json.dumps({
            "requirements": [{"id": "REQ-001", "description": "Add numbers", "acceptance_criteria": ["1+1==2"]}],
            "architecture_doc": "# Arch\nSimple.",
        })
        mock_llm = MockLLM([
            tech_lead_reply,   # tech_lead_node
            "# Requirement: REQ-001\ndef add(a, b): return a + b",  # dev_node
            "# Requirement: REQ-001\ndef test_add(): assert True",  # qa_node
            # review_node only calls LLM on failures — no call if all pass
            "# Release Notes\n\nDone.",   # release_engineer_node
            "Run complete.",              # supervisor_node
        ])
        monkeypatch.setattr(nodes_mod, "load_llm", lambda section="main", **kw: mock_llm)

    def test_all_gates_pass_reaches_done(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        self._setup_mocks(monkeypatch, tmp_path, ruff_pass=True, pytest_pass=True)

        import src.state.persistence as pers
        monkeypatch.setattr(pers, "RUNS_DIR", tmp_path / "runs")

        from src.agents.graph import build_graph
        graph = build_graph()
        initial = SDLCState(
            run_id="e2e-stage4-pass",
            objective="Build a calculator",
        )
        result_dict = graph.invoke(initial.model_dump())
        result = SDLCState.model_validate(result_dict)
        assert result.current_phase == "done"
        assert result.release_notes is not None
        assert result.supervisor_notes is not None

    def test_always_failing_gates_escalate_to_human_review(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import src.agents.nodes as nodes_mod
        import src.io.workspace as ws
        import src.state.persistence as pers

        monkeypatch.setattr(ws, "VOLUMES_DIR", tmp_path)
        monkeypatch.setattr(pers, "RUNS_DIR", tmp_path / "runs")

        monkeypatch.setattr(nodes_mod, "run_ruff", MagicMock(return_value=_failing_evidence("ruff")))
        monkeypatch.setattr(nodes_mod, "run_pytest", MagicMock(return_value=_failing_evidence("pytest")))
        monkeypatch.setattr(nodes_mod, "run_mypy", MagicMock(return_value=_passing_evidence("mypy")))
        monkeypatch.setattr(nodes_mod, "run_bandit", MagicMock(return_value=_passing_evidence("bandit")))
        monkeypatch.setattr(nodes_mod, "run_pip_audit", MagicMock(return_value=_passing_evidence("pip_audit")))
        monkeypatch.setattr(nodes_mod, "run_complexity_check", MagicMock(return_value=_passing_evidence("radon")))

        tech_lead_reply = json.dumps({
            "requirements": [{"id": "REQ-001", "description": "Add numbers", "acceptance_criteria": []}],
            "architecture_doc": "# Arch",
        })
        mock_llm = MockLLM(tech_lead_reply)  # always returns same thing (fallback for any extra calls)
        monkeypatch.setattr(nodes_mod, "load_llm", lambda section="main", **kw: mock_llm)

        from src.agents.graph import build_graph
        graph = build_graph()
        initial = SDLCState(
            run_id="e2e-stage4-fail",
            objective="Build a calculator",
        )
        result_dict = graph.invoke(initial.model_dump())
        result = SDLCState.model_validate(result_dict)
        assert result.current_phase == "human_review"
        assert result.loop_count >= 3
