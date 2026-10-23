from pathlib import Path
from unittest.mock import MagicMock, patch

from automation.config import get_persona_config, load_config, load_tools_for_persona
from automation.graph import AgentAssignment, OrchestratorDecision, make_orchestrator_node

PACK = Path(__file__).resolve().parents[1] / "examples/hcm/swarm_config.yml"


def test_hcm_pack_configuration_and_tools():
    config = load_config(PACK)
    assert len(config["personas"]) == 8
    assert config["orchestrator"]["agent"] == "Dr. Nexus (HCMSAS)"
    assert config["orchestrator"]["journalist"] == "HCMSAS Journalist"
    assert config["reviewer"]["enabled"]
    assert "pre_flight" in config["hitl"]["checkpoints"]
    for persona in config["personas"]:
        assert get_persona_config(config, persona["name"])["content"]
        assert len(load_tools_for_persona(config, persona)) == len(persona["tools"])


def test_hcm_routing_uses_existing_graph_and_named_journalist():
    config = load_config(PACK)
    specialists = [p["name"] for p in config["personas"][1:-1]]
    model = MagicMock()
    model.invoke.return_value = OrchestratorDecision(
        reasoning="Independent checks", next_agents=specialists[:2], instructions="",
        assignments=[AgentAssignment(agent=name, instructions=f"Check {name}") for name in specialists[:2]],
    )
    with patch("automation.config.load_persona_content", return_value="HCM prompt"):
        node = make_orchestrator_node(config, specialists, model, {"enabled": False})
    state = {"task": "Assess HCM", "agent_outputs": {}, "agent_call_count": 0}
    result = node(state)
    assert result["next_agents"] == specialists[:2]
    assert result["agent_call_count"] == 2
    assert set(result["agent_assignments"]) == set(specialists[:2])
    model.invoke.return_value = OrchestratorDecision(
        reasoning="Sufficient evidence", next_agents=["HCMSAS Journalist"], instructions="Draft"
    )
    assert node(state)["next_agents"] == ["Journalist"]
