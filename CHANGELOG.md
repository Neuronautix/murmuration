# Changelog

All notable changes to this project are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

### Fixed
- **Orchestration quick wins** (see `docs/dev/ORCHESTRATION_AUDIT.md`, findings B1, B3, B6–B10, B12, R4, R7, R8, R11–R15):
  - An orchestrator decision of `['END']` no longer crashes the run with a `KeyError`; it routes to the Journalist, which is the only way a run can finish.
  - Per-agent assignments are replaced on every orchestrator pass instead of merged forever, so a stale instruction from an earlier round can no longer shadow new ones.
  - Reviewer verdicts such as `**APPROVED**` or `APPROVED.` are recognised as approvals instead of burning a revision loop.
  - Exceptions raised inside a tool are returned to the agent as a tool error message (`handle_tool_errors=True`) instead of aborting the whole run on langgraph ≥ 1.0.
  - The orchestrator's own token usage is now counted in `run_metrics.json` (`with_structured_output(..., include_raw=True)`).
  - `post_plan` HITL overrides match exact persona names (comma-separated) instead of substrings; free text is appended to the instructions as `USER REDIRECT` instead of being dropped.
  - Specialists in the root template and all example packs now have the `append_traceability` tool they were already being instructed to call; the instruction is only injected for agents that actually have the tool.
  - Traceability-matrix rows escape `|` and newlines so a logged fact can no longer break the table.
  - `search_pubmed` HTTP calls have a 20 s timeout.
  - The orchestrator receives compressed findings (default 1500 characters per specialist) instead of every finding verbatim on every routing call.
  - On revision the Journalist sees its rejected draft under `PREVIOUS DRAFT` instead of disguised as a specialist finding.
  - Ignored routing targets and deferred Journalist dispatches are noted in the orchestrator's progress message.
  - An explicit LangGraph `recursion_limit` is derived from `max_agent_calls` and `max_revision_loops`.

- **Orchestration robustness** (audit findings B4, B5, B13–B15, R1, R3):
  - HITL checkpoints are separate nodes that make no LLM calls, so resuming after `post_plan` or `on_rejection` runs exactly the plan or verdict you answered. Previously the routing or reviewer call was replayed on resume and could produce a different result.
  - The `pre_journalist` framing and the orchestrator's instructions now reach the Journalist, on every draft. They were silently dropped before.
  - A specialist or Journalist that is still calling tools when `max_tool_rounds_per_agent` runs out gets one final call without tools and reports what it found, instead of returning empty text.
  - An empty draft is rejected without a reviewer call; if it stays empty the run reports failure instead of "Task complete".
  - A model error in one agent no longer aborts the run: the failure is recorded and the other agents' findings are kept. An unusable routing reply is retried once.
  - A specialist dispatched twice keeps its earlier findings; duplicate targets in one decision are dispatched once.
  - Builder commands (`persona add`, `team configure`, …) no longer drop config keys they do not model, such as `output_sections`, `reviewer.custom_prompt` or a cross-provider `reviewer.model.provider`.
  - Message content returned as a list of blocks (Anthropic-style) is handled by the reviewer and all agents.

### Added
- **`swarm.run_log`** (default `false`): a JSON Lines record of every node update — routing decision and reasoning, assignments, reviewer verdict and feedback, HITL answers, token deltas and `final_status` — plus run start and end, including crashes (audit R6).
- **`swarm.persist_findings`** (default `false`): each specialist's findings are written to `<output_dir>/findings/<persona-slug>.md` as soon as it reports, so a crash before the Journalist keeps them (audit I5).
- **`orchestrator.on_agent_failure`**: `continue` (default) records a failed agent and keeps going; `abort` stops on the first error.
- `execute` and `report` print whether the draft was approved, force-approved after `reviewer.max_revision_loops`, or overridden by the user, and exit with code 1 when no draft was produced.
- **`journalist.brief`**: the Journalist's task instructions are now a Jinja2 template in `swarm_config.yml` (audit finding B2). The runtime previously hard-coded a psychiatry-specific requirement ("distinguish high engagement from clinical impairment") and two tool names for every domain. The default brief lists `reviewer.required_elements`; the toxicology and HCM examples render their `output_sections` lists, and the PIU example keeps the engagement-vs-impairment rule. `swarm doctor` renders the template and reports errors; the builder round-trips the key.
- `orchestrator.max_findings_chars` and `orchestrator.max_peer_chars` (documented as commented defaults in `swarm_config.yml`).
- `model.pricing: {input_per_m, output_per_m}` for the cost estimate in `run_metrics.json`; defaults stay at gpt-4o list prices.
- `swarm doctor` warns when a specialist with search tools has no traceability tool.
- `tests/test_orchestration.py`: offline control-flow tests for the supervisor loop; remaining audit findings are strict `xfail` tests.
- `docs/dev/ORCHESTRATION_AUDIT.md`: verified audit of the orchestration layer with ranked recommendations.

### Changed
- **Renamed the project from Research Swarm to Murmuration.** The Python package is now `murmuration` (`pip show murmuration`) and HTTP tools send `User-Agent: Murmuration/1.0`. The CLI (`swarm`), import package (`automation`) and `swarm_config.yml` schema are unchanged.
- Repository moved from `dhuzard/metodax` (METODAX) to `Neuronautix/murmuration`. Project URLs in `pyproject.toml`, `CITATION.cff`, README, QUICKSTART, CONTRIBUTING and FAQ now point to the new location; they previously pointed to `dhuzard/piu-psych-swarm`.

---

## [0.2.0] — 2026-04-09

### Added
- **Interactive swarm builder** (Phases 1–8): `swarm init`, `persona add/edit`, `team configure`, `review configure`, `model configure`, `metadata configure`, `tools configure/add/edit/remove`, `blueprint export/import`, `doctor`, `preview`
- **Async tool execution**: concurrent tool dispatch via `asyncio.gather` within specialist nodes
- **Human-in-the-Loop (HITL)**: four checkpoints (`pre_flight`, `post_plan`, `pre_journalist`, `on_rejection`) via LangGraph `interrupt()`
- **Token and cost tracking**: per-run metrics written to `Drafts/run_metrics.json`
- **Cross-provider Reviewer-2**: reviewer model can be configured independently of research agents for adversarial diversity
- **Portable blueprints**: export/import swarm configurations as standalone YAML blueprint files
- **`search_semantic_scholar` tool**: indexes preprints, arXiv, and CHI papers not yet on PubMed
- **`you_research` tool**: You.com Research API multi-step synthesis with inline citations
- **`lookup_doi` tool**: full citation metadata via Europe PMC and Crossref (no API key required)
- **`trace_literature_network` tool**: expand a seed paper through its reference list and lead authors
- **Per-agent tool scoping**: each persona gets only its configured tool subset
- **Named starter blueprints**: `research-core`, `literature-mapping`, `intervention-lab`, `rapid-brief`
- **`doctor --fix`**: auto-repair of missing KB directories and `.gitkeep` files
- **Semantic validation in `doctor`**: duplicate roles, routing consistency, empty KB warnings

### Changed
- Rewritten as true supervisor multi-agent pattern (each persona is a separate LangGraph node)
- Full `persona.md` content now injected per-agent (previously truncated at 300 chars)
- `search_pubmed` upgraded from `esummary` (title only) to `efetch` (full abstracts)
- `scrape_webpage` upgraded to You.com Contents API with BeautifulSoup fallback
- `git_commit_snapshot` now uses explicit safe path staging (only `Drafts/` and traceability matrix)
- Reviewer model configurable separately from research agents

### Fixed
- `_run_tool_loop` message accumulation bug: prior context was discarded after first tool call
- `search_you_engine` base URL updated to current API endpoint
- `write_manuscript_section` now appends version suffix (`_v2`, `_v3`) instead of silently overwriting

---

## [0.1.0] — 2024

### Added
- Initial multi-agent research swarm built on LangGraph
- PubMed search, web search (You.com), and KB vectorization (ChromaDB)
- `swarm_config.yml` configuration surface
- Persona system with per-agent KB folders
- Reviewer-2 adversarial checkpoint
- Makefile and Docker Compose setup
- Support for OpenAI, Anthropic, and Google model providers
