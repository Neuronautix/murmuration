from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml

from automation.builder.models import SwarmSpec, slugify_name

BLUEPRINT_KIND = "swarm_blueprint"
BLUEPRINT_VERSION = 1


def default_blueprint_path(root_dir: Path, blueprint_name: str) -> Path:
    filename = f"{slugify_name(blueprint_name)}.swarm-blueprint.yml"
    return root_dir / "blueprints" / filename


def export_swarm_blueprint(
    spec: SwarmSpec,
    output_path: Path,
    blueprint_name: str | None = None,
    blueprint_description: str | None = None,
) -> Path:
    payload = {
        "kind": BLUEPRINT_KIND,
        "version": BLUEPRINT_VERSION,
        "blueprint": {
            "name": blueprint_name or spec.swarm_name,
            "description": blueprint_description or spec.swarm_description,
            "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "spec": spec.model_dump(),
        },
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return output_path


def load_swarm_blueprint(path: Path) -> tuple[dict, SwarmSpec]:
    if not path.exists():
        raise FileNotFoundError(f"blueprint file not found: {path}")

    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if payload.get("kind") != BLUEPRINT_KIND:
        raise ValueError(f"invalid blueprint kind in {path}")
    if payload.get("version") != BLUEPRINT_VERSION:
        raise ValueError(
            f"unsupported blueprint version '{payload.get('version')}' in {path}; expected {BLUEPRINT_VERSION}"
        )

    blueprint = payload.get("blueprint", {}) or {}
    spec_data = blueprint.get("spec")
    if not isinstance(spec_data, dict):
        raise ValueError(f"blueprint spec payload missing or invalid in {path}")

    spec = SwarmSpec.model_validate(spec_data)
    return blueprint, spec


def apply_blueprint_overrides(
    spec: SwarmSpec,
    swarm_name: str | None = None,
    swarm_description: str | None = None,
    domain: str | None = None,
) -> SwarmSpec:
    updated = spec.model_copy(deep=True)
    if swarm_name:
        updated.swarm_name = swarm_name
    if swarm_description:
        updated.swarm_description = swarm_description
    if domain:
        old_domain = updated.domain
        updated.domain = domain
        if old_domain != domain:
            for persona in updated.personas:
                persona.kb_focus = [item.replace(old_domain, domain, 1) for item in persona.kb_focus]
    return updated