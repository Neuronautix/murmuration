from __future__ import annotations

from pathlib import Path

from automation.builder.models import PersonaSpec, SwarmSpec, ToolSpec
from automation.config import load_config


def _extract_section(lines: list[str], heading: str) -> list[str]:
    section_header = f"## {heading}"
    try:
        start = lines.index(section_header)
    except ValueError:
        return []

    collected: list[str] = []
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("-   "):
            collected.append(stripped[4:])
        elif stripped.startswith("- "):
            collected.append(stripped[2:])
        else:
            collected.append(stripped)
    return collected


def parse_persona_markdown(
    path: Path,
    fallback_name: str,
    fallback_icon: str,
    fallback_role: str,
    tools: list[str],
) -> PersonaSpec:
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = content.splitlines()

    core_mission_section = _extract_section(lines, "Core Mission")
    core_mission = " ".join(core_mission_section).strip() or f"Core mission for {fallback_name}."

    return PersonaSpec(
        name=fallback_name,
        folder_name=path.parent.name,
        icon=fallback_icon,
        role=fallback_role,
        tools=tools,
        core_mission=core_mission,
        kb_focus=_extract_section(lines, "Knowledge Base (KB) Focus"),
        behavior_rules=_extract_section(lines, "Behavior"),
    )


def load_swarm_spec_from_disk(root_dir: Path) -> SwarmSpec:
    config = load_config(root_dir / "swarm_config.yml")

    personas: list[PersonaSpec] = []
    for persona in config.get("personas", []):
        persona_path = root_dir / persona.get("persona_file", "").replace("./", "")
        personas.append(
            parse_persona_markdown(
                persona_path,
                fallback_name=persona.get("name", "Persona"),
                fallback_icon=persona.get("icon", "🤖"),
                fallback_role=persona.get("role", "Role"),
                tools=persona.get("tools", []),
            )
        )

    reviewer = config.get("reviewer", {})
    reviewer_model = reviewer.get("model", {}) or {}
    tool_registry = {
        name: ToolSpec(**spec)
        for name, spec in config.get("tools", {}).items()
    }

    swarm = config.get("swarm", {})
    model = config.get("model", {})
    orchestrator = config.get("orchestrator", {})
    hitl = config.get("hitl", {})

    return SwarmSpec(
        swarm_name=swarm.get("name", "Swarm"),
        swarm_description=swarm.get("description", "Generated swarm"),
        domain=swarm.get("name", "General Research"),
        output_dir=swarm.get("output_dir", "./Drafts"),
        traceability_matrix=swarm.get("traceability_matrix", "./Knowledge_Traceability_Matrix.md"),
        model_provider=model.get("provider", "openai"),
        model_name=model.get("name", "gpt-4o"),
        model_temperature=model.get("temperature", 0.2),
        model_env_key=model.get("env_key", "OPENAI_API_KEY"),
        personas=personas,
        orchestrator_agent=orchestrator.get("agent", personas[0].name if personas else "Coordinator"),
        journalist_agent=orchestrator.get("journalist", personas[-1].name if personas else "Journalist"),
        max_agent_calls=orchestrator.get("max_agent_calls", 8),
        max_tool_rounds_per_agent=orchestrator.get("max_tool_rounds_per_agent", 5),
        reviewer_enabled=reviewer.get("enabled", True),
        reviewer_max_revision_loops=reviewer.get("max_revision_loops", 3),
        reviewer_banned_words=reviewer.get("banned_words", []),
        reviewer_required_elements=reviewer.get("required_elements", []),
        reviewer_rejection_patterns=reviewer.get("rejection_patterns", []),
        reviewer_tone=reviewer.get("tone", "strictly factual, objective, and neutral"),
        reviewer_model_name=reviewer_model.get("name"),
        reviewer_model_temperature=reviewer_model.get("temperature", 0.7),
        hitl_enabled=hitl.get("enabled", False),
        hitl_checkpoints=hitl.get("checkpoints", []),
        epistemic_tags=config.get("epistemic_tags", ["[FACT]", "[INFERENCE]", "[SPECULATION]"]),
        tool_registry=tool_registry,
    )