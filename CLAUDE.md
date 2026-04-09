# CLAUDE.md — Guidance for Claude Code on this Repository

## Project Purpose

Research Swarm is a **configuration-driven** multi-agent research framework built on LangGraph.
The core design invariant is: **a user should never need to modify Python code to customize swarm behavior.**
All customization flows through `swarm_config.yml` and `agents/*/persona.md`.

---

## Architecture Overview

```
swarm_config.yml          ← Single source of truth for all runtime behavior
agents/*/persona.md       ← Agent instructions (system prompts)
agents/*/KB/              ← Per-agent vectorized knowledge base documents
automation/graph.py       ← LangGraph state machine (supervisor pattern)
automation/config.py      ← YAML loader + validator + system prompt builder
automation/tools.py       ← All @tool-decorated functions
automation/ingest.py      ← KB vectorization (ChromaDB)
automation/main.py        ← Typer CLI entry point
automation/builder/       ← Interactive swarm builder (wizard, generator, doctor)
examples/                 ← Reference domain implementations (do not modify root config)
```

---

## What Claude Must NOT Do

- **Do not modify `automation/graph.py`, `automation/config.py`, `automation/tools.py`, or `automation/ingest.py`** to add domain-specific behavior. Any domain customization must go through `swarm_config.yml` or `agents/*/persona.md`.
- **Do not hard-code persona names, tool names, or model names in Python code.** All of these are resolved dynamically from `swarm_config.yml` at runtime.
- **Do not commit `.env` files.** `.env.example` is the only secrets template that should be tracked.
- **Do not run `git add -A` or `git add .`.** The `git_commit_snapshot` tool in `tools.py` explicitly stages only safe paths (`Drafts/` and the traceability matrix). Unrestricted `git add` risks staging `.env`, credentials, or binary data.
- **Do not modify `swarm_config.yml` in the repo root when working on an example.** Examples under `examples/*/` have their own independent `swarm_config.yml`.
- **Do not add `langchain-huggingface` or `langchain-chroma` to `pyproject.toml` `[dependencies]`.** These are used via graceful try/except fallback in `ingest.py` and `tools.py` — if unavailable, the code falls back to the equivalent `langchain_community` classes. Adding them as hard dependencies would break installations on environments that can't build them. `langchain-text-splitters` IS a hard dependency (direct import in `ingest.py`) and IS in `pyproject.toml`.
- **Do not use `asyncio.run()` inside tool functions.** Tool functions execute inside an already-running async event loop via `asyncio.gather` in `graph.py`. Use synchronous `requests` calls inside tool functions.

---

## Running the Project

```bash
# Prerequisites: Python 3.10+, OPENAI_API_KEY in .env
make setup          # creates .venv, installs deps, copies .env.example
make ingest         # vectorizes agents/*/KB/ documents into ChromaDB
make info           # shows active team, tool map, and reviewer config
make run PROMPT="Your research question"
make doctor         # validate config, file structure, KB, and tool wiring
```

Windows PowerShell (no Makefile):
```powershell
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
Copy-Item .env.example .env   # then edit .env with your keys
.\.venv\Scripts\python -m automation.ingest
.\.venv\Scripts\python -m automation.main execute "Your research question"
```

---

## Testing

A minimal test suite exists in `tests/`. Run with `make test`.

- Tests must not require real API keys — use `unittest.mock` to patch `requests.get` and LLM calls.
- New tools should have a corresponding test in `tests/test_tools.py`.
- New config-loading behavior should be tested in `tests/test_config.py`.
- `pytest` is in `[project.optional-dependencies] dev` and installed by `make setup`.

## Planning Documents

Internal development planning documents live in `docs/dev/` and are not user-facing:
- `docs/dev/INTERACTIVE_SWARM_BUILDER_PLAN.md` — builder architecture and roadmap
- `docs/dev/Future_Improvements_TODO.md` — completed and pending improvements tracker

Do not modify these files unless the user explicitly asks. Do not move them back to the repo root.

---

## Key Conventions

### swarm_config.yml

- All persona names in `personas:` must match folder names in `agents/`.
- The `orchestrator.agent` value must exist in the `personas:` list.
- The `orchestrator.journalist` value must exist in the `personas:` list.
- Every tool key listed in a persona's `tools:` list must have an entry in the top-level `tools:` section.
- Validate changes with `python -m automation.main doctor`.

### Persona files (`agents/*/persona.md`)

- Persona files are injected verbatim as the agent's system prompt.
- They should define: Core Mission, Knowledge Base Focus, and Behavior rules.
- Behavior rules should include explicit **Search Triggers** and **Reporting Rules**.
- Do not add confidential information to persona files — they are checked into git.

### Tool functions (`automation/tools.py`)

- All tool functions must be decorated with `@tool` from `langchain_core.tools`.
- Tool functions must be synchronous (not `async def`). The graph handles concurrency.
- Tool functions must return a `str` — the string is injected into the agent's context.
- Register new tools in `swarm_config.yml` under `tools:` before assigning them to personas.
- HTTP requests should include a `User-Agent: ResearchSwarm/1.0 (Academic Research)` header.

### Config loading (`automation/config.py`)

- `load_config()` is the single entrypoint for reading `swarm_config.yml`.
- It raises `ValueError` for missing required fields and `FileNotFoundError` for missing files.
- Do not parse `swarm_config.yml` directly with `yaml.safe_load()` outside of `config.py`.

### Builder (`automation/builder/`)

- `models.py` → Pydantic specs (source of truth for structure)
- `generator.py` → writes files to disk (always previews diffs before writing unless `force=True`)
- `wizard.py` → interactive prompts, calls generator
- `doctor.py` → validation and safe repair
- All builder operations must go through `models.py` → `generator.py` — never write raw YAML strings directly to `swarm_config.yml`.

---

## Adding a New Built-in Tool

1. Write a synchronous function in `automation/tools.py` decorated with `@tool`.
2. Add the tool to the `tools:` section of `swarm_config.yml` template.
3. Assign the tool to relevant example personas in `examples/piu_psychiatry/swarm_config.yml`.
4. Add the tool to `automation/builder/models.py` `BUILTIN_TOOLS` if it should appear in the wizard.
5. Document it in `README.md` under the **Built-in tools** table.

---

## Adding a New Example Domain

1. Create `examples/{domain_name}/` with: `swarm_config.yml`, `agents/*/persona.md`, `README.md`.
2. The example's `swarm_config.yml` must be a complete standalone config (not relative to root).
3. Add the example to `README.md` under the **Examples** section.
4. Optionally add pre-populated KB files under `examples/{domain_name}/agents/*/KB/`.

---

## Style and Formatting

- Python style: `ruff` with `line-length = 120` (configured in `pyproject.toml`).
- Run linter with: `ruff check automation/` or `make lint` if that target exists.
- YAML files: 2-space indentation, no tabs.
- Markdown files: no trailing spaces, blank line between sections.

---

## Security Notes

- API keys are loaded via `python-dotenv` from `.env`. They must never appear in code or YAML files.
- The `git_commit_snapshot` tool in `tools.py` stages only `Drafts/` and the configured traceability matrix path — not the whole working tree.
- Do not add the `.env` file or any file containing real credentials to `.gitignore` exceptions.
- The `scrape_webpage` tool fetches arbitrary URLs — do not pass untrusted URLs from user-facing interfaces without sanitization.
