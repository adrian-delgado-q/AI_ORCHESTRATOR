"""Stage 4 tests — LLM agents and Diagnostic Utility (mock LLMs, no API keys)."""
from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

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

# MockLLM is imported from conftest via pytest's plugin mechanism
from tests.conftest import MockLLM  # noqa: F401 — used in type hints only


# ---------------------------------------------------------------------------
# 1. src/core/llm.py
# ---------------------------------------------------------------------------


class TestLLMModule:
    def test_litellm_backend_implements_protocol(self):
        assert isinstance(LiteLLMBackend(model="x/y", api_key="k"), BaseLLM)

    def test_stub_llm_implements_protocol(self):
        assert isinstance(StubLLM(), BaseLLM)

    def test_load_llm_returns_stub_when_no_api_key(self, tmp_path):
        import yaml
        cfg = {"main": {"model": "deepseek/deepseek-chat", "api_key_env": "TOTALLY_MISSING_KEY_XYZ"}}
        cfg_path = tmp_path / "llm.yaml"
        cfg_path.write_text(yaml.dump(cfg))
        assert isinstance(load_llm("main", config_path=cfg_path), StubLLM)

    def test_load_llm_returns_litellm_when_key_set(self, tmp_path, monkeypatch):
        import yaml
        monkeypatch.setenv("MY_TEST_KEY", "sk-test-1234")
        cfg = {"main": {"model": "deepseek/deepseek-chat", "api_key_env": "MY_TEST_KEY"}}
        cfg_path = tmp_path / "llm.yaml"
        cfg_path.write_text(yaml.dump(cfg))
        assert isinstance(load_llm("main", config_path=cfg_path), LiteLLMBackend)

    def test_load_llm_missing_section_raises(self, tmp_path):
        import yaml
        cfg_path = tmp_path / "llm.yaml"
        cfg_path.write_text(yaml.dump({"main": {"model": "x/y"}}))
        with pytest.raises(KeyError, match="diagnostic"):
            load_llm("diagnostic", config_path=cfg_path)

    def test_load_llm_reads_temperature_and_max_tokens(self, tmp_path, monkeypatch):
        import yaml
        monkeypatch.setenv("MY_TEST_KEY2", "sk-real-key")
        cfg = {"main": {"model": "x/y", "api_key_env": "MY_TEST_KEY2", "temperature": 0.7, "max_tokens": 512}}
        cfg_path = tmp_path / "llm.yaml"
        cfg_path.write_text(yaml.dump(cfg))
        llm = load_llm("main", config_path=cfg_path)
        assert isinstance(llm, LiteLLMBackend)
        assert llm.temperature == 0.7
        assert llm.max_tokens == 512

    def test_stub_llm_tech_lead_returns_valid_json(self):
        stub = StubLLM()
        reply = stub.chat([
            {"role": "system", "content": "You are a principal engineer. Return JSON with requirements and architecture_doc."},
            {"role": "user", "content": "Project objective: build a calculator"},
        ])
        parsed = json.loads(reply)
        assert "requirements" in parsed
        assert "architecture_doc" in parsed

    def test_stub_llm_dev_returns_python_with_tag(self):
        stub = StubLLM()
        reply = stub.chat([
            {"role": "system", "content": "You are an expert Python developer. Write production-ready Python code."},
            {"role": "user", "content": "Requirement ID: REQ-042\nDescription: test"},
        ])
        assert "# Requirement: REQ-042" in reply
        assert "def stub_req_042" in reply

    def test_stub_llm_qa_returns_test_code(self):
        stub = StubLLM()
        reply = stub.chat([
            {"role": "system", "content": "You are a senior QA engineer writing pytest test files."},
            {"role": "user", "content": "Requirement ID: REQ-007\nDescription: thing"},
        ])
        assert "# Requirement: REQ-007" in reply
        assert "def test_" in reply

    def test_stub_llm_diagnostic_returns_string(self):
        stub = StubLLM()
        reply = stub.chat([
            {"role": "system", "content": "You are a diagnostic assistant. Provide a concise fix instruction."},
            {"role": "user", "content": "Tool: ruff\nFindings: E501 line too long"},
        ])
        assert isinstance(reply, str)

    def test_stub_llm_unknown_prompt_returns_fallback(self):
        stub = StubLLM()
        reply = stub.chat([
            {"role": "system", "content": "You are a completely unknown agent type."},
            {"role": "user", "content": "Do something unknown"},
        ])
        assert isinstance(reply, str)
        assert len(reply) > 0


# ---------------------------------------------------------------------------
# 2. src/agents/diagnostic.py
# ---------------------------------------------------------------------------


class TestDiagnosticUtility:
    def test_diagnose_failing_returns_nonempty(self, failing_evidence):
        from tests.conftest import MockLLM
        llm = MockLLM("Fix the import on line 5.")
        du = DiagnosticUtility(llm)
        result = du.diagnose(failing_evidence("ruff", "E401 multiple imports on line 5"))
        assert result == "Fix the import on line 5."
        assert len(llm.calls) == 1

    def test_diagnose_passing_returns_empty(self, passing_evidence):
        from tests.conftest import MockLLM
        llm = MockLLM("Should not be called")
        du = DiagnosticUtility(llm)
        result = du.diagnose(passing_evidence("ruff"))
        assert result == ""
        assert len(llm.calls) == 0

    def test_diagnose_prompt_contains_tool_name_and_findings(self, failing_evidence):
        from tests.conftest import MockLLM
        llm = MockLLM("fix it")
        du = DiagnosticUtility(llm)
        du.diagnose(failing_evidence("pytest", "FAILED test_foo.py::test_bar"))
        user_msg = llm.calls[0][1]["content"]
        assert "pytest" in user_msg
        assert "FAILED test_foo.py::test_bar" in user_msg


# ---------------------------------------------------------------------------
# 3. Helper functions in nodes.py
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_extract_json_fenced(self):
        assert _extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'

    def test_extract_json_unfenced_object(self):
        assert _extract_json('Here is the result: {"b": 2}') == '{"b": 2}'

    def test_extract_json_unfenced_array(self):
        assert _extract_json("Result: [1, 2, 3]") == "[1, 2, 3]"

    def test_extract_json_no_json_returns_input(self):
        assert _extract_json("no json here") == "no json here"

    def test_diagnosis_context_empty_when_no_failures(self, passing_evidence):
        evidence = [passing_evidence("ruff"), passing_evidence("pytest")]
        assert _diagnosis_context(evidence) == ""

    def test_diagnosis_context_empty_when_failures_have_no_diagnosis(self, failing_evidence):
        assert _diagnosis_context([failing_evidence("ruff")]) == ""

    def test_diagnosis_context_includes_diagnosis(self, failing_evidence):
        ev = failing_evidence("ruff")
        ev.diagnosis = "Fix imports"
        result = _diagnosis_context([ev])
        assert "ruff" in result
        assert "Fix imports" in result


# ---------------------------------------------------------------------------
# 4. tech_lead_node
# ---------------------------------------------------------------------------


class TestTechLeadNode:
    def test_generates_requirements_from_llm_json(self, make_state):
        from tests.conftest import MockLLM
        req_json = json.dumps({
            "requirements": [
                {"id": "REQ-001", "description": "Add numbers", "acceptance_criteria": ["1+1==2"]},
                {"id": "REQ-002", "description": "Subtract numbers", "acceptance_criteria": ["3-1==2"]},
            ],
            "architecture_doc": "# Arch\nSimple CLI tool.",
        })
        llm = MockLLM(req_json)
        result = tech_lead_node(make_state(requirements=[]), llm=llm)
        assert len(result.requirements) == 2
        assert result.requirements[0].id == "REQ-001"
        assert result.architecture_doc == "# Arch\nSimple CLI tool."
        assert result.current_phase == "stack_resolution"

    def test_falls_back_to_stub_req_on_invalid_json(self, make_state):
        from tests.conftest import MockLLM
        result = tech_lead_node(make_state(requirements=[]), llm=MockLLM("not valid json"))
        assert len(result.requirements) == 1
        assert result.requirements[0].id == "REQ-001"

    def test_risk_level_high_for_security_keyword(self, make_state):
        from tests.conftest import MockLLM
        req_json = json.dumps({
            "requirements": [{"id": "REQ-001", "description": "Auth", "acceptance_criteria": []}],
            "architecture_doc": "arch",
        })
        result = tech_lead_node(
            make_state(requirements=[], objective="Implement auth system"),
            llm=MockLLM(req_json),
        )
        assert result.risk_level == "high"

    def test_lessons_learned_included_in_prompt(self, make_state):
        from tests.conftest import MockLLM
        req_json = json.dumps({
            "requirements": [{"id": "REQ-001", "description": "x", "acceptance_criteria": []}],
            "architecture_doc": "arch",
        })
        llm = MockLLM(req_json)
        tech_lead_node(make_state(requirements=[], lessons_learned=["Avoid global state"]), llm=llm)
        assert "Avoid global state" in llm.calls[0][0]["content"]


# ---------------------------------------------------------------------------
# 5. dev_node
# ---------------------------------------------------------------------------


class TestDevNode:
    def test_writes_file_with_requirement_tag(self, tmp_dirs, make_state):
        from tests.conftest import MockLLM
        import src.io.workspace as ws  # already patched by tmp_dirs
        code = "def add(a: int, b: int) -> int:\n    return a + b\n"
        llm = MockLLM(code)
        result = dev_node(make_state(), llm=llm)
        assert len(result.files_changed) == 1
        written = (tmp_dirs / "volumes" / "test-run-001" / "src" / "req_001_impl.py").read_text()
        assert "# Requirement: REQ-001" in written

    def test_prepends_tag_if_missing(self, tmp_dirs, make_state):
        from tests.conftest import MockLLM
        dev_node(make_state(), llm=MockLLM("def add(a, b):\n    return a + b\n"))
        written = (tmp_dirs / "volumes" / "test-run-001" / "src" / "req_001_impl.py").read_text()
        assert written.startswith("# Requirement: REQ-001")

    def test_injects_diagnosis_context_in_prompt(self, tmp_dirs, make_state, failing_evidence):
        from tests.conftest import MockLLM
        llm = MockLLM("# Requirement: REQ-001\npass")
        ev = failing_evidence("ruff")
        ev.diagnosis = "Remove unused import on line 3"
        dev_node(make_state(gate_evidence=[ev]), llm=llm)
        assert "Remove unused import on line 3" in llm.calls[0][1]["content"]

    def test_phase_set_to_testing(self, tmp_dirs, make_state):
        from tests.conftest import MockLLM
        result = dev_node(make_state(), llm=MockLLM("# Requirement: REQ-001\npass"))
        assert result.current_phase == "testing"

    def test_parallel_generation_preserves_requirement_order(self, tmp_dirs, monkeypatch):
        import time
        import src.io.workspace as ws  # already patched by tmp_dirs
        from src.state.schema import SDLCState, SDLCRequirement

        class ReqAwareLLM:
            def chat(self, messages):
                content = messages[1]["content"]
                req_id = "REQ-002" if "REQ-002" in content else "REQ-001"
                if req_id == "REQ-001":
                    time.sleep(0.03)
                return f"# Requirement: {req_id}\ndef impl_{req_id.lower().replace('-', '_')}(): pass\n"

        monkeypatch.setenv("OMEGA_LLM_CONCURRENCY", "2")
        state = SDLCState(
            run_id="test-run-001",
            objective="order test",
            requirements=[
                SDLCRequirement(id="REQ-001", description="First", acceptance_criteria=[]),
                SDLCRequirement(id="REQ-002", description="Second", acceptance_criteria=[]),
            ],
        )
        result = dev_node(state, llm=ReqAwareLLM())
        assert [fc.requirement_id for fc in result.files_changed] == ["REQ-001", "REQ-002"]


# ---------------------------------------------------------------------------
# 6. qa_node
# ---------------------------------------------------------------------------


class TestQaNode:
    def test_writes_test_file_with_tag(self, tmp_dirs, make_state):
        from tests.conftest import MockLLM
        test_code = textwrap.dedent("""\
            import sys
            from pathlib import Path
            sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))
            from req_001_impl import add

            def test_add():
                assert add(1, 2) == 3
        """)
        result = qa_node(make_state(), llm=MockLLM(test_code))
        assert len(result.tests_written) == 1
        written = (tmp_dirs / "volumes" / "test-run-001" / "tests" / "test_req_001.py").read_text()
        assert "# Requirement: REQ-001" in written
        assert "def test_" in written

    def test_phase_set_to_review(self, tmp_dirs, make_state):
        from tests.conftest import MockLLM
        result = qa_node(make_state(), llm=MockLLM("# Requirement: REQ-001\ndef test_x(): pass"))
        assert result.current_phase == "review"

    def test_parallel_generation_preserves_requirement_order(self, tmp_dirs, monkeypatch):
        import time
        from src.state.schema import SDLCState, SDLCRequirement

        class ReqAwareLLM:
            def chat(self, messages):
                content = messages[1]["content"]
                req_id = "REQ-002" if "REQ-002" in content else "REQ-001"
                if req_id == "REQ-001":
                    time.sleep(0.03)
                return f"# Requirement: {req_id}\ndef test_{req_id.lower().replace('-', '_')}(): pass\n"

        monkeypatch.setenv("OMEGA_LLM_CONCURRENCY", "2")
        state = SDLCState(
            run_id="test-run-001",
            objective="order test",
            requirements=[
                SDLCRequirement(id="REQ-001", description="First", acceptance_criteria=[]),
                SDLCRequirement(id="REQ-002", description="Second", acceptance_criteria=[]),
            ],
        )
        result = qa_node(state, llm=ReqAwareLLM())
        assert [fc.requirement_id for fc in result.tests_written] == ["REQ-001", "REQ-002"]


# ---------------------------------------------------------------------------
# 7. review_node — diagnosis integration
# ---------------------------------------------------------------------------


class TestReviewNodeStage4:
    def test_diagnosis_called_only_on_failures(self, make_state, patch_all_tools, failing_evidence):
        from tests.conftest import MockLLM
        mocks = patch_all_tools(overrides={"run_ruff": failing_evidence("ruff", "E101")})
        diag_llm = MockLLM("Fix E101 by sorting imports.")
        result = review_node(make_state(), llm=diag_llm)
        ruff_ev = next(e for e in result.gate_evidence if e.tool_name == "ruff")
        assert ruff_ev.diagnosis == "Fix E101 by sorting imports."
        for ev in result.gate_evidence:
            if ev.passed:
                assert ev.diagnosis is None

    def test_diagnosis_not_called_when_all_pass(self, make_state, patch_all_tools):
        from tests.conftest import MockLLM
        patch_all_tools()
        diag_llm = MockLLM("should not be called")
        review_node(make_state(), llm=diag_llm)
        assert diag_llm.calls == []

    def test_diagnosis_exception_sets_fallback_message(self, make_state, patch_all_tools, failing_evidence):
        class ExplodingLLM:
            def chat(self, messages):
                raise RuntimeError("API down")

        mocks = patch_all_tools(overrides={"run_ruff": failing_evidence("ruff")})
        result = review_node(make_state(), llm=ExplodingLLM())
        ruff_ev = next(e for e in result.gate_evidence if e.tool_name == "ruff")
        assert ruff_ev.diagnosis is not None
        assert "Diagnosis unavailable" in ruff_ev.diagnosis


# ---------------------------------------------------------------------------
# 8. release_engineer_node
# ---------------------------------------------------------------------------


class TestReleaseEngineerNodeStage4:
    def test_generates_release_notes_via_llm(self, make_state, passing_evidence):
        from tests.conftest import MockLLM
        state = make_state(gate_evidence=[passing_evidence("ruff"), passing_evidence("pytest")])
        result = release_engineer_node(state, llm=MockLLM("# Release Notes\n\nAll good."))
        assert result.release_notes == "# Release Notes\n\nAll good."
        assert result.current_phase == "done"

    def test_uses_fallback_when_llm_fails(self, make_state, passing_evidence):
        class ExplodingLLM:
            def chat(self, messages):
                raise RuntimeError("network error")

        state = make_state(gate_evidence=[passing_evidence("ruff"), passing_evidence("pytest")])
        result = release_engineer_node(state, llm=ExplodingLLM())
        assert result.release_notes is not None
        assert state.run_id in result.release_notes
        assert result.current_phase == "done"

    def test_blocks_when_required_gate_fails(self, make_state, failing_evidence, passing_evidence):
        from tests.conftest import MockLLM
        state = make_state(gate_evidence=[failing_evidence("ruff"), passing_evidence("pytest")])
        llm = MockLLM("should not be called")
        result = release_engineer_node(state, llm=llm)
        assert result.current_phase == "implementation_planning"
        assert llm.calls == []


# ---------------------------------------------------------------------------
# 9. supervisor_node
# ---------------------------------------------------------------------------


class TestSupervisorNodeStage4:
    def test_sets_supervisor_notes(self, make_state, passing_evidence):
        from tests.conftest import MockLLM
        result = supervisor_node(
            make_state(gate_evidence=[passing_evidence("ruff")]),
            llm=MockLLM("Run completed successfully."),
        )
        assert result.supervisor_notes == "Run completed successfully."

    def test_escalates_on_loop_cap(self, make_state):
        from tests.conftest import MockLLM
        result = supervisor_node(make_state(loop_count=3), llm=MockLLM("Escalating."))
        assert result.current_phase == "human_review"

    def test_escalates_on_critical_risk(self, make_state):
        from tests.conftest import MockLLM
        result = supervisor_node(make_state(risk_level="critical"), llm=MockLLM("Critical."))
        assert result.current_phase == "human_review"

    def test_does_not_escalate_for_normal_run(self, make_state, passing_evidence):
        from tests.conftest import MockLLM
        result = supervisor_node(
            make_state(current_phase="done", gate_evidence=[passing_evidence("ruff")]),
            llm=MockLLM("Clean run. Done."),
        )
        assert result.current_phase == "done"

    def test_supervisor_notes_fallback_on_llm_failure(self, make_state):
        class ExplodingLLM:
            def chat(self, messages):
                raise RuntimeError("timeout")

        result = supervisor_node(make_state(), llm=ExplodingLLM())
        assert result.supervisor_notes is not None
        assert "unavailable" in result.supervisor_notes.lower()


# ---------------------------------------------------------------------------
# 10. Schema additions
# ---------------------------------------------------------------------------


class TestSchemaStage4:
    def test_sdlcstate_has_supervisor_notes(self):
        state = SDLCState(run_id="x", objective="y")
        assert hasattr(state, "supervisor_notes")
        assert state.supervisor_notes is None

    def test_supervisor_notes_serialises(self):
        state = SDLCState(run_id="x", objective="y", supervisor_notes="hello")
        assert state.model_dump()["supervisor_notes"] == "hello"


# ---------------------------------------------------------------------------
# 11. End-to-end: full graph with mocked LLM
# ---------------------------------------------------------------------------


class TestStage4EndToEnd:
    def test_all_gates_pass_reaches_done(self, tmp_dirs, monkeypatch, patch_all_tools):
        from tests.conftest import MockLLM
        import src.agents.nodes as nodes_mod

        patch_all_tools()

        tech_lead_reply = json.dumps({
            "requirements": [{"id": "REQ-001", "description": "Add numbers", "acceptance_criteria": ["1+1==2"]}],
            "architecture_doc": "# Arch",
        })
        mock_llm = MockLLM([
            tech_lead_reply,
            "# Requirement: REQ-001\ndef add(a, b): return a + b",
            "# Requirement: REQ-001\ndef test_add(): assert True",
            "# Release Notes\n\nDone.",
            "Run complete.",
        ])
        monkeypatch.setattr(nodes_mod, "load_llm", lambda section="main", **kw: mock_llm)

        from src.agents.graph import build_graph
        result = SDLCState.model_validate(
            build_graph().invoke(SDLCState(run_id="e2e-stage4-pass", objective="Build a calculator").model_dump())
        )
        assert result.current_phase == "done"
        assert result.release_notes is not None
        assert result.supervisor_notes is not None

    def test_always_failing_gates_escalate_to_human_review(self, tmp_dirs, monkeypatch, patch_all_tools):
        from tests.conftest import MockLLM
        import src.agents.nodes as nodes_mod

        patch_all_tools(all_pass=False)

        tech_lead_reply = json.dumps({
            "requirements": [{"id": "REQ-001", "description": "x", "acceptance_criteria": []}],
            "architecture_doc": "# Arch",
        })
        monkeypatch.setattr(nodes_mod, "load_llm", lambda section="main", **kw: MockLLM(tech_lead_reply))

        from src.agents.graph import build_graph
        result = SDLCState.model_validate(
            build_graph().invoke(SDLCState(run_id="e2e-stage4-fail", objective="Build a calculator").model_dump())
        )
        assert result.current_phase == "human_review"
        assert result.loop_count >= 3
