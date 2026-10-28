"""
config.py — Configuration Engine for Murmuration

Loads swarm_config.yml and dynamically builds:
  - System prompts (from persona files)
  - Reviewer-2 prompts (from banned_words, required_elements, tone)
  - Tool registries (from module + function references)
  - LLM model instances (from provider + model name)

A user should NEVER need to edit this file. All customization flows
through swarm_config.yml and agents/*/persona.md files.
"""

import contextlib
import importlib
import os
import re
from pathlib import Path
from typing import Any

import jinja2
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

    _validate_orchestrator_options(config.get("orchestrator") or {})

    journalist = config.get("journalist")
    if journalist is not None:
        if not isinstance(journalist, dict):
            raise ValueError("swarm_config.yml: 'journalist' must be a mapping (e.g. journalist: {brief: ...}).")
        brief = journalist.get("brief")
        if brief is not None and not isinstance(brief, str):
            raise ValueError("swarm_config.yml: 'journalist.brief' must be a string (use a YAML block scalar '|').")

    _validate_options(config)
    return config


def _validate_options(config: dict) -> None:
    """Check the optional keys that tune orchestration; raise ValueError naming the key path."""
    swarm = config.get("swarm") or {}
    run_log = swarm.get("run_log")
    if run_log is not None and run_log is not False and not (isinstance(run_log, str) and run_log.strip()):
        raise ValueError(
            f"swarm_config.yml: swarm.run_log must be a file path or false, not {run_log!r}."
        )
    persist = swarm.get("persist_findings")
    if persist is not None and not isinstance(persist, bool):
        raise ValueError(f"swarm_config.yml: swarm.persist_findings must be true or false, not {persist!r}.")

    _validate_checkpointer(config)

    for persona in config.get("personas") or []:
        if not isinstance(persona, dict):
            continue  # malformed entries are reported by doctor / fail in get_persona_config
        _validate_model_override(persona.get("model"), f"personas[{persona.get('name', '?')}].model")


CHECKPOINTER_TYPES = ("none", "memory", "sqlite")


def _validate_checkpointer(config: dict) -> None:
    block = config.get("checkpointer")
    if block is None:
        return
    if not isinstance(block, dict):
        raise ValueError("swarm_config.yml: checkpointer must be a mapping such as {type: sqlite, path: ...}.")
    kind = block.get("type")
    if kind not in CHECKPOINTER_TYPES:
        raise ValueError(
            f"swarm_config.yml: checkpointer.type must be one of {', '.join(CHECKPOINTER_TYPES)}, not {kind!r}."
        )
    path = block.get("path")
    if path is not None and not (isinstance(path, str) and path.strip()):
        raise ValueError("swarm_config.yml: checkpointer.path must be a file path.")
    if path is not None and kind != "sqlite":
        raise ValueError("swarm_config.yml: checkpointer.path is only used with checkpointer.type: sqlite.")
    if kind == "none" and (config.get("hitl") or {}).get("enabled"):
        raise ValueError(
            "swarm_config.yml: checkpointer.type: none cannot be used with hitl.enabled: true; "
            "HITL checkpoints need a checkpointer to pause and resume (use memory or sqlite)."
        )


SUPPORTED_PROVIDERS = ("openai", "anthropic", "google")
# The variable each provider's LangChain client reads its API key from.
PROVIDER_ENV_KEYS = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "google": "GOOGLE_API_KEY"}
MODEL_OVERRIDE_KEYS = ("provider", "name", "temperature", "env_key")


def _validate_model_override(override, path: str) -> None:
    """A per-persona model block: a mapping of provider/name/temperature/env_key (all optional)."""
    if override is None:
        return
    if not isinstance(override, dict):
        raise ValueError(f"swarm_config.yml: {path} must be a mapping such as {{name: gpt-4o-mini}}.")
    unknown = sorted(set(override) - set(MODEL_OVERRIDE_KEYS))
    if unknown:
        raise ValueError(
            f"swarm_config.yml: {path}.{unknown[0]} is not supported; use {', '.join(MODEL_OVERRIDE_KEYS)}."
        )
    if "provider" in override and override["provider"] not in SUPPORTED_PROVIDERS:
        raise ValueError(
            f"swarm_config.yml: {path}.provider must be one of {', '.join(SUPPORTED_PROVIDERS)}, "
            f"not {override['provider']!r}."
        )
    for key in ("name", "env_key"):
        if key in override and not (isinstance(override[key], str) and override[key].strip()):
            raise ValueError(f"swarm_config.yml: {path}.{key} must be a non-empty string.")
    temperature = override.get("temperature")
    if "temperature" in override and (
        isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not 0 <= temperature <= 2
    ):
        raise ValueError(f"swarm_config.yml: {path}.temperature must be a number between 0 and 2.")


def _validate_orchestrator_options(orchestrator: dict) -> None:
    """Reject orchestrator options whose type or value the graph cannot use."""
    failure_policy = orchestrator.get("on_agent_failure", "continue")
    if failure_policy not in ("continue", "abort"):
        raise ValueError(
            f"swarm_config.yml: orchestrator.on_agent_failure must be 'continue' or 'abort', "
            f"not {failure_policy!r}."
        )
    allow_tools = orchestrator.get("allow_tools", False)
    if not isinstance(allow_tools, bool):
        raise ValueError(
            f"swarm_config.yml: orchestrator.allow_tools must be true or false, not {allow_tools!r}."
        )
    early_stop = orchestrator.get("early_stop")
    if early_stop is not None:
        if not isinstance(early_stop, dict):
            raise ValueError("swarm_config.yml: orchestrator.early_stop must be a mapping {enabled, min_rounds}.")
        unknown = sorted(set(early_stop) - {"enabled", "min_rounds"})
        if unknown:
            raise ValueError(f"swarm_config.yml: orchestrator.early_stop.{unknown[0]} is not supported; "
                             "use enabled and min_rounds.")
        if not isinstance(early_stop.get("enabled", False), bool):
            raise ValueError("swarm_config.yml: orchestrator.early_stop.enabled must be true or false.")
        min_rounds = early_stop.get("min_rounds", 1)
        if isinstance(min_rounds, bool) or not isinstance(min_rounds, int) or min_rounds < 1:
            raise ValueError(
                f"swarm_config.yml: orchestrator.early_stop.min_rounds must be a whole number of at least 1, "
                f"not {min_rounds!r}."
            )
    max_parallel = orchestrator.get("max_parallel_agents")
    if max_parallel is not None and (isinstance(max_parallel, bool) or not isinstance(max_parallel, int)
                                     or max_parallel < 1):
        raise ValueError(
            f"swarm_config.yml: orchestrator.max_parallel_agents must be a whole number of at least 1 "
            f"or null (no limit), not {max_parallel!r}."
        )


# RunnableConfig metadata key carrying the calling persona's name from a graph node
# to its tools (read by append_traceability_matrix for the matrix's Author/Agent column).
AGENT_METADATA_KEY = "murmuration_agent"


SWARM_PATH_DEFAULTS = {
    "output_dir": "./Drafts",
    "traceability_matrix": "./Knowledge_Traceability_Matrix.md",
}


def resolve_swarm_path(config: dict | None, key: str) -> Path:
    """Resolve a ``swarm.output_dir`` / ``swarm.traceability_matrix`` setting.

    Relative paths are resolved against the project root so the CLI and the
    agent tools agree on the same location regardless of the working directory.
    Pass ``config=None`` to load ``swarm_config.yml``; if it cannot be loaded,
    the documented default is used.
    """
    if config is None:
        try:
            config = load_config()
        except (FileNotFoundError, ValueError, yaml.YAMLError):
            config = {}
    raw = (config.get("swarm") or {}).get(key) or SWARM_PATH_DEFAULTS[key]
    return _resolve_project_path(raw)


def _resolve_project_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def get_run_log_path(config: dict) -> Path | None:
    """Resolve ``swarm.run_log``; None when the run log is off (the default)."""
    raw = (config.get("swarm") or {}).get("run_log")
    return _resolve_project_path(raw) if raw else None


def findings_slug(name: str) -> str:
    """File stem for a persona's persisted findings (Unicode letters kept, lower case)."""
    return re.sub(r"[^\w]+|_+", "-", name).strip("-").lower() or "agent"


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



# Default instructions appended to the Journalist's task message. Rendered with
# Jinja2; see build_journalist_brief() for the available variables. Override it
# with `journalist.brief` in swarm_config.yml — never by editing this file.
DEFAULT_JOURNALIST_BRIEF = """\
Write a complete structured output. You MUST include:
{% for item in required_elements %}
  • {{ item }}
{% else %}
  • Formal in-text citations [1], [2]…
  • A limitations section
  • A complete formal References section at the end
{% endfor %}
{% if tool_names %}
Your tools: {{ tool_names | join(', ') }}. Save the finished output to disk with your
writing tool before you finish, then create a version snapshot if you have a snapshot tool.
{% endif %}
"""


def build_journalist_brief(config: dict, tool_names: list | None = None) -> str:
    """Render the Journalist's task brief from ``journalist.brief`` (or the default).

    The template is Jinja2 and sees every top-level key of swarm_config.yml
    (``swarm``, ``reviewer``, ``epistemic_tags``, ``output_sections`` …) plus:

      required_elements  reviewer.required_elements (list of str)
      tool_names         names of the tools bound to the Journalist (list of str)
      journalist_name    the Journalist persona's name

    Undefined variables are an error so a typo in the template fails at build
    time (and in ``swarm doctor``) rather than silently producing an empty brief.
    """
    template_src = (config.get("journalist") or {}).get("brief") or DEFAULT_JOURNALIST_BRIEF
    env = jinja2.Environment(undefined=jinja2.StrictUndefined, trim_blocks=True, lstrip_blocks=True,
                             keep_trailing_newline=False, autoescape=False)
    context = dict(config)
    context.update({
        "required_elements": list((config.get("reviewer") or {}).get("required_elements") or []),
        "tool_names": list(tool_names or []),
        "journalist_name": (config.get("orchestrator") or {}).get("journalist", "Journalist"),
    })
    try:
        rendered = env.from_string(template_src).render(**context)
    except jinja2.TemplateError as e:
        raise ValueError(f"swarm_config.yml: journalist.brief failed to render: {e}") from e
    rendered = rendered.strip()
    if not rendered:
        raise ValueError("swarm_config.yml: journalist.brief rendered to an empty string.")
    return rendered


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


def build_agent_system_prompt(persona_cfg: dict, epistemic_tags: list = None,
                              tool_names: list = None) -> str:
    """Build a focused system prompt for a single agent node.

    Injects only this agent's persona content — not all agents — keeping
    per-call token cost proportional to one persona rather than the full swarm.

    ``tool_names`` are the names of the tools actually bound to this agent. When
    given, the traceability-logging instruction is only emitted if the agent has
    a tool that writes to the matrix; otherwise the agent would be told to call
    a tool it cannot see.
    """
    name = persona_cfg.get("name", "Agent")
    icon = persona_cfg.get("icon", "🤖")
    role = persona_cfg.get("role", "")
    content = persona_cfg.get("content", "")

    prompt = f"# {icon} You are {name}\n**Role**: {role}\n\n{content}"

    if epistemic_tags:
        tags_str = ", ".join(epistemic_tags)
        prompt += f"\n\nEPISTEMIC TAGS: Classify every claim with one of: {tags_str}\n"
        if tool_names is None or "append_traceability_matrix" in tool_names:
            prompt += "After every literature or web search, call `append_traceability_matrix` immediately.\n"
        prompt += "Include formal in-text citations in all outputs."

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


def persona_model_config(config: dict, persona_cfg: dict) -> dict | None:
    """The model spec for one persona: ``personas[].model`` merged over ``model``.

    Returns None when the persona has no override, so callers can share the base
    model instance. Works like ``reviewer.model``: only the keys that differ need
    to be given.
    """
    override = persona_cfg.get("model")
    if not override:
        return None
    merged = {**config["model"], **override}
    # A provider switch without its own env_key checks that provider's standard
    # variable, not the base model's (which belongs to another provider).
    if "env_key" not in override and merged.get("provider") != config["model"].get("provider", "openai"):
        merged["env_key"] = PROVIDER_ENV_KEYS.get(merged.get("provider"), merged.get("env_key"))
    return merged


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


def get_checkpointer_config(config: dict) -> dict:
    """Resolve the top-level ``checkpointer`` block to ``{"type", "path"}``.

    Default: ``memory`` when HITL is on (interrupts need one), otherwise ``none``,
    which is the behaviour before the key existed. ``sqlite`` persists every step
    to ``path`` (default ``./.murmuration/checkpoints.sqlite``) so a crashed or
    paused run can be resumed from another process with ``--resume``. The default
    is deliberately outside ``output_dir``: git_commit_snapshot force-adds that
    folder, and the database is binary and holds the full run state.
    """
    block = config.get("checkpointer") or {}
    kind = block.get("type") or ("memory" if (config.get("hitl") or {}).get("enabled") else "none")
    path = None
    if kind == "sqlite":
        raw = block.get("path")
        path = _resolve_project_path(raw or DEFAULT_CHECKPOINT_PATH)
    return {"type": kind, "path": path}


DEFAULT_CHECKPOINT_PATH = "./.murmuration/checkpoints.sqlite"
SQLITE_EXTRA_HINT = "pip install 'murmuration[persist]'  (installs langgraph-checkpoint-sqlite)"


def sqlite_saver_class():
    """Import the async SQLite saver from the optional ``persist`` extra."""
    try:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    except ImportError as e:
        raise RuntimeError(
            f"checkpointer.type: sqlite needs the optional 'persist' extra: {SQLITE_EXTRA_HINT}"
        ) from e
    return AsyncSqliteSaver


@contextlib.asynccontextmanager
async def open_checkpointer(config: dict):
    """Yield the checkpointer configured by ``checkpointer`` (None for type none).

    The SQLite saver holds an aiosqlite connection bound to the running event
    loop, so it must be opened inside it, around graph compilation and the run.
    """
    settings = get_checkpointer_config(config)
    if settings["type"] == "memory":
        from langgraph.checkpoint.memory import MemorySaver
        yield MemorySaver()
    elif settings["type"] == "sqlite":
        saver_class = sqlite_saver_class()
        settings["path"].parent.mkdir(parents=True, exist_ok=True)
        async with saver_class.from_conn_string(str(settings["path"])) as saver:
            yield saver
    else:
        yield None


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

    # Personas with their own model (personas[].model) may need a different key.
    for persona in config.get("personas", []):
        merged = persona_model_config(config, persona)
        if merged is None:
            continue
        persona_key = merged.get("env_key", "OPENAI_API_KEY")
        if not os.getenv(persona_key):
            raise EnvironmentError(
                f"Environment variable '{persona_key}' is not set; persona '{persona.get('name')}' "
                f"uses it for its own model (personas[].model).\n"
                f"Add it to .env, or set personas[].model.env_key."
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
