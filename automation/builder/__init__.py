"""Builder package for interactive swarm creation and validation."""

from automation.builder.blueprints import (
    apply_blueprint_overrides,
    default_blueprint_path,
    export_swarm_blueprint,
    load_swarm_blueprint,
)
from automation.builder.doctor import SwarmDoctorReport, apply_safe_fixes, inspect_swarm, preview_existing_swarm
from automation.builder.generator import GenerationResult, generate_swarm_project, preview_generation_diff
from automation.builder.loader import load_swarm_spec_from_disk
from automation.builder.models import PersonaSpec, SwarmSpec, ToolSpec
from automation.builder.templates import (
    AVAILABLE_ARCHETYPES,
    BUILTIN_TOOL_REGISTRY,
    build_persona_from_archetype,
    build_starter_swarm_spec,
)

__all__ = [
    "AVAILABLE_ARCHETYPES",
    "BUILTIN_TOOL_REGISTRY",
    "GenerationResult",
    "PersonaSpec",
    "SwarmDoctorReport",
    "SwarmSpec",
    "ToolSpec",
    "apply_blueprint_overrides",
    "apply_safe_fixes",
    "build_persona_from_archetype",
    "build_starter_swarm_spec",
    "default_blueprint_path",
    "export_swarm_blueprint",
    "generate_swarm_project",
    "inspect_swarm",
    "load_swarm_blueprint",
    "load_swarm_spec_from_disk",
    "preview_existing_swarm",
    "preview_generation_diff",
]