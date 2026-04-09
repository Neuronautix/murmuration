from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
import typer

try:
    import questionary
except ImportError:  # pragma: no cover - fallback path
    questionary = None

from automation.builder.models import SwarmSpec, ToolSpec
from automation.builder.templates import (
    ARCHETYPE_SPECS,
    AVAILABLE_ARCHETYPES,
    AVAILABLE_BLUEPRINTS,
    BUILTIN_TOOL_REGISTRY,
    build_persona_from_archetype,
    build_starter_swarm_spec,
    build_swarm_spec_from_blueprint,
)

console = Console()


def _q_text(message: str, default: str = "") -> str:
    if questionary is not None:
        return questionary.text(message, default=default).ask() or default
    return typer.prompt(message, default=default)


def _q_confirm(message: str, default: bool = True) -> bool:
    if questionary is not None:
        value = questionary.confirm(message, default=default).ask()
        return default if value is None else bool(value)
    return typer.confirm(message, default=default)


def _q_select(message: str, choices: list[str], default: str | None = None) -> str:
    if questionary is not None:
        return questionary.select(message, choices=choices, default=default).ask() or (default or choices[0])
    while True:
        rendered_choices = ", ".join(choices)
        value = typer.prompt(f"{message} ({rendered_choices})", default=default or choices[0]).strip()
        if value in choices:
            return value
        typer.secho(f"Unknown value '{value}'.", fg=typer.colors.RED)


def _q_checkbox(message: str, choices: list[str], default: list[str] | None = None) -> list[str]:
    if questionary is not None:
        selected = questionary.checkbox(message, choices=choices, default=default or []).ask()
        return selected or list(default or [])
    tool_csv = typer.prompt(message, default=", ".join(default or choices))
    return [item.strip() for item in tool_csv.split(",") if item.strip()]


def get_available_tool_registry(spec: SwarmSpec) -> dict[str, ToolSpec]:
    registry = dict(BUILTIN_TOOL_REGISTRY)
    registry.update(spec.tool_registry)
    return registry


def build_persona_interactively(
    domain: str,
    existing_persona: Any | None = None,
):
    if existing_persona is None:
        archetype = _prompt_archetype()
        default_name = archetype.replace("-", " ").title().replace(" ", "")
        name = _q_text("Persona name", default=default_name)
        role_default = ARCHETYPE_SPECS[archetype]["role"]
        role = _q_text("Role label", default=role_default)
        persona = build_persona_from_archetype(archetype, domain, name=name, role=role)
    else:
        persona = existing_persona.model_copy(deep=True)
        name = _q_text("Persona name", default=persona.name)
        role = _q_text("Role label", default=persona.role)
        icon = _q_text("Icon", default=persona.icon)
        persona.name = name
        persona.role = role
        persona.icon = icon

    if existing_persona is None or _q_confirm("Edit tool list?", default=existing_persona is None):
        persona.tools = _q_checkbox(
            "Select persona tools",
            list(BUILTIN_TOOL_REGISTRY.keys()),
            default=persona.tools,
        )

    persona.core_mission = _q_text("Core mission", default=persona.core_mission)
    persona.domain_focus = _q_checkbox(
        "Domain focus items (comma-separated fallback in plain mode)",
        persona.domain_focus or [f"{domain}: key question"],
        default=persona.domain_focus or [f"{domain}: key question"],
    )
    persona.kb_focus = _q_checkbox(
        "KB focus items (comma-separated fallback in plain mode)",
        persona.kb_focus or [f"{domain}: reference materials"],
        default=persona.kb_focus or [f"{domain}: reference materials"],
    )
    persona.behavior_rules = _q_checkbox(
        "Behavior rules (comma-separated fallback in plain mode)",
        persona.behavior_rules or ["state clear evidence limits"],
        default=persona.behavior_rules or ["state clear evidence limits"],
    )
    return persona


def preview_swarm_spec(spec: SwarmSpec) -> None:
    console.print(Panel.fit(
        f"[bold]{spec.swarm_name}[/bold]\n{spec.swarm_description}\n\n"
        f"Domain: {spec.domain}\n"
        f"Model: {spec.model_provider}/{spec.model_name}\n"
        f"Orchestrator: {spec.orchestrator_agent}\n"
        f"Journalist: {spec.journalist_agent}",
        title="Swarm Preview",
    ))

    table = Table(title="Personas")
    table.add_column("Name")
    table.add_column("Role")
    table.add_column("Tools")
    for persona in spec.personas:
        table.add_row(persona.name, persona.role, ", ".join(persona.tools))
    console.print(table)


def _prompt_archetype() -> str:
    return _q_select("Persona archetype", AVAILABLE_ARCHETYPES, default="domain-specialist")


def build_swarm_spec_interactively(
    domain: str | None = None,
    swarm_name: str | None = None,
    description: str | None = None,
    blueprint: str | None = None,
) -> SwarmSpec:
    domain_value = domain or _q_text("Target domain", default="General Research")
    swarm_name_value = swarm_name or _q_text("Swarm name", default=f"{domain_value} Swarm")
    description_value = description or _q_text(
        "Swarm description",
        default=f"Autonomous multi-agent research swarm for {domain_value}",
    )
    model_provider = _q_select("Model provider", ["openai", "anthropic", "google"], default="openai")
    model_name_default = {
        "openai": "gpt-4o",
        "anthropic": "claude-3-7-sonnet-latest",
        "google": "gemini-2.5-pro",
    }.get(model_provider, "gpt-4o")
    model_name = _q_text("Model name", default=model_name_default)
    model_env_key = _q_text("Model env key", default={
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "google": "GOOGLE_API_KEY",
    }.get(model_provider, "OPENAI_API_KEY"))

    use_starter = _q_confirm("Use the recommended starter team?", default=True)
    if use_starter:
        selected_blueprint = blueprint or _q_select(
            "Starter blueprint",
            AVAILABLE_BLUEPRINTS,
            default="research-core",
        )
        spec = build_swarm_spec_from_blueprint(
            blueprint=selected_blueprint,
            domain=domain_value,
            swarm_name=swarm_name_value,
            swarm_description=description_value,
            model_provider=model_provider,
            model_name=model_name,
            model_env_key=model_env_key,
        )
        preview_swarm_spec(spec)
        return spec

    orchestrator_name = _q_text("Orchestrator persona name", default="Coordinator")
    journalist_name = _q_text("Final writer persona name", default="Journalist")
    specialist_count = int(_q_text("Number of specialist personas", default="3"))

    personas = [
        build_persona_from_archetype("orchestrator", domain_value, name=orchestrator_name),
        build_persona_from_archetype("journalist", domain_value, name=journalist_name),
    ]

    for index in range(1, specialist_count + 1):
        typer.secho(f"\nSpecialist {index}", fg=typer.colors.CYAN)
        persona = build_persona_interactively(domain_value)
        personas.append(persona)

    reviewer_enabled = _q_confirm("Enable Reviewer-2?", default=True)
    hitl_enabled = _q_confirm("Enable HITL checkpoints?", default=False)
    max_agent_calls = int(_q_text("Max specialist dispatches", default="8"))
    max_tool_rounds = int(_q_text("Max tool rounds per specialist", default="5"))

    spec = SwarmSpec(
        swarm_name=swarm_name_value,
        swarm_description=description_value,
        domain=domain_value,
        model_provider=model_provider,
        model_name=model_name,
        model_env_key=model_env_key,
        personas=personas,
        orchestrator_agent=orchestrator_name,
        journalist_agent=journalist_name,
        max_agent_calls=max_agent_calls,
        max_tool_rounds_per_agent=max_tool_rounds,
        reviewer_enabled=reviewer_enabled,
        reviewer_banned_words=[
            "groundbreaking",
            "revolutionary",
            "game-changing",
            "paradigm-shifting",
            "unprecedented",
        ],
        reviewer_required_elements=[
            "clear task framing",
            "a short limitations or uncertainty section",
            "source-aware in-text citations when claims rely on external evidence",
            "a final References or Sources section",
        ],
        reviewer_rejection_patterns=[
            "any unsupported factual claim presented with certainty",
        ],
        hitl_enabled=hitl_enabled,
        tool_registry=BUILTIN_TOOL_REGISTRY,
    )
    preview_swarm_spec(spec)
    return spec


def configure_team_interactively(
    spec: SwarmSpec,
    orchestrator_name: str | None = None,
    journalist_name: str | None = None,
    max_agent_calls: int | None = None,
    max_tool_rounds: int | None = None,
    hitl_mode: str | None = None,
    hitl_checkpoints: list[str] | None = None,
) -> SwarmSpec:
    updated = spec.model_copy(deep=True)
    persona_names = [persona.name for persona in updated.personas]
    checkpoint_choices = ["pre_flight", "post_plan", "pre_journalist", "on_rejection"]

    updated.orchestrator_agent = orchestrator_name or _q_select(
        "Select orchestrator persona",
        persona_names,
        default=updated.orchestrator_agent,
    )
    journalist_default = updated.journalist_agent if updated.journalist_agent in persona_names else persona_names[0]
    updated.journalist_agent = journalist_name or _q_select(
        "Select journalist persona",
        persona_names,
        default=journalist_default,
    )
    updated.max_agent_calls = max_agent_calls if max_agent_calls is not None else int(
        _q_text("Max specialist dispatches", default=str(updated.max_agent_calls))
    )
    updated.max_tool_rounds_per_agent = max_tool_rounds if max_tool_rounds is not None else int(
        _q_text("Max tool rounds per specialist", default=str(updated.max_tool_rounds_per_agent))
    )

    chosen_hitl_mode = hitl_mode or _q_select(
        "HITL mode",
        ["keep", "enable", "disable"],
        default="enable" if updated.hitl_enabled else "disable",
    )
    if chosen_hitl_mode == "enable":
        updated.hitl_enabled = True
    elif chosen_hitl_mode == "disable":
        updated.hitl_enabled = False

    if updated.hitl_enabled:
        updated.hitl_checkpoints = hitl_checkpoints or _q_checkbox(
            "Select HITL checkpoints",
            checkpoint_choices,
            default=updated.hitl_checkpoints or checkpoint_choices,
        )
    elif chosen_hitl_mode != "keep":
        updated.hitl_checkpoints = []

    return updated


def configure_reviewer_interactively(
    spec: SwarmSpec,
    reviewer_mode: str | None = None,
    max_revision_loops: int | None = None,
    tone: str | None = None,
    banned_words: list[str] | None = None,
    required_elements: list[str] | None = None,
    rejection_patterns: list[str] | None = None,
    reviewer_model_name: str | None = None,
    reviewer_model_temperature: float | None = None,
) -> SwarmSpec:
    updated = spec.model_copy(deep=True)

    chosen_mode = reviewer_mode or _q_select(
        "Reviewer mode",
        ["keep", "enable", "disable"],
        default="enable" if updated.reviewer_enabled else "disable",
    )
    if chosen_mode == "enable":
        updated.reviewer_enabled = True
    elif chosen_mode == "disable":
        updated.reviewer_enabled = False

    if updated.reviewer_enabled:
        updated.reviewer_max_revision_loops = max_revision_loops if max_revision_loops is not None else int(
            _q_text("Reviewer max revision loops", default=str(updated.reviewer_max_revision_loops))
        )
        updated.reviewer_tone = tone or _q_text("Reviewer tone", default=updated.reviewer_tone)
        updated.reviewer_banned_words = banned_words or _q_checkbox(
            "Reviewer banned words",
            updated.reviewer_banned_words or ["groundbreaking", "revolutionary"],
            default=updated.reviewer_banned_words,
        )
        updated.reviewer_required_elements = required_elements or _q_checkbox(
            "Reviewer required elements",
            updated.reviewer_required_elements or ["clear task framing"],
            default=updated.reviewer_required_elements,
        )
        updated.reviewer_rejection_patterns = rejection_patterns or _q_checkbox(
            "Reviewer rejection patterns",
            updated.reviewer_rejection_patterns or ["any unsupported factual claim presented with certainty"],
            default=updated.reviewer_rejection_patterns,
        )

        model_name_value = reviewer_model_name
        if model_name_value is None:
            model_name_value = _q_text(
                "Reviewer model name (blank to inherit primary model)",
                default=updated.reviewer_model_name or "",
            )
        updated.reviewer_model_name = model_name_value or None

        if updated.reviewer_model_name:
            updated.reviewer_model_temperature = reviewer_model_temperature if reviewer_model_temperature is not None else float(
                _q_text(
                    "Reviewer model temperature",
                    default=str(updated.reviewer_model_temperature or 0.7),
                )
            )
        else:
            updated.reviewer_model_temperature = reviewer_model_temperature

    return updated


def configure_model_interactively(
    spec: SwarmSpec,
    provider: str | None = None,
    model_name: str | None = None,
    temperature: float | None = None,
    env_key: str | None = None,
) -> SwarmSpec:
    updated = spec.model_copy(deep=True)
    updated.model_provider = provider or _q_select(
        "Model provider",
        ["openai", "anthropic", "google"],
        default=updated.model_provider,
    )
    model_name_default = {
        "openai": "gpt-4o",
        "anthropic": "claude-3-7-sonnet-latest",
        "google": "gemini-2.5-pro",
    }.get(updated.model_provider, updated.model_name)
    updated.model_name = model_name or _q_text("Model name", default=updated.model_name or model_name_default)
    updated.model_temperature = temperature if temperature is not None else float(
        _q_text("Model temperature", default=str(updated.model_temperature))
    )
    updated.model_env_key = env_key or _q_text(
        "Model env key",
        default=updated.model_env_key or {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "google": "GOOGLE_API_KEY",
        }.get(updated.model_provider, "OPENAI_API_KEY"),
    )
    return updated


def configure_metadata_interactively(
    spec: SwarmSpec,
    swarm_name: str | None = None,
    swarm_description: str | None = None,
    domain: str | None = None,
    output_dir: str | None = None,
    traceability_matrix: str | None = None,
    epistemic_tags: list[str] | None = None,
) -> SwarmSpec:
    updated = spec.model_copy(deep=True)
    updated.swarm_name = swarm_name or _q_text("Swarm name", default=updated.swarm_name)
    updated.swarm_description = swarm_description or _q_text(
        "Swarm description",
        default=updated.swarm_description,
    )
    updated.domain = domain or _q_text("Domain label", default=updated.domain)
    updated.output_dir = output_dir or _q_text("Output directory", default=updated.output_dir)
    updated.traceability_matrix = traceability_matrix or _q_text(
        "Traceability matrix path",
        default=updated.traceability_matrix,
    )
    updated.epistemic_tags = epistemic_tags or _q_checkbox(
        "Epistemic tags",
        updated.epistemic_tags or ["[FACT]", "[INFERENCE]", "[SPECULATION]"],
        default=updated.epistemic_tags,
    )
    return updated


def configure_tools_interactively(
    spec: SwarmSpec,
    enabled_tools: list[str] | None = None,
) -> SwarmSpec:
    updated = spec.model_copy(deep=True)
    available_registry = get_available_tool_registry(updated)
    selected_tools = enabled_tools or _q_checkbox(
        "Select tools to keep in the active registry",
        list(available_registry.keys()),
        default=list(updated.tool_registry.keys()),
    )
    selected_set = set(selected_tools)
    updated.tool_registry = {
        name: available_registry[name]
        for name in selected_tools
        if name in available_registry
    }

    for persona in updated.personas:
        persona.tools = [tool for tool in persona.tools if tool in selected_set]
        if not persona.tools:
            raise ValueError(
                f"tool selection leaves persona '{persona.name}' with no tools; keep at least one compatible tool"
            )

    return updated


def build_tool_spec_interactively(
    spec: SwarmSpec,
    tool_key: str | None = None,
    existing_spec: ToolSpec | None = None,
    builtin_key: str | None = None,
    module: str | None = None,
    function: str | None = None,
    description: str | None = None,
) -> tuple[str, ToolSpec]:
    available_registry = get_available_tool_registry(spec)

    if builtin_key:
        if builtin_key not in BUILTIN_TOOL_REGISTRY:
            raise ValueError(f"unknown built-in tool '{builtin_key}'")
        key = tool_key or builtin_key
        return key, BUILTIN_TOOL_REGISTRY[builtin_key].model_copy(deep=True)

    current_key = tool_key or ""
    current_spec = existing_spec.model_copy(deep=True) if existing_spec else None
    if current_spec is None and current_key in available_registry:
        current_spec = available_registry[current_key].model_copy(deep=True)

    if not current_key:
        mode = _q_select("Tool source", ["builtin", "custom"], default="builtin")
        if mode == "builtin":
            builtin_choice = _q_select(
                "Select built-in tool",
                list(BUILTIN_TOOL_REGISTRY.keys()),
                default=list(BUILTIN_TOOL_REGISTRY.keys())[0],
            )
            return builtin_choice, BUILTIN_TOOL_REGISTRY[builtin_choice].model_copy(deep=True)

    key = current_key or _q_text("Tool key", default=current_key or "custom_tool")
    spec_value = current_spec or ToolSpec(module="automation.tools", function="custom_tool", description="Custom tool")
    built_module = module or _q_text("Tool module", default=spec_value.module)
    built_function = function or _q_text("Tool function", default=spec_value.function)
    built_description = description or _q_text("Tool description", default=spec_value.description)
    return key, ToolSpec(module=built_module, function=built_function, description=built_description)


def upsert_tool_in_spec(spec: SwarmSpec, tool_key: str, tool_spec: ToolSpec) -> SwarmSpec:
    updated = spec.model_copy(deep=True)
    updated.tool_registry[tool_key] = tool_spec
    return updated


def remove_tool_from_spec(spec: SwarmSpec, tool_key: str) -> SwarmSpec:
    updated = spec.model_copy(deep=True)
    if tool_key not in updated.tool_registry:
        raise ValueError(f"tool '{tool_key}' not found in active registry")

    del updated.tool_registry[tool_key]
    for persona in updated.personas:
        persona.tools = [tool for tool in persona.tools if tool != tool_key]
        if not persona.tools:
            raise ValueError(
                f"removing '{tool_key}' leaves persona '{persona.name}' with no tools"
            )
    return updated