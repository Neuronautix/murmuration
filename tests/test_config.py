"""
Tests for automation/config.py

These tests use temporary YAML files and do not require API keys.
"""

import pytest
import tempfile
import os
from pathlib import Path


MINIMAL_CONFIG = """
swarm:
  name: "Test Swarm"
  description: "Test"
  output_dir: "./Drafts"
  traceability_matrix: "./Knowledge_Traceability_Matrix.md"

orchestrator:
  agent: "Orchestrator"
  journalist: "Journalist"
  max_agent_calls: 4
  max_tool_rounds_per_agent: 3

model:
  provider: "openai"
  name: "gpt-4o"
  temperature: 0.2
  env_key: "OPENAI_API_KEY"

personas:
  - name: "Orchestrator"
    icon: "👑"
    role: "Test orchestrator"
    persona_file: "./agents/Orchestrator/persona.md"
    tools: []

  - name: "Journalist"
    icon: "✍️"
    role: "Test journalist"
    persona_file: "./agents/Journalist/persona.md"
    tools: []

tools: {}

reviewer:
  enabled: false
  max_revision_loops: 1
  banned_words: []
  required_elements: []
  rejection_patterns: []
  tone: "neutral"

hitl:
  enabled: false
  checkpoints: []

epistemic_tags:
  - "[FACT]"
  - "[INFERENCE]"
"""


@pytest.fixture
def config_file(tmp_path):
    """Write a minimal valid swarm_config.yml to a temp directory."""
    cfg = tmp_path / "swarm_config.yml"
    cfg.write_text(MINIMAL_CONFIG)
    return tmp_path


def test_load_config_returns_dict(config_file):
    """load_config() should return a dict with the expected top-level keys."""
    from automation.config import load_config
    cfg = load_config(config_path=config_file / "swarm_config.yml")
    assert isinstance(cfg, dict)
    assert "swarm" in cfg
    assert "personas" in cfg
    assert "orchestrator" in cfg


def test_load_config_missing_file(tmp_path):
    """load_config() should raise FileNotFoundError when swarm_config.yml is absent."""
    from automation.config import load_config
    with pytest.raises(FileNotFoundError):
        load_config(config_path=tmp_path / "swarm_config.yml")


def test_load_config_swarm_name(config_file):
    """Swarm name from YAML is correctly parsed."""
    from automation.config import load_config
    cfg = load_config(config_path=config_file / "swarm_config.yml")
    assert cfg["swarm"]["name"] == "Test Swarm"


def test_load_config_persona_count(config_file):
    """Config should contain exactly the personas defined in the YAML."""
    from automation.config import load_config
    cfg = load_config(config_path=config_file / "swarm_config.yml")
    assert len(cfg["personas"]) == 2
