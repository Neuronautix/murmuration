# Research Swarm — Improvements & Fix Tracker

Items are grouped by theme. `[x]` = implemented and pushed. `[ ]` = pending.

---

## Architecture: Multi-Agent Refactor

- [x] Rewrite `graph.py` as a true supervisor multi-agent pattern — each persona is a
      separate LangGraph node with its own system prompt and scoped tool set
- [x] Add per-persona helper functions to `config.py`:
      `get_persona_config`, `load_tools_for_persona`, `build_agent_system_prompt`
- [x] Update `main.py` `execute()` to pass task string as initial state
      (no swarm-level system prompt injected from CLI)
- [x] Add `orchestrator:` section to `swarm_config.yml`
      (`agent`, `journalist`, `max_agent_calls`, `max_tool_rounds_per_agent`)
- [x] Remove `truncate(300)` from `system_prompt_template` persona injection —
      full persona.md content now reaches the model
- [x] Mark `system_prompt_template` as `LEGACY — NOT USED` in `swarm_config.yml`
- [x] Mark `build_system_prompt()` as legacy in `config.py` docstring
- [x] Remove `load_tools()` from `config.py` — dead code, no longer called at runtime

---

## Orchestrator

- [x] Structured output routing via `OrchestratorDecision` Pydantic model
      (`reasoning`, `next_agents: list[str]`, `instructions`)
- [x] Parallel specialist fan-out via LangGraph `Send` — orchestrator can dispatch
      multiple specialists simultaneously when sub-questions are independent
- [x] Add `SPECIALISTS ALREADY CONSULTED` list to orchestrator routing prompt
      (derived from `agent_outputs.keys()`) to prevent redundant re-routing
- [x] Fix `agent_call_count` to increment only for specialist dispatches
      (not for Journalist or END routing); gives full `max_agent_calls` budget
      for actual research calls
- [x] Allow per-agent instructions in parallel dispatch — `AgentAssignment(BaseModel)`
      added to `OrchestratorDecision`; `agent_assignments: dict` in `GraphState`;
      specialists read `state["agent_assignments"].get(name)` before falling back
      to the shared `next_instructions`
- [x] Cache `create_model(config)` at build time — all LLM instances created once in
      `build_graph()`; specialist models pre-bound with `base_model.bind_tools(tools)`;
      eliminates per-call API client instantiation

---

## Tool Bugs & Safety

- [x] Fix `_run_tool_loop` bug — `messages = tool_result["messages"]` replaced with
      `messages = messages + tool_result["messages"]`; prior context was being
      discarded after the first tool call in every agent node
- [x] Fix `git_commit_snapshot` — replace `git add -A` with explicit safe paths
      (`Drafts/` + traceability matrix read from config); eliminates risk of
      staging `.env`, credentials, notebooks, or binaries
- [x] `write_manuscript_section` silently overwrites on repeated calls with the same
      section name — fixed: version counter suffix (_v2, _v3, …) appended when base
      file already exists
- [x] `append_traceability_matrix` appends raw markdown table rows with no structure
      validation — fixed: checks file exists and table header sentinel is present
      before writing; returns descriptive error if either check fails

---

## Tool Capability

- [x] `search_pubmed`: switch from `esummary` (title + PMID only) to `efetch` with
      `rettype=abstract&retmode=xml`; parses full abstract including labelled sections
- [x] Add `search_semantic_scholar` tool (Semantic Scholar Graph API, no key needed);
      indexes preprints, arXiv, CHI, and 2024–2026 papers absent from PubMed;
      registered as `search_preprints` and assigned to all four research specialists
- [x] Add `Search Fallback` rule to all specialist `persona.md` files:
      if `search_literature` returns empty → try `search_preprints` → then `search_web`
- [x] Fix `search_you_engine` base URL (legacy `ydc-index.io` endpoint → `api.ydc-index.io`)
      and update response parsing for current API response structure
- [x] Add `you_research` tool (You.com Research / RAG API) — synthesised answer with citations;
      assigned to all research specialist agents
- [x] Replace `scrape_webpage` BeautifulSoup implementation with You.com Contents API
      (`/news?url=...`); BeautifulSoup kept as fallback for API unavailability
- [x] Assign `scrape_page` to all four research specialists in `swarm_config.yml`
      (was registered but unassigned)
- [x] Add `lookup_doi` tool (Europe PMC primary + Crossref fallback) — no API key;
      returns title, authors, journal, year, DOI, abstract snippet, OA PDF link;
      registered in swarm_config.yml and assigned to all four research specialists
- [x] Replace deprecated `langchain_community` embedding/vectorstore imports in
      `ingest.py` and `tools.py` with current `langchain-huggingface` / `langchain-chroma`;
      graceful fallback to `langchain_community` if new packages not installed;
      added `langchain-huggingface` + `langchain-chroma` to `requirements.txt`
- [x] Human-in-the-Loop (HITL) via LangGraph `interrupt()` — four checkpoints:
      `pre_flight` (CLI confirm), `post_plan` (approve/redirect orchestrator's first plan),
      `pre_journalist` (add final framing), `on_rejection` (REVISE / OVERRIDE / custom);
      graph compiled with `MemorySaver` when `hitl.enabled: true`; stream loop in
      `main.py` detects `__interrupt__` events and resumes with `Command(resume=answer)`;
      fully autonomous when `hitl.enabled: false` (no checkpointer overhead)
- [x] Add async tool execution within specialists — currently all tool calls are
      synchronous and sequential; `asyncio` + `ToolNode` async variants would allow
      parallel `search_pubmed` + `search_semantic_scholar` within one specialist call
      — `_run_tool_loop_async` added to `graph.py` using `model.ainvoke` +
      `ToolNode.ainvoke` (concurrent tool dispatch via `asyncio.gather`);
      specialist and journalist nodes are now `async def`; `main.py` streams via
      `graph.astream` inside `asyncio.run(_run_graph_async(...))`
- [x] Add token/cost tracking per run — `_extract_token_usage()` reads `usage_metadata`
      (LangChain ≥ 0.2) or `response_metadata.token_usage` (older); `_merge_token_usage`
      accumulates in `GraphState`; `main.py` writes `Drafts/run_metrics.json` with
      input/output/total tokens and estimated cost (gpt-4o list pricing)

---

## Reviewer-2 (Adversarial Checkpoint)

- [x] Reviewer model diversity — `create_reviewer_model()` in `config.py` merges
      `reviewer.model` overrides onto the base config; active config sets
      `temperature: 0.8` for genuinely adversarial critique vs `0.2` for agents
- [x] Add AI-chatbot construct instability check to `reviewer.required_elements` —
      reviewer rejects any output on AI/LLM use that lacks a note on whether
      evidence meets clinical-level impairment criteria
- [x] Reviewer uses the same model name as research agents (only temperature differs);
      for maximum adversarial diversity, configure a different model family
      (e.g., Anthropic claude-opus when agents run on gpt-4o, or vice versa)
      — cross-provider examples (Anthropic claude-opus-4-6, Google gemini-1.5-pro)
      added as commented blocks in `swarm_config.yml` under `reviewer.model`
- [x] Add reviewer checks: prevalence claims must name instrument + cut-off; IGD mentions
      must clarify ICD-11 vs DSM-5 status; output must not imply pathology from frequency
      alone — all three enforced via `required_elements` and `rejection_patterns` in
      `swarm_config.yml`; `build_reviewer_prompt()` in `config.py` now incorporates
      `rejection_patterns` into the generated adversarial prompt

---

## Knowledge Base & Literature

- [x] Add 2025–2026 AI-dependence literature to all five agent KBs:
      `ClinicalPsych`, `EpiScope`, `NeuroCogs`, `CarePath`, `DrNexus`
      (15 peer-reviewed and preprint references across construct debate,
      scales, reward mechanisms, mental health correlates, and care implications)
- [x] Add separate KB packets for social media disorder, gaming disorder (IGD),
      and smartphone overuse (currently all live in the same general PIU KB)
      — dedicated `.md` files added to `ClinicalPsych/KB/`, `EpiScope/KB/`,
      `NeuroCogs/KB/`, and `DrNexus/KB/` for all three constructs
- [x] Add intervention summaries for family-based, school-based, and telehealth
      pathways to `CarePath/KB/`
      — `family_based_interventions.md`, `school_based_interventions.md`, and
      `telehealth_pathways.md` added with evidence summaries and key references
- [x] Add traceability-matrix auto-header — `main.py` creates `Knowledge_Traceability_Matrix.md`
      with Markdown table header if absent; appends a dated run-separator
      (`### Run: YYYY-MM-DD HH:MM | Task: ...`) before each graph execution

---

## Clinical Workflow & Output Templates

- [x] Add `report` CLI command with output mode templates — `REPORT_MODES` dict in
      `main.py` with templates for `scoping-review`, `narrative-review`, `evidence-brief`;
      selectable via `--mode` flag; prepends mode instruction to the research prompt
- [x] Add optional screening prompts for adolescent, university, and general-adult
      populations (different PIU base-rates, instruments, and clinical thresholds apply)
      — `adolescent-screen`, `university-screen`, `general-adult-screen` modes added
      to `REPORT_MODES` in `main.py`
- [x] Add prompt packs for grant support, manuscript drafting, and journalistic briefings
      — `grant-support`, `manuscript-draft`, `journalistic-brief` modes added to
      `REPORT_MODES` in `main.py`; `report` command docstring and `--mode` help updated

---

## Interactive Swarm Builder

- [x] Introduce a `SwarmBuilder` core module separate from the CLI so config generation,
      preview, and file writes are testable without terminal interaction
- [x] Add an initial `swarm init` command backed by the builder core; current version
      supports starter-team generation, custom specialist creation, preview, and safe writes
- [x] Add `swarm preview` and `swarm doctor` commands for inspection and validation of
      on-disk swarms, including persona files, KB folders, tool imports, and env-key checks
- [x] Keep `Typer` as the CLI entrypoint and add `Questionary` support with fallback to
      plain prompts so the wizard is richer without depending entirely on one prompt library
- [x] Add safe diff previews before builder writes and add `swarm persona add` /
      `swarm persona edit` commands for modifying existing swarms without hand-editing YAML
- [x] Add `swarm doctor --fix` for safe filesystem repairs such as missing KB directories
      and `.gitkeep` placeholders
- [x] Add `swarm team configure` and `swarm review configure` so non-persona edits no longer
      require direct YAML changes
- [x] Add targeted commands for model/provider changes, tool registry curation, and swarm-level
      metadata updates so the remaining top-level config sections are editable without raw YAML
- [x] Add custom tool-registry editing flows so users can add, remove, or override non-built-in
      tools from the CLI instead of only curating the built-in registry subset
- [x] Replace the older `scaffold` path with the richer builder workflow and align the
      CLI creation path around `swarm init` (kept as a deprecated alias for compatibility)
- [x] Add persona templates for common roles: orchestrator, literature scout, critic,
      methodology reviewer, scribe, and domain specialist
      (`ARCHETYPE_SPECS` in `automation/builder/templates.py` — six archetypes:
      `orchestrator`, `domain-specialist`, `literature-scout`, `methods-reviewer`,
      `intervention-specialist`, `journalist`; used by `swarm persona add` and `swarm init`)
- [x] Add `swarm doctor` validation: missing persona files, unknown tools, invalid routing,
      duplicate roles, empty KB paths, and reviewer rule inconsistencies
- [x] Add `swarm preview` before file write so users can inspect team topology, tool scopes,
      and a config diff before confirming changes
      (`preview_generation_diff()` called before every write in `main.py`;
      `preview_swarm_spec()` called before confirmation in all builder commands)
- [x] Support both interactive mode and non-interactive flags so teams can be created from CI,
      scripts, or future UI layers using the same backend module
      (`--interactive/--no-interactive` flag on `swarm init`; `--yes` to skip confirmation;
      `swarm scaffold` deprecated alias for fully non-interactive generation)
- [x] Add named starter blueprints so users can begin from higher-level team presets instead of
      only one default research-core layout
- [x] Add blueprint export/import so custom teams can be saved as portable YAML files and reused
      across repositories without hand-copying config and persona folders
- [x] Add richer blueprint packs and reusable policy presets for common workflows such as
      evidence mapping, intervention assessment, and rapid briefing
      (`BLUEPRINT_SPECS` in `automation/builder/templates.py` — four presets:
      `research-core`, `literature-mapping`, `intervention-lab`, `rapid-brief`)

---

## Methodological Guardrails (Reviewer Rules)

*All three checks below were implemented in swarm_config.yml (`required_elements` +
`rejection_patterns`) and wired into the auto-generated reviewer prompt via
`build_reviewer_prompt()` in config.py.*

- [x] Reject outputs that imply high-frequency internet use is inherently pathological
      without evidence of functional impairment (`rejection_patterns` key)
- [x] Reject prevalence claims that do not name the screening instrument and cut-off
      (`required_elements`: "…cut-off score used must be stated")
- [x] Require explicit disorder-status clarification when IGD or internet gaming disorder
      is mentioned (`required_elements`: "ICD-11 recognized vs. DSM-5 Section III")
