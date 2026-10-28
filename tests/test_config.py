"""
Tests for automation/config.py

These tests use temporary YAML files and do not require API keys.
"""


import pytest

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
    cfg.write_text(MINIMAL_CONFIG, encoding="utf-8")
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


# ── Validation of the orchestration options (docs/dev/ORCHESTRATION_AUDIT.md) ──


@pytest.mark.parametrize("key, value, ok", [
    ("run_log", True, False), ("run_log", "", False), ("run_log", 0, False), ("persist_findings", "yes", False),
    ("run_log", None, True), ("run_log", False, True), ("persist_findings", None, True),
])
def test_load_config_validates_observability_keys(tmp_path, key, value, ok):
    import yaml

    from automation.config import CONFIG_PATH, load_config
    real = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    real["swarm"][key] = value
    (tmp_path / "swarm_config.yml").write_text(yaml.safe_dump(real), encoding="utf-8")
    if ok:
        load_config(tmp_path / "swarm_config.yml")
    else:
        with pytest.raises(ValueError, match=f"swarm.{key}"):
            load_config(tmp_path / "swarm_config.yml")


def test_load_config_rejects_non_bool_allow_tools(tmp_path):
    import yaml

    from automation.config import CONFIG_PATH, load_config
    real = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    real["orchestrator"]["allow_tools"] = "yes"
    (tmp_path / "swarm_config.yml").write_text(yaml.safe_dump(real), encoding="utf-8")
    with pytest.raises(ValueError, match="orchestrator.allow_tools"):
        load_config(tmp_path / "swarm_config.yml")
    real["orchestrator"]["allow_tools"] = True
    (tmp_path / "swarm_config.yml").write_text(yaml.safe_dump(real), encoding="utf-8")
    assert load_config(tmp_path / "swarm_config.yml")["orchestrator"]["allow_tools"] is True


@pytest.mark.parametrize("override, message", [
    ("gpt-4o-mini", r"personas\[Specialist1\]\.model must be a mapping"),
    ({"name": "x", "max_tokens": 5}, r"personas\[Specialist1\]\.model\.max_tokens is not supported"),
    ({"provider": "mistral"}, r"personas\[Specialist1\]\.model\.provider"),
    ({"temperature": 3}, r"personas\[Specialist1\]\.model\.temperature"),
    ({"name": ""}, r"personas\[Specialist1\]\.model\.name"),
])
def test_load_config_rejects_bad_persona_models(tmp_path, override, message):
    import yaml

    from automation.config import CONFIG_PATH, load_config
    real = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    real["personas"][1]["model"] = override
    (tmp_path / "swarm_config.yml").write_text(yaml.safe_dump(real), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_config(tmp_path / "swarm_config.yml")
    real["personas"][1]["model"] = {"provider": "anthropic", "name": "claude-x", "env_key": "ANTHROPIC_API_KEY"}
    (tmp_path / "swarm_config.yml").write_text(yaml.safe_dump(real), encoding="utf-8")
    load_config(tmp_path / "swarm_config.yml")


@pytest.mark.parametrize("value", [0, -1, 1.5, True, "2"])
def test_load_config_rejects_bad_max_parallel_agents(tmp_path, value):
    import yaml

    from automation.config import CONFIG_PATH, load_config
    real = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    real["orchestrator"]["max_parallel_agents"] = value
    (tmp_path / "swarm_config.yml").write_text(yaml.safe_dump(real), encoding="utf-8")
    with pytest.raises(ValueError, match="orchestrator.max_parallel_agents"):
        load_config(tmp_path / "swarm_config.yml")


@pytest.mark.parametrize("block, hitl, message", [
    ("sqlite", False, "checkpointer must be a mapping"),
    ({"type": "redis"}, False, "checkpointer.type must be one of"),
    ({"type": "sqlite", "path": ""}, False, "checkpointer.path must be a file path"),
    ({"type": "memory", "path": "x.sqlite"}, False, "checkpointer.path is only used with"),
    ({"type": "none"}, True, "checkpointer.type: none cannot be used with hitl.enabled"),
])
def test_load_config_rejects_bad_checkpointer(tmp_path, block, hitl, message):
    import yaml

    from automation.config import CONFIG_PATH, load_config
    real = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    real["checkpointer"] = block
    real["hitl"]["enabled"] = hitl
    (tmp_path / "swarm_config.yml").write_text(yaml.safe_dump(real), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_config(tmp_path / "swarm_config.yml")


@pytest.mark.parametrize("value, message", [
    (True, "orchestrator.early_stop must be a mapping"),
    ({"enabled": "yes"}, "orchestrator.early_stop.enabled"),
    ({"enabled": True, "min_rounds": 0}, "orchestrator.early_stop.min_rounds"),
    ({"enabled": True, "rounds": 2}, "orchestrator.early_stop.rounds is not supported"),
])
def test_load_config_rejects_bad_early_stop(tmp_path, value, message):
    import yaml

    from automation.config import CONFIG_PATH, load_config
    real = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    real["orchestrator"]["early_stop"] = value
    (tmp_path / "swarm_config.yml").write_text(yaml.safe_dump(real), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_config(tmp_path / "swarm_config.yml")


@pytest.mark.parametrize("key, value, message", [
    ("on_rejection", "specialists", "reviewer.on_rejection"),
    ("on_max_revisions", "approve", "reviewer.on_max_revisions"),
    ("max_evidence_loops", -1, "reviewer.max_evidence_loops"),
    ("matrix_excerpt_rows", 0, "reviewer.matrix_excerpt_rows"),
    ("context", ["draft", "web"], "reviewer.context"),
    ("context", ["task"], "reviewer.context must include draft"),
])
def test_load_config_rejects_bad_reviewer_options(tmp_path, key, value, message):
    import yaml

    from automation.config import CONFIG_PATH, load_config
    real = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    real["reviewer"][key] = value
    (tmp_path / "swarm_config.yml").write_text(yaml.safe_dump(real), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_config(tmp_path / "swarm_config.yml")


@pytest.mark.parametrize("block, message", [
    (True, "collaboration must be a mapping"),
    ({"structured_findings": "yes"}, "collaboration.structured_findings must be true or false"),
    ({"debate": True}, "collaboration.debate is not supported"),
    ({"surface_disagreements": True}, "collaboration.surface_disagreements needs collaboration.structured_findings"),
    ({"structured_findings": False, "critique_round": True}, "collaboration.critique_round needs"),
])
def test_load_config_rejects_bad_collaboration(tmp_path, block, message):
    import yaml

    from automation.config import CONFIG_PATH, load_config
    real = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    real["collaboration"] = block
    (tmp_path / "swarm_config.yml").write_text(yaml.safe_dump(real), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_config(tmp_path / "swarm_config.yml")


def test_load_config_rejects_personas_whose_claim_ids_would_collide(tmp_path):
    import yaml

    from automation.config import CONFIG_PATH, load_config
    real = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    real["collaboration"] = {"structured_findings": True}
    real["personas"][1]["name"], real["personas"][3]["name"] = "Lit_Scout", "lit scout"
    (tmp_path / "swarm_config.yml").write_text(yaml.safe_dump(real), encoding="utf-8")
    with pytest.raises(ValueError, match="share the claim-id prefix 'lit-scout'"):
        load_config(tmp_path / "swarm_config.yml")
