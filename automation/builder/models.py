from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


def slugify_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "", value.strip())
    return cleaned or "Persona"


class ToolSpec(BaseModel):
    module: str
    function: str
    description: str


class PersonaSpec(BaseModel):
    name: str
    folder_name: str = ""
    icon: str = "🤖"
    role: str
    tools: list[str] = Field(default_factory=list)
    core_mission: str
    kb_focus: list[str] = Field(default_factory=list)
    behavior_rules: list[str] = Field(default_factory=list)

    @field_validator("name", "role", "core_mission")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("field cannot be empty")
        return value

    @field_validator("folder_name")
    @classmethod
    def validate_folder_name(cls, value: str) -> str:
        return slugify_name(value) if value else value

    @field_validator("tools")
    @classmethod
    def validate_tools(cls, value: list[str]) -> list[str]:
        tools = [tool.strip() for tool in value if tool and tool.strip()]
        if not tools:
            raise ValueError("persona must have at least one tool")
        return list(dict.fromkeys(tools))

    @field_validator("kb_focus", "behavior_rules")
    @classmethod
    def normalize_bullets(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item and item.strip()]

    @model_validator(mode="after")
    def ensure_folder_name(self) -> "PersonaSpec":
        if not self.folder_name:
            self.folder_name = slugify_name(self.name)
        return self

    @property
    def persona_file(self) -> str:
        return f"./agents/{self.folder_name}/persona.md"

    @property
    def kb_dir(self) -> str:
        return f"./agents/{self.folder_name}/KB"


class SwarmSpec(BaseModel):
    swarm_name: str
    swarm_description: str
    domain: str
    output_dir: str = "./Drafts"
    traceability_matrix: str = "./Knowledge_Traceability_Matrix.md"
    model_provider: str = "openai"
    model_name: str = "gpt-4o"
    model_temperature: float = 0.2
    model_env_key: str = "OPENAI_API_KEY"
    personas: list[PersonaSpec]
    orchestrator_agent: str
    journalist_agent: str
    max_agent_calls: int = 8
    max_tool_rounds_per_agent: int = 5
    reviewer_enabled: bool = True
    reviewer_max_revision_loops: int = 3
    reviewer_banned_words: list[str] = Field(default_factory=list)
    reviewer_required_elements: list[str] = Field(default_factory=list)
    reviewer_rejection_patterns: list[str] = Field(default_factory=list)
    reviewer_tone: str = "strictly factual, objective, and neutral"
    reviewer_model_name: str | None = None
    reviewer_model_temperature: float | None = 0.7
    hitl_enabled: bool = False
    hitl_checkpoints: list[str] = Field(default_factory=lambda: [
        "pre_flight",
        "post_plan",
        "pre_journalist",
        "on_rejection",
    ])
    epistemic_tags: list[str] = Field(default_factory=lambda: [
        "[FACT]",
        "[INFERENCE]",
        "[SPECULATION]",
    ])
    tool_registry: dict[str, ToolSpec]

    @field_validator("swarm_name", "swarm_description", "domain", "model_provider", "model_name", "model_env_key")
    @classmethod
    def validate_text_fields(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("field cannot be empty")
        return value

    @field_validator("model_temperature")
    @classmethod
    def validate_temperature(cls, value: float) -> float:
        if value < 0 or value > 2:
            raise ValueError("model temperature must be between 0 and 2")
        return value

    @field_validator("max_agent_calls", "max_tool_rounds_per_agent", "reviewer_max_revision_loops")
    @classmethod
    def validate_positive_ints(cls, value: int) -> int:
        if value < 1:
            raise ValueError("value must be at least 1")
        return value

    @model_validator(mode="after")
    def validate_team_shape(self) -> "SwarmSpec":
        persona_names = [persona.name for persona in self.personas]
        if len(persona_names) < 3:
            raise ValueError("swarm must include at least three personas")
        if len(persona_names) != len(set(persona_names)):
            raise ValueError("persona names must be unique")

        folder_names = [persona.folder_name for persona in self.personas]
        if len(folder_names) != len(set(folder_names)):
            raise ValueError("persona folder names must be unique")

        if self.orchestrator_agent not in persona_names:
            raise ValueError("orchestrator must be one of the defined personas")
        if self.journalist_agent not in persona_names:
            raise ValueError("journalist must be one of the defined personas")
        if self.orchestrator_agent == self.journalist_agent:
            raise ValueError("orchestrator and journalist must be different personas")

        known_tools = set(self.tool_registry.keys())
        for persona in self.personas:
            unknown_tools = [tool for tool in persona.tools if tool not in known_tools]
            if unknown_tools:
                raise ValueError(
                    f"persona '{persona.name}' references unknown tools: {unknown_tools}"
                )

        specialist_names = [
            persona.name for persona in self.personas
            if persona.name not in {self.orchestrator_agent, self.journalist_agent}
        ]
        if not specialist_names:
            raise ValueError("swarm must include at least one research specialist")

        if self.hitl_enabled and not self.hitl_checkpoints:
            raise ValueError("HITL is enabled but no checkpoints are configured")

        if self.reviewer_enabled and not self.reviewer_required_elements:
            raise ValueError("reviewer is enabled but required elements are empty")

        return self

    def to_config(self) -> dict[str, Any]:
        reviewer_block: dict[str, Any] = {
            "enabled": self.reviewer_enabled,
            "max_revision_loops": self.reviewer_max_revision_loops,
            "banned_words": self.reviewer_banned_words,
            "required_elements": self.reviewer_required_elements,
            "rejection_patterns": self.reviewer_rejection_patterns,
            "tone": self.reviewer_tone,
        }
        if self.reviewer_model_name:
            reviewer_block["model"] = {
                "name": self.reviewer_model_name,
                "temperature": self.reviewer_model_temperature,
            }

        return {
            "swarm": {
                "name": self.swarm_name,
                "description": self.swarm_description,
                "output_dir": self.output_dir,
                "traceability_matrix": self.traceability_matrix,
            },
            "orchestrator": {
                "agent": self.orchestrator_agent,
                "journalist": self.journalist_agent,
                "max_agent_calls": self.max_agent_calls,
                "max_tool_rounds_per_agent": self.max_tool_rounds_per_agent,
            },
            "model": {
                "provider": self.model_provider,
                "name": self.model_name,
                "temperature": self.model_temperature,
                "env_key": self.model_env_key,
            },
            "personas": [
                {
                    "name": persona.name,
                    "icon": persona.icon,
                    "role": persona.role,
                    "persona_file": persona.persona_file,
                    "tools": persona.tools,
                }
                for persona in self.personas
            ],
            "tools": {
                name: tool.model_dump()
                for name, tool in self.tool_registry.items()
            },
            "reviewer": reviewer_block,
            "hitl": {
                "enabled": self.hitl_enabled,
                "checkpoints": self.hitl_checkpoints,
            },
            "epistemic_tags": self.epistemic_tags,
        }