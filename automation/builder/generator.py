from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

from automation.builder.models import PersonaSpec, SwarmSpec

console = Console()


@dataclass
class GenerationResult:
    root_dir: Path
    config_path: Path
    written_files: list[Path] = field(default_factory=list)
    created_directories: list[Path] = field(default_factory=list)


@dataclass
class PlannedFile:
    path: Path
    content: str


def render_persona_markdown(persona: PersonaSpec) -> str:
    lines = [
        "## Core Mission",
        persona.core_mission,
        "",
        "## Knowledge Base (KB) Focus",
    ]
    lines.extend(f"-   {item}" for item in persona.kb_focus)
    lines.extend([
        "",
        "## Behavior",
    ])
    lines.extend(f"-   {item}" for item in persona.behavior_rules)
    lines.append("")
    return "\n".join(lines)


def render_swarm_config(spec: SwarmSpec) -> str:
    return yaml.safe_dump(
        spec.to_config(),
        sort_keys=False,
        allow_unicode=True,
    )


def build_generation_plan(spec: SwarmSpec, root_dir: Path) -> list[PlannedFile]:
    plan = [PlannedFile(path=root_dir / "swarm_config.yml", content=render_swarm_config(spec))]
    for persona in spec.personas:
        persona_root = root_dir / "agents" / persona.folder_name
        plan.append(PlannedFile(path=persona_root / "persona.md", content=render_persona_markdown(persona)))
        plan.append(PlannedFile(path=persona_root / "KB" / ".gitkeep", content=""))
    return plan


def preview_generation_diff(spec: SwarmSpec, root_dir: Path) -> None:
    plan = build_generation_plan(spec, root_dir)
    for item in plan:
        existing = item.path.read_text(encoding="utf-8") if item.path.exists() else ""
        if existing == item.content:
            continue

        if item.path.exists():
            diff = "\n".join(difflib.unified_diff(
                existing.splitlines(),
                item.content.splitlines(),
                fromfile=f"current/{item.path.relative_to(root_dir)}",
                tofile=f"next/{item.path.relative_to(root_dir)}",
                lineterm="",
            ))
            console.print(Panel(Syntax(diff or "(no diff)", "diff", theme="ansi_dark"), title=str(item.path.relative_to(root_dir))))
        else:
            console.print(Panel(Syntax(item.content or "", "markdown", theme="ansi_dark"), title=f"new/{item.path.relative_to(root_dir)}"))


def _write_text(path: Path, content: str, force: bool, result: GenerationResult) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite existing file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent not in result.created_directories:
        result.created_directories.append(path.parent)
    path.write_text(content, encoding="utf-8")
    result.written_files.append(path)


def generate_swarm_project(spec: SwarmSpec, root_dir: Path, force: bool = False) -> GenerationResult:
    result = GenerationResult(
        root_dir=root_dir,
        config_path=root_dir / "swarm_config.yml",
    )

    for item in build_generation_plan(spec, root_dir):
        kb_dir = item.path.parent if item.path.name == ".gitkeep" else None
        if kb_dir is not None:
            kb_dir.mkdir(parents=True, exist_ok=True)
            if kb_dir not in result.created_directories:
                result.created_directories.append(kb_dir)

        _write_text(item.path, item.content, force=force or item.path.name == ".gitkeep", result=result)

    for persona in spec.personas:
        kb_dir = root_dir / "agents" / persona.folder_name / "KB"
        kb_dir.mkdir(parents=True, exist_ok=True)
        if kb_dir not in result.created_directories:
            result.created_directories.append(kb_dir)

    return result