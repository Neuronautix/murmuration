"""Offline validation of the ported toxicology (TOCSAS) and FAIR-NAMs example configs."""

import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from automation.builder.doctor import inspect_swarm
from automation.builder.loader import load_swarm_spec_from_disk
from automation.config import get_persona_config, load_config, load_tools_for_persona
from automation.graph import AgentAssignment, OrchestratorDecision, make_orchestrator_node

ROOT = Path(__file__).resolve().parents[1]

PACKS = {
    "toxicology": {
        "personas": 9,
        "orchestrator": "TOCSAS Dr. Nexus",
        "journalist": "TOCSAS Journalist",
        "allowed_warnings": set(),
    },
    "fair_nams": {
        "personas": 5,
        "orchestrator": "Dr. Nexus",
        "journalist": "Journalist",
        # Donor reviewer rules are carried over unchanged; the Journalist has no KB.
        "allowed_warnings": {
            "reviewer is enabled but rejection_patterns is empty; adversarial checks may be too weak",
            "KB directory is empty for 'Journalist'",
        },
    },
}


def _config_path(name: str) -> Path:
    return ROOT / "examples" / name / "swarm_config.yml"


def _staged_root(tmp_path: Path, name: str) -> Path:
    """Lay out the pack as doctor/loader expect: <root>/swarm_config.yml plus repo-relative persona paths."""
    shutil.copy(_config_path(name), tmp_path / "swarm_config.yml")
    shutil.copytree(ROOT / "examples" / name, tmp_path / "examples" / name)
    return tmp_path


@pytest.mark.parametrize("name", sorted(PACKS))
def test_example_pack_configuration_and_tools(name):
    expected = PACKS[name]
    config = load_config(_config_path(name))
    assert len(config["personas"]) == expected["personas"]
    assert config["orchestrator"]["agent"] == expected["orchestrator"]
    assert config["orchestrator"]["journalist"] == expected["journalist"]
    assert config["reviewer"]["enabled"]
    assert config["reviewer"]["required_elements"]
    for persona in config["personas"]:
        assert persona["persona_file"].startswith(f"./examples/{name}/agents/")
        assert get_persona_config(config, persona["name"])["content"]
        assert set(persona["tools"]) <= set(config["tools"])
        assert len(load_tools_for_persona(config, persona)) == len(persona["tools"])


@pytest.mark.parametrize("name", sorted(PACKS))
def test_example_pack_passes_doctor_and_builder_validation(tmp_path, monkeypatch, name):
    root = _staged_root(tmp_path, name)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-placeholder-not-used")
    report = inspect_swarm(root)
    errors = [issue.message for issue in report.issues if issue.severity == "error"]
    warnings = {issue.message for issue in report.issues if issue.severity == "warning"}
    assert errors == []
    assert warnings == PACKS[name]["allowed_warnings"]
    spec = load_swarm_spec_from_disk(root)
    assert spec.orchestrator_agent == PACKS[name]["orchestrator"]
    assert len(spec.personas) == PACKS[name]["personas"]
    assert all(persona.core_mission and persona.behavior_rules for persona in spec.personas)


@pytest.mark.parametrize("name", sorted(PACKS))
def test_example_pack_routing_uses_existing_graph(name):
    config = load_config(_config_path(name))
    specialists = [p["name"] for p in config["personas"]
                   if p["name"] not in {PACKS[name]["orchestrator"], PACKS[name]["journalist"]}]
    model = MagicMock()
    model.invoke.return_value = OrchestratorDecision(
        reasoning="Independent checks", next_agents=specialists[:2], instructions="",
        assignments=[AgentAssignment(agent=s, instructions=f"Check {s}") for s in specialists[:2]],
    )
    with patch("automation.config.load_persona_content", return_value="Pack prompt"):
        node = make_orchestrator_node(config, specialists, model, {"enabled": False})
    state = {"task": "Assess the pack", "agent_outputs": {}, "agent_call_count": 0}
    result = node(state)
    assert result["next_agents"] == specialists[:2]
    assert result["agent_call_count"] == 2
    assert set(result["agent_assignments"]) == set(specialists[:2])
