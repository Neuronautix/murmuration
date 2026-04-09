"""
config.py — Configuration Engine for the Research Swarm

Loads swarm_config.yml and dynamically builds:
  - System prompts (from persona files)
  - Reviewer-2 prompts (from banned_words, required_elements, tone)
  - Tool registries (from module + function references)
  - LLM model instances (from provider + model name)

A user should NEVER need to edit this file. All customization flows
through swarm_config.yml and agents/*/persona.md files.
"""

import os
import importlib
from pathlib import Path
from typing import Any

import yaml
from rich.console import Console

console = Console()

# Resolve the project root (one level above automation/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "swarm_config.yml"


def load_config(config_path: Path = CONFIG_PATH) -> dict:
    """Load and validate the swarm configuration from YAML."""
    if not config_path.exists():
        raise FileNotFoundError(
            f"Configuration file not found: {config_path}\n"
            f"Please create a swarm_config.yml in the project root.\n"
            f"See QUICKSTART.md for instructions."
        )

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # Validate required top-level keys
    required_keys = ["swarm", "orchestrator", "model", "personas", "tools", "reviewer"]
    missing = [k for k in required_keys if k not in config]
    if missing:
        raise ValueError(
            f"swarm_config.yml is missing required keys: {missing}\n"
            f"Please refer to the example configuration."
        )

    return config


def load_persona_content(persona_file: str) -> str:
    """Read a persona markdown file from disk.

    Resolves paths relative to the project root.
    """
    path = PROJECT_ROOT / persona_file
    if not path.exists():
        console.print(f"[yellow]Warning: Persona file not found: {path}[/yellow]")
        return ""
    return path.read_text(encoding="utf-8")


def build_reviewer_prompt(config: dict) -> str:
    """Dynamically construct the Reviewer-2 prompt from config.

    If the config provides a `custom_prompt`, that is used verbatim.
    Otherwise, the prompt is assembled from `banned_words`,
    `required_elements`, and `tone`.
    """
    r = config["reviewer"]

    # Allow full override
    if "custom_prompt" in r and r["custom_prompt"]:
        return r["custom_prompt"]

    banned = ", ".join(f"'{w}'" for w in r.get("banned_words", []))
    required_items = r.get("required_elements", [])
    required = " AND ".join(required_items)
    tone = r.get("tone", "objective and neutral")

    rejection_patterns = r.get("rejection_patterns", [])
    rejection_clause = ""
    if rejection_patterns:
        patterns_str = "; ".join(f"'{p}'" for p in rejection_patterns)
        rejection_clause = (
            f" Additionally, REJECT if the text contains any of these prohibited patterns: "
            f"{patterns_str}."
        )

    return (
        f"You are Reviewer-2 (Adversarial Critic). You read the agent's final text output. "
        f"If they use hype words ({banned}), OR if they fail to maintain a {tone} tone, "
        f"you MUST reject it. Additionally, if the text does NOT include {required}, "
        f"you MUST reject it.{rejection_clause} "
        f"To reject, reply starting exactly with 'REJECTED: ' and list the "
        f"exact flaws prohibiting publication. "
        f"If it is perfectly factual, properly cited, and contains all required elements, "
        f"reply exactly with 'APPROVED'."
    )



def get_persona_config(config: dict, persona_name: str) -> dict:
    """Return the full config dict for a single persona, with persona.md content loaded.

    Raises ValueError if the persona is not found in swarm_config.yml.
    """
    for persona in config["personas"]:
        if persona["name"] == persona_name:
            p = dict(persona)
            p["content"] = load_persona_content(persona.get("persona_file", ""))
            return p
    raise ValueError(
        f"Persona '{persona_name}' not found in swarm_config.yml. "
        f"Available: {[p['name'] for p in config['personas']]}"
    )


def load_tools_for_persona(config: dict, persona_cfg: dict) -> list:
    """Load and return only the tools assigned to a specific persona.

    Respects the per-persona 'tools' list in swarm_config.yml instead of
    returning every registered tool (as load_tools() does).
    """
    allowed_keys = set(persona_cfg.get("tools", []))
    tool_functions = []
    seen = set()

    for tool_name, tool_spec in config["tools"].items():
        if tool_name not in allowed_keys:
            continue
        module_path = tool_spec["module"]
        function_name = tool_spec["function"]
        key = f"{module_path}.{function_name}"
        if key in seen:
            continue
        seen.add(key)
        try:
            module = importlib.import_module(module_path)
            func = getattr(module, function_name)
            tool_functions.append(func)
        except (ModuleNotFoundError, AttributeError) as e:
            console.print(
                f"[red]Error loading tool '{tool_name}' for persona "
                f"'{persona_cfg.get('name', '?')}': {e}[/red]"
            )

    return tool_functions


def build_agent_system_prompt(persona_cfg: dict, epistemic_tags: list = None) -> str:
    """Build a focused system prompt for a single agent node.

    Injects only this agent's persona content — not all agents — keeping
    per-call token cost proportional to one persona rather than the full swarm.
    """
    name = persona_cfg.get("name", "Agent")
    icon = persona_cfg.get("icon", "🤖")
    role = persona_cfg.get("role", "")
    content = persona_cfg.get("content", "")

    prompt = f"# {icon} You are {name}\n**Role**: {role}\n\n{content}"

    if epistemic_tags:
        tags_str = ", ".join(epistemic_tags)
        prompt += (
            f"\n\nEPISTEMIC TAGS: Classify every claim with one of: {tags_str}\n"
            "After every literature or web search, call `append_traceability_matrix` immediately.\n"
            "Include formal in-text citations in all outputs."
        )

    return prompt


def create_model(config: dict) -> Any:
    """Create an LLM instance from the config's model specification.

    Supports multiple providers: openai, anthropic, google.
    """
    provider = config["model"].get("provider", "openai")
    model_name = config["model"]["name"]
    temperature = config["model"].get("temperature", 0.2)

    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model_name, temperature=temperature)

    elif provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=model_name, temperature=temperature)

    elif provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=model_name, temperature=temperature)

    else:
        raise ValueError(
            f"Unsupported model provider: '{provider}'. "
            f"Supported providers: openai, anthropic, google. "
            f"Edit the 'model.provider' key in swarm_config.yml."
        )


def create_reviewer_model(config: dict) -> Any:
    """Create an LLM instance for Reviewer-2, with optional model/temperature override.

    If reviewer.model is defined in swarm_config.yml, those values are merged on top
    of the main model config — allowing a higher temperature or a different model for
    genuinely adversarial review rather than the same instance used by the agents.

    Example swarm_config.yml entry::

        reviewer:
          model:
            name: "gpt-4o"
            temperature: 0.8   # higher = more adversarial

    If reviewer.model is absent, falls back to create_model(config).
    """
    reviewer_model_overrides = config.get("reviewer", {}).get("model")
    if not reviewer_model_overrides:
        return create_model(config)

    # Merge overrides onto the base model config
    merged_model_cfg = {**config["model"], **reviewer_model_overrides}
    reviewer_config = {**config, "model": merged_model_cfg}
    return create_model(reviewer_config)


def get_hitl_config(config: dict) -> dict:
    """Return the HITL configuration block, with safe defaults.

    When hitl.enabled is false (or the section is absent), returns a dict
    that disables all checkpoints so node factories can branch on it without
    needing to guard against missing keys.
    """
    hitl = config.get("hitl", {})
    return {
        "enabled": hitl.get("enabled", False),
        "checkpoints": hitl.get("checkpoints", []),
    }


def validate_env(config: dict) -> list[str]:
    """Validate that required environment variables are present.

    Returns a list of warning messages for missing optional keys.
    Does NOT raise for optional keys — only for the primary model key.
    """
    warnings = []

    # Check primary model key
    required_key = config["model"].get("env_key", "OPENAI_API_KEY")
    if not os.getenv(required_key):
        raise EnvironmentError(
            f"Required environment variable '{required_key}' is not set.\n"
            f"Copy .env.example to .env and add your API key."
        )

    # Check tool-specific optional keys — map function name → env var.
    # Collect tool names per missing key to emit one consolidated warning each.
    tool_env_keys = {
        "search_you_engine": "YOU_API_KEY",
        "you_research": "YOU_API_KEY",
        "scrape_webpage": "YOU_API_KEY",
    }
    missing_key_tools: dict[str, list[str]] = {}
    for tool_name, tool_spec in config["tools"].items():
        func_name = tool_spec.get("function", "")
        if func_name in tool_env_keys:
            env_key = tool_env_keys[func_name]
            if not os.getenv(env_key):
                missing_key_tools.setdefault(env_key, []).append(tool_name)

    for env_key, tool_names in missing_key_tools.items():
        warnings.append(
            f"'{env_key}' is not set — tools {tool_names} will fail at runtime if called."
        )

    return warnings
