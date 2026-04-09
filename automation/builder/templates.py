from __future__ import annotations

from automation.builder.models import PersonaSpec, SwarmSpec, ToolSpec, slugify_name


def _default_reviewer_banned_words() -> list[str]:
    return [
        "groundbreaking",
        "revolutionary",
        "game-changing",
        "paradigm-shifting",
        "unprecedented",
    ]


def _default_reviewer_required_elements() -> list[str]:
    return [
        "clear task framing",
        "a short limitations or uncertainty section",
        "source-aware in-text citations when claims rely on external evidence",
        "a final References or Sources section",
    ]


def _default_reviewer_rejection_patterns() -> list[str]:
    return [
        "any unsupported factual claim presented with certainty",
    ]

BUILTIN_TOOL_REGISTRY = {
    "search_literature": ToolSpec(
        module="automation.tools",
        function="search_pubmed",
        description="Search PubMed for peer-reviewed biomedical literature",
    ),
    "search_web": ToolSpec(
        module="automation.tools",
        function="search_you_engine",
        description="Search the live web via You.com API",
    ),
    "check_schema_org": ToolSpec(
        module="automation.tools",
        function="check_schema_org",
        description="Validate terms against the Schema.org vocabulary",
    ),
    "write_section": ToolSpec(
        module="automation.tools",
        function="write_manuscript_section",
        description="Write a drafted section to disk as Markdown",
    ),
    "append_traceability": ToolSpec(
        module="automation.tools",
        function="append_traceability_matrix",
        description="Log a fact and its source to the Knowledge Traceability Matrix",
    ),
    "search_kb": ToolSpec(
        module="automation.tools",
        function="search_knowledge_base",
        description="Search the local Knowledge Base documents",
    ),
    "scrape_page": ToolSpec(
        module="automation.tools",
        function="scrape_webpage",
        description="Fetch and extract full text content from a URL",
    ),
    "search_preprints": ToolSpec(
        module="automation.tools",
        function="search_semantic_scholar",
        description="Search Semantic Scholar for preprints and recent academic papers",
    ),
    "trace_literature_network": ToolSpec(
        module="automation.tools",
        function="trace_literature_network",
        description="Expand a major paper through its references and lead authors",
    ),
    "you_research": ToolSpec(
        module="automation.tools",
        function="you_research",
        description="Use You.com Research for synthesised multi-source web answers",
    ),
    "lookup_doi": ToolSpec(
        module="automation.tools",
        function="lookup_doi",
        description="Look up full citation metadata via Europe PMC and Crossref",
    ),
    "git_snapshot": ToolSpec(
        module="automation.tools",
        function="git_commit_snapshot",
        description="Commit swarm output files for version-controlled audit trail",
    ),
}

ARCHETYPE_SPECS = {
    "orchestrator": {
        "icon": "👑",
        "role": "Orchestrator and synthesizer",
        "tools": ["search_literature", "search_web", "search_kb", "append_traceability"],
        "core_mission": "Coordinate the swarm, route specialist work, and merge findings into a coherent plan.",
        "kb_focus": [
            "project scope notes and task decomposition guidelines",
            "shared bibliographies and high-level definitions",
            "domain constraints and known disagreements",
        ],
        "behavior_rules": [
            "start by defining the user objective and target output",
            "route sub-questions to the most appropriate specialist rather than broadcasting everything",
            "resolve conflicts between sources or methods before synthesizing",
            "maintain a clear synthesis path to the final writer",
        ],
    },
    "domain-specialist": {
        "icon": "🧠",
        "role": "Domain specialist",
        "tools": [
            "search_literature",
            "search_preprints",
            "search_web",
            "search_kb",
            "scrape_page",
            "lookup_doi",
            "append_traceability",
        ],
        "core_mission": "Investigate the core substantive questions in the target domain using primary and secondary sources.",
        "kb_focus": [
            "domain-specific review papers and key empirical findings",
            "major construct definitions and reference documents",
            "active limitations, disagreements, and open questions",
        ],
        "behavior_rules": [
            "separate strong evidence from weak or indirect claims",
            "look up citation metadata when a reference seems central",
            "log important findings to the traceability matrix",
        ],
    },
    "literature-scout": {
        "icon": "📚",
        "role": "Literature scout for citation chaining and author-trail expansion",
        "tools": [
            "search_literature",
            "search_preprints",
            "trace_literature_network",
            "lookup_doi",
            "scrape_page",
            "append_traceability",
        ],
        "core_mission": "Find the landmark papers in the field and expand from them through references, lead authors, and adjacent citation clusters.",
        "kb_focus": [
            "shared bibliographies, reading lists, and literature maps",
            "landmark reviews, meta-analyses, and consensus papers",
            "field overviews and citation-cluster anchor papers",
        ],
        "behavior_rules": [
            "start from a high-yield seed paper whenever possible",
            "use literature-network tracing before falling back to broad web search",
            "distinguish clearly between seed papers, references, and author-derived follow-ons",
        ],
    },
    "methods-reviewer": {
        "icon": "📊",
        "role": "Methods and evidence-quality reviewer",
        "tools": [
            "search_literature",
            "search_preprints",
            "search_web",
            "search_kb",
            "scrape_page",
            "lookup_doi",
            "append_traceability",
        ],
        "core_mission": "Check whether study design, measurement quality, and evidence synthesis justify the claims being made.",
        "kb_focus": [
            "methods papers and quality appraisal guides",
            "measurement instruments, codebooks, and validation studies",
            "bias taxonomies and comparability issues across study designs",
        ],
        "behavior_rules": [
            "challenge overconfident claims that exceed the underlying evidence",
            "look for design flaws before accepting strong conclusions",
            "surface what remains uncertain, not just what appears supported",
        ],
    },
    "intervention-specialist": {
        "icon": "🛟",
        "role": "Intervention and applied-practice specialist",
        "tools": [
            "search_literature",
            "search_preprints",
            "search_web",
            "search_kb",
            "scrape_page",
            "lookup_doi",
            "append_traceability",
        ],
        "core_mission": "Evaluate intervention options, implementation constraints, and applied implications for the target domain.",
        "kb_focus": [
            "intervention summaries and systematic reviews",
            "practice guidelines and implementation workflows",
            "evidence base for applied recommendations and care pathways",
        ],
        "behavior_rules": [
            "prefer systematic reviews and high-quality comparative evidence when available",
            "separate practical implications from stronger causal conclusions",
            "note where intervention evidence is thin or non-transferable",
        ],
    },
    "journalist": {
        "icon": "✍️",
        "role": "Final writer and documentarian",
        "tools": ["write_section", "append_traceability", "git_snapshot"],
        "core_mission": "Write the final output clearly, neutrally, and in a form the user can reuse.",
        "kb_focus": [
            "style guides and report templates",
            "reference formatting conventions",
            "markdown documentation standards",
        ],
        "behavior_rules": [
            "do not invent claims that specialists did not support",
            "preserve caveats and uncertainty in the final draft",
            "produce structured outputs with formal in-text citations and a References section",
            "maintain strictly neutral, professional tone — avoid hype words",
        ],
    },
}

AVAILABLE_ARCHETYPES = sorted(ARCHETYPE_SPECS.keys())


BLUEPRINT_SPECS = {
    "research-core": {
        "description": "Balanced default research team for broad literature-backed synthesis.",
        "personas": [
            ("orchestrator", "Coordinator"),
            ("literature-scout", "LiteratureScout"),
            ("domain-specialist", "DomainSpecialist"),
            ("methods-reviewer", "MethodsReviewer"),
            ("journalist", "Journalist"),
        ],
        "orchestrator": "Coordinator",
        "journalist": "Journalist",
        "max_agent_calls": 8,
        "max_tool_rounds_per_agent": 5,
        "reviewer_enabled": True,
        "hitl_enabled": False,
    },
    "literature-mapping": {
        "description": "Citation-chaining heavy team for landmark papers, author trails, and evidence maps.",
        "personas": [
            ("orchestrator", "Coordinator"),
            ("literature-scout", "LitScoutLead"),
            ("domain-specialist", "TheoryMapper"),
            ("methods-reviewer", "EvidenceAuditor"),
            ("journalist", "Journalist"),
        ],
        "orchestrator": "Coordinator",
        "journalist": "Journalist",
        "max_agent_calls": 10,
        "max_tool_rounds_per_agent": 6,
        "reviewer_enabled": True,
        "hitl_enabled": False,
    },
    "intervention-lab": {
        "description": "Applied evidence team for treatment, prevention, and implementation questions.",
        "personas": [
            ("orchestrator", "Coordinator"),
            ("literature-scout", "LiteratureScout"),
            ("intervention-specialist", "InterventionLead"),
            ("methods-reviewer", "MethodsReviewer"),
            ("journalist", "Journalist"),
        ],
        "orchestrator": "Coordinator",
        "journalist": "Journalist",
        "max_agent_calls": 8,
        "max_tool_rounds_per_agent": 5,
        "reviewer_enabled": True,
        "hitl_enabled": False,
    },
    "rapid-brief": {
        "description": "Lean briefing team for concise evidence briefs with fewer specialist hops.",
        "personas": [
            ("orchestrator", "Coordinator"),
            ("domain-specialist", "SubjectLead"),
            ("methods-reviewer", "QualityReviewer"),
            ("journalist", "Journalist"),
        ],
        "orchestrator": "Coordinator",
        "journalist": "Journalist",
        "max_agent_calls": 5,
        "max_tool_rounds_per_agent": 4,
        "reviewer_enabled": True,
        "hitl_enabled": False,
    },
}

AVAILABLE_BLUEPRINTS = sorted(BLUEPRINT_SPECS.keys())


def get_blueprint_names() -> list[str]:
    return list(AVAILABLE_BLUEPRINTS)


def get_blueprint_descriptions() -> dict[str, str]:
    return {
        name: spec["description"]
        for name, spec in BLUEPRINT_SPECS.items()
    }


def _validate_archetype_tools(archetype: str, tools: list[str]) -> None:
    """Raise if any tool listed in an archetype spec is absent from BUILTIN_TOOL_REGISTRY.

    This catches mistakes in ARCHETYPE_SPECS at swarm-build time rather than
    at runtime when the tool import would silently fail.
    """
    unknown = [t for t in tools if t not in BUILTIN_TOOL_REGISTRY]
    if unknown:
        raise ValueError(
            f"archetype '{archetype}' references tools not in BUILTIN_TOOL_REGISTRY: {unknown}. "
            f"Add them to BUILTIN_TOOL_REGISTRY or remove them from the archetype."
        )


def build_persona_from_archetype(
    archetype: str,
    domain: str,
    name: str | None = None,
    role: str | None = None,
) -> PersonaSpec:
    if archetype not in ARCHETYPE_SPECS:
        raise ValueError(f"unknown archetype '{archetype}'")

    spec = ARCHETYPE_SPECS[archetype]
    _validate_archetype_tools(archetype, spec["tools"])
    persona_name = name or archetype.replace("-", " ").title()
    persona_role = role or spec["role"]

    return PersonaSpec(
        name=persona_name,
        folder_name=slugify_name(persona_name),
        icon=spec["icon"],
        role=persona_role,
        tools=list(spec["tools"]),
        core_mission=spec["core_mission"],
        kb_focus=[f"{domain}: {item}" for item in spec["kb_focus"]],
        behavior_rules=list(spec["behavior_rules"]),
    )


def build_starter_swarm_spec(
    domain: str,
    swarm_name: str,
    swarm_description: str,
    model_provider: str = "openai",
    model_name: str = "gpt-4o",
    model_env_key: str = "OPENAI_API_KEY",
) -> SwarmSpec:
    return build_swarm_spec_from_blueprint(
        blueprint="research-core",
        domain=domain,
        swarm_name=swarm_name,
        swarm_description=swarm_description,
        model_provider=model_provider,
        model_name=model_name,
        model_env_key=model_env_key,
    )


def build_swarm_spec_from_blueprint(
    blueprint: str,
    domain: str,
    swarm_name: str,
    swarm_description: str,
    model_provider: str = "openai",
    model_name: str = "gpt-4o",
    model_env_key: str = "OPENAI_API_KEY",
) -> SwarmSpec:
    if blueprint not in BLUEPRINT_SPECS:
        raise ValueError(f"unknown blueprint '{blueprint}'")

    blueprint_spec = BLUEPRINT_SPECS[blueprint]
    personas = [
        build_persona_from_archetype(archetype, domain, name=name)
        for archetype, name in blueprint_spec["personas"]
    ]

    return SwarmSpec(
        swarm_name=swarm_name,
        swarm_description=swarm_description,
        domain=domain,
        model_provider=model_provider,
        model_name=model_name,
        model_env_key=model_env_key,
        personas=personas,
        orchestrator_agent=blueprint_spec["orchestrator"],
        journalist_agent=blueprint_spec["journalist"],
        max_agent_calls=blueprint_spec["max_agent_calls"],
        max_tool_rounds_per_agent=blueprint_spec["max_tool_rounds_per_agent"],
        reviewer_enabled=blueprint_spec["reviewer_enabled"],
        reviewer_banned_words=_default_reviewer_banned_words(),
        reviewer_required_elements=_default_reviewer_required_elements(),
        reviewer_rejection_patterns=_default_reviewer_rejection_patterns(),
        hitl_enabled=blueprint_spec["hitl_enabled"],
        tool_registry=BUILTIN_TOOL_REGISTRY,
    )