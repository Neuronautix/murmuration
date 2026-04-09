from __future__ import annotations

import importlib
import os
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from automation.config import load_config

console = Console()

DISCOVERY_TOOLS = {
    "search_literature",
    "search_preprints",
    "search_web",
    "trace_literature_network",
    "search_kb",
    "scrape_page",
    "lookup_doi",
    "you_research",
}

WRITING_TOOLS = {
    "write_section",
}

REQUIRED_PERSONA_SECTIONS = {
    "## Core Mission",
    "## Knowledge Base (KB) Focus",
    "## Behavior",
}


def _normalize_role(value: str) -> str:
    return " ".join(value.lower().split())


def _has_any_tool(persona: dict, tool_names: set[str]) -> bool:
    return any(tool in tool_names for tool in persona.get("tools", []))


def _check_role_collisions(report: SwarmDoctorReport, personas: list[dict]) -> None:
    role_map: dict[str, list[str]] = {}
    for persona in personas:
        role = _normalize_role(persona.get("role", ""))
        if not role:
            continue
        role_map.setdefault(role, []).append(persona.get("name", ""))

    for role, names in role_map.items():
        unique_names = [name for name in names if name]
        if len(unique_names) > 1:
            report.issues.append(
                ValidationIssue(
                    "warning",
                    f"duplicate persona role '{role}' assigned to: {', '.join(unique_names)}",
                    "swarm_config.yml",
                )
            )


def _check_routing_semantics(
    report: SwarmDoctorReport,
    personas: list[dict],
    orchestrator_name: str,
    journalist_name: str,
    max_agent_calls: int,
    max_tool_rounds: int,
) -> None:
    persona_map = {persona.get("name", ""): persona for persona in personas}
    specialists = [
        persona for persona in personas
        if persona.get("name") not in {orchestrator_name, journalist_name}
    ]

    if orchestrator_name == journalist_name and orchestrator_name:
        report.issues.append(
            ValidationIssue(
                "error",
                "orchestrator and journalist cannot be the same persona",
                "swarm_config.yml",
            )
        )

    if max_agent_calls < 1:
        report.issues.append(
            ValidationIssue("error", "max_agent_calls must be at least 1", "swarm_config.yml")
        )
    elif specialists and max_agent_calls < len(specialists):
        report.issues.append(
            ValidationIssue(
                "warning",
                f"max_agent_calls ({max_agent_calls}) is lower than specialist count ({len(specialists)}); some specialists may never be routed in one run",
                "swarm_config.yml",
            )
        )

    if max_tool_rounds < 1:
        report.issues.append(
            ValidationIssue("error", "max_tool_rounds_per_agent must be at least 1", "swarm_config.yml")
        )

    orchestrator = persona_map.get(orchestrator_name)
    journalist = persona_map.get(journalist_name)

    if orchestrator and not _has_any_tool(orchestrator, DISCOVERY_TOOLS):
        report.issues.append(
            ValidationIssue(
                "warning",
                f"orchestrator '{orchestrator_name}' has no discovery-oriented tools; routing may rely on weak context gathering",
                "swarm_config.yml",
            )
        )

    if journalist and not _has_any_tool(journalist, WRITING_TOOLS):
        report.issues.append(
            ValidationIssue(
                "warning",
                f"journalist '{journalist_name}' has no writing tool; final synthesis may fail to persist outputs",
                "swarm_config.yml",
            )
        )

    for persona in specialists:
        if not _has_any_tool(persona, DISCOVERY_TOOLS):
            report.issues.append(
                ValidationIssue(
                    "warning",
                    f"specialist '{persona.get('name', '')}' has no discovery-oriented tools",
                    "swarm_config.yml",
                )
            )


def _check_reviewer_semantics(report: SwarmDoctorReport, reviewer: dict) -> None:
    reviewer_enabled = reviewer.get("enabled", True)
    max_revision_loops = reviewer.get("max_revision_loops", 3)
    required_elements = reviewer.get("required_elements", []) or []
    rejection_patterns = reviewer.get("rejection_patterns", []) or []
    banned_words = reviewer.get("banned_words", []) or []
    reviewer_model = reviewer.get("model", {}) or {}

    if reviewer_enabled and max_revision_loops < 1:
        report.issues.append(
            ValidationIssue(
                "error",
                "reviewer is enabled but max_revision_loops is less than 1",
                "swarm_config.yml",
            )
        )

    if reviewer_enabled and not required_elements:
        report.issues.append(
            ValidationIssue(
                "error",
                "reviewer is enabled but required_elements is empty",
                "swarm_config.yml",
            )
        )

    if reviewer_enabled and not rejection_patterns:
        report.issues.append(
            ValidationIssue(
                "warning",
                "reviewer is enabled but rejection_patterns is empty; adversarial checks may be too weak",
                "swarm_config.yml",
            )
        )

    if not reviewer_enabled and (required_elements or rejection_patterns or banned_words):
        report.issues.append(
            ValidationIssue(
                "warning",
                "reviewer is disabled but reviewer rules are still configured; they will not run until reviewer is re-enabled",
                "swarm_config.yml",
            )
        )

    if reviewer_model.get("temperature") is not None and not reviewer_model.get("name"):
        report.issues.append(
            ValidationIssue(
                "warning",
                "reviewer model temperature is set without a reviewer model name; the override may be ignored by readers of the config",
                "swarm_config.yml",
            )
        )


@dataclass
class ValidationIssue:
    severity: str
    message: str
    path: str = ""


@dataclass
class SwarmDoctorReport:
    root_dir: Path
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == "warning")

    @property
    def ok(self) -> bool:
        return self.error_count == 0


def _check_persona_sections(report: SwarmDoctorReport, persona_name: str, persona_path: Path) -> None:
    """Warn when a persona.md is missing any of the three required content sections."""
    try:
        headings = {
            line.strip()
            for line in persona_path.read_text(encoding="utf-8").splitlines()
            if line.startswith("## ")
        }
    except OSError as exc:
        report.issues.append(ValidationIssue("error", f"could not read persona file for '{persona_name}': {exc}", str(persona_path)))
        return

    for section in sorted(REQUIRED_PERSONA_SECTIONS - headings):
        report.issues.append(ValidationIssue(
            "warning",
            f"persona '{persona_name}' is missing section '{section}' — loader will return empty value for this field",
            str(persona_path),
        ))


def _check_tool_registry_sync(report: SwarmDoctorReport, config_tools: dict) -> None:
    """Warn about config tools absent from BUILTIN_TOOL_REGISTRY.

    Tools not in BUILTIN_TOOL_REGISTRY are custom additions. They work at runtime
    (the import check catches broken ones), but will be silently dropped if the
    swarm is ever regenerated with ``swarm init --force`` from a blueprint.
    """
    try:
        from automation.builder.templates import BUILTIN_TOOL_REGISTRY
    except Exception:
        return  # builder not available in this context; skip

    builtin_keys = set(BUILTIN_TOOL_REGISTRY.keys())
    for tool_name in sorted(set(config_tools) - builtin_keys):
        report.issues.append(ValidationIssue(
            "warning",
            f"tool '{tool_name}' is not in BUILTIN_TOOL_REGISTRY — it is a custom tool and will not be preserved by 'swarm init --force'",
            "swarm_config.yml",
        ))


def inspect_swarm(root_dir: Path) -> SwarmDoctorReport:
    report = SwarmDoctorReport(root_dir=root_dir)
    config_path = root_dir / "swarm_config.yml"
    if not config_path.exists():
        report.issues.append(ValidationIssue("error", "swarm_config.yml is missing", str(config_path)))
        return report

    try:
        config = load_config(config_path)
    except Exception as e:
        report.issues.append(ValidationIssue("error", f"failed to load config: {e}", str(config_path)))
        return report

    personas = config.get("personas", [])
    tools = config.get("tools", {})
    reviewer = config.get("reviewer", {})
    orchestrator_block = config.get("orchestrator", {})
    persona_names = {persona.get("name", "") for persona in personas}

    orchestrator = orchestrator_block.get("agent")
    journalist = orchestrator_block.get("journalist")
    if orchestrator not in persona_names:
        report.issues.append(ValidationIssue("error", "orchestrator agent is not defined in personas", "swarm_config.yml"))
    if journalist not in persona_names:
        report.issues.append(ValidationIssue("error", "journalist agent is not defined in personas", "swarm_config.yml"))

    _check_role_collisions(report, personas)
    _check_routing_semantics(
        report,
        personas,
        orchestrator_name=orchestrator,
        journalist_name=journalist,
        max_agent_calls=orchestrator_block.get("max_agent_calls", 0),
        max_tool_rounds=orchestrator_block.get("max_tool_rounds_per_agent", 0),
    )
    _check_reviewer_semantics(report, reviewer)

    seen_names = set()
    seen_paths = set()
    for persona in personas:
        persona_name = persona.get("name", "")
        persona_file = persona.get("persona_file", "")
        if persona_name in seen_names:
            report.issues.append(ValidationIssue("error", f"duplicate persona name: {persona_name}", "swarm_config.yml"))
        seen_names.add(persona_name)

        persona_path = root_dir / persona_file.replace("./", "") if persona_file else None
        if not persona_file:
            report.issues.append(ValidationIssue("error", f"persona '{persona_name}' is missing persona_file", "swarm_config.yml"))
            continue

        if persona_file in seen_paths:
            report.issues.append(ValidationIssue("error", f"duplicate persona_file path: {persona_file}", "swarm_config.yml"))
        seen_paths.add(persona_file)

        if not persona_path.exists():
            report.issues.append(ValidationIssue("error", f"persona file missing for '{persona_name}'", str(persona_path)))
        else:
            _check_persona_sections(report, persona_name, persona_path)
        kb_dir = persona_path.parent / "KB"
        if not kb_dir.exists():
            report.issues.append(ValidationIssue("warning", f"KB directory missing for '{persona_name}'", str(kb_dir)))
        elif not any(child.name != ".gitkeep" for child in kb_dir.iterdir()):
            report.issues.append(ValidationIssue("warning", f"KB directory is empty for '{persona_name}'", str(kb_dir)))

        persona_tools = persona.get("tools", [])
        if not persona_tools:
            report.issues.append(ValidationIssue("warning", f"persona '{persona_name}' has no tools", "swarm_config.yml"))
        for tool_name in persona_tools:
            if tool_name not in tools:
                report.issues.append(ValidationIssue("error", f"persona '{persona_name}' references unknown tool '{tool_name}'", "swarm_config.yml"))

    for tool_name, spec in tools.items():
        module_path = spec.get("module", "")
        function_name = spec.get("function", "")
        if not module_path or not function_name:
            report.issues.append(ValidationIssue("error", f"tool '{tool_name}' is missing module/function", "swarm_config.yml"))
            continue
        try:
            module = importlib.import_module(module_path)
            getattr(module, function_name)
        except Exception as e:
            report.issues.append(ValidationIssue("error", f"tool '{tool_name}' failed to import: {e}", "swarm_config.yml"))

    _check_tool_registry_sync(report, tools)

    env_key = config.get("model", {}).get("env_key", "OPENAI_API_KEY")
    if not os.getenv(env_key):
        report.issues.append(ValidationIssue("warning", f"model env key '{env_key}' is not set in the current shell", ".env"))

    return report


def apply_safe_fixes(root_dir: Path) -> list[Path]:
    config = load_config(root_dir / "swarm_config.yml")
    created: list[Path] = []
    for persona in config.get("personas", []):
        persona_file = persona.get("persona_file", "")
        if not persona_file:
            continue
        persona_path = root_dir / persona_file.replace("./", "")
        kb_dir = persona_path.parent / "KB"
        if not kb_dir.exists():
            kb_dir.mkdir(parents=True, exist_ok=True)
            created.append(kb_dir)
        gitkeep = kb_dir / ".gitkeep"
        if not gitkeep.exists():
            gitkeep.write_text("", encoding="utf-8")
            created.append(gitkeep)
    return created


def render_doctor_report(report: SwarmDoctorReport) -> None:
    status = "Healthy" if report.ok else "Problems Found"
    console.print(Panel.fit(f"[bold]{status}[/bold]\nRoot: {report.root_dir}", title="Swarm Doctor"))
    table = Table(title="Checks")
    table.add_column("Severity")
    table.add_column("Message")
    table.add_column("Path")
    if not report.issues:
        table.add_row("ok", "No issues detected", "")
    else:
        for issue in report.issues:
            table.add_row(issue.severity, issue.message, issue.path)
    console.print(table)


def preview_existing_swarm(root_dir: Path) -> None:
    config = load_config(root_dir / "swarm_config.yml")

    console.print(Panel.fit(
        f"[bold]{config['swarm']['name']}[/bold]\n"
        f"{config['swarm'].get('description', '')}\n\n"
        f"Model: {config['model']['provider']}/{config['model']['name']}\n"
        f"Orchestrator: {config.get('orchestrator', {}).get('agent', '')}\n"
        f"Journalist: {config.get('orchestrator', {}).get('journalist', '')}",
        title="Current Swarm",
    ))

    persona_table = Table(title="Personas")
    persona_table.add_column("Name")
    persona_table.add_column("Role")
    persona_table.add_column("File")
    persona_table.add_column("Tools")
    for persona in config.get("personas", []):
        persona_table.add_row(
            persona.get("name", ""),
            persona.get("role", ""),
            persona.get("persona_file", ""),
            ", ".join(persona.get("tools", [])),
        )
    console.print(persona_table)

    tool_table = Table(title="Tool Registry")
    tool_table.add_column("Key")
    tool_table.add_column("Target")
    tool_table.add_column("Description")
    for tool_name, spec in config.get("tools", {}).items():
        tool_table.add_row(
            tool_name,
            f"{spec.get('module', '')}.{spec.get('function', '')}",
            spec.get("description", ""),
        )
    console.print(tool_table)