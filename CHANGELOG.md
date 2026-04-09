# Changelog

All notable changes to this project are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
