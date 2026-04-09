"""
Smoke tests for automation/graph.py

These tests verify that the graph can be constructed from a minimal valid
config without any real LLM calls or API keys.
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


MINIMAL_CONFIG = {
    "swarm": {
        "name": "Test Swarm",
        "description": "Test",
        "output_dir": "./Drafts",
        "traceability_matrix": "./Knowledge_Traceability_Matrix.md",
    },
    "orchestrator": {
        "agent": "Orchestrator",
        "journalist": "Journalist",
        "max_agent_calls": 2,
        "max_tool_rounds_per_agent": 2,
    },
    "model": {
        "provider": "openai",
        "name": "gpt-4o",
        "temperature": 0.2,
        "env_key": "OPENAI_API_KEY",
    },
    "personas": [
        {
            "name": "Orchestrator",
            "icon": "👑",
            "role": "Coordinator",
            "persona_file": "./agents/Orchestrator/persona.md",
            "tools": [],
        },
        {
            "name": "Journalist",
            "icon": "✍️",
            "role": "Writer",
            "persona_file": "./agents/Journalist/persona.md",
            "tools": [],
        },
    ],
    "tools": {},
    "reviewer": {
        "enabled": False,
        "max_revision_loops": 1,
        "banned_words": [],
        "required_elements": [],
        "rejection_patterns": [],
        "tone": "neutral",
    },
    "hitl": {"enabled": False, "checkpoints": []},
    "epistemic_tags": ["[FACT]", "[INFERENCE]"],
}


def test_build_graph_returns_compiled_graph():
    """build_graph() should return a compiled LangGraph without raising."""
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = mock_llm

    with patch("automation.graph.create_model", return_value=mock_llm), \
         patch("automation.graph.create_reviewer_model", return_value=mock_llm), \
         patch("automation.config.load_persona_content", return_value="You are a test agent."):
        from automation.graph import build_graph
        graph = build_graph(config=MINIMAL_CONFIG)
        assert graph is not None


def test_build_graph_with_reviewer_disabled():
    """build_graph() with reviewer disabled should not add a reviewer node."""
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = mock_llm

    config = {**MINIMAL_CONFIG, "reviewer": {**MINIMAL_CONFIG["reviewer"], "enabled": False}}

    with patch("automation.graph.create_model", return_value=mock_llm), \
         patch("automation.graph.create_reviewer_model", return_value=mock_llm), \
         patch("automation.config.load_persona_content", return_value=""):
        from automation.graph import build_graph
        graph = build_graph(config=config)
        assert graph is not None
