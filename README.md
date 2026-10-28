# Murmuration

> *A murmuration is a flock of starlings that moves as one, with no leader, each bird following a few simple rules. Here, the birds are research agents and the rules live in `swarm_config.yml`.*

> Repository: [Neuronautix/murmuration](https://github.com/Neuronautix/murmuration). Formerly published as **METODAX** (`dhuzard/metodax`) and **Research Swarm**; old GitHub URLs redirect here.

**Murmuration automatically conducts literature reviews and writes research documents.**

Give it a research question. It deploys a team of AI agents that search PubMed, read papers, debate findings across specialisms, and write a structured document with full citations — no programming required.

> **What you get:** A formatted Markdown document — suitable as a draft for a literature review, evidence brief, grant background, or manuscript section — saved to the `Drafts/` folder with a complete References section and an evidence audit trail.

**Example research questions it handles well:**
- *"Review the epidemiological literature on burnout in emergency physicians, 2015–2025."*
- *"Summarize the evidence for digital CBT interventions in adolescent depression."*
- *"Write a grant background section on air quality and cognitive development in children."*
- *"Map the literature on antibiotic resistance in community-acquired pneumonia."*

> **Not a coder?** Start with [QUICKSTART.md](QUICKSTART.md) → it takes about 20 minutes to get your first result. See [FAQ.md](FAQ.md) for common questions including cost estimates.

---

## How It Works

Murmuration coordinates a team of specialist AI agents through a supervised graph. Each agent has a focused role, a private knowledge base, and access to a curated set of research tools. An orchestrator routes sub-questions to the right specialists, a Reviewer-2 checkpoint validates outputs against configurable quality standards, and a Journalist agent writes the final structured document.

The entire team is configured through a single YAML file — `swarm_config.yml`. You never need to modify Python code to deploy a new research domain.

**Cost:** A typical research query costs **$1–$5** using GPT-4o. Complex multi-topic synthesis costs $5–$15. See [FAQ.md](FAQ.md) for detailed cost guidance and how to set spending limits.

**Built-in tools:**

| Tool | Purpose |
| :--- | :--- |
| `search_pubmed` | Search peer-reviewed biomedical and scientific literature |
| `search_semantic_scholar` | Search preprints, CHI papers, and recent publications |
| `trace_literature_network` | Expand a seed paper via its references and lead authors |
| `search_knowledge_base` | Search local vectorized KB documents in agents/*/KB/ |
| `scrape_webpage` | Pull full-text content from URLs |
| `you_research` | Multi-step web synthesis with inline citations |
| `lookup_doi` | Full citation metadata via Europe PMC and Crossref (no API key) |
| `append_traceability_matrix` | Log evidence and epistemic status to audit trail |
| `write_manuscript_section` | Write markdown outputs into Drafts/ |
| `git_commit_snapshot` | Version-control swarm outputs for reproducibility |

---

## Quickstart

### Linux or Windows WSL

Requirements: Python 3.10+, `python3-venv`, `make`

```bash
git clone https://github.com/Neuronautix/murmuration.git
cd murmuration
make setup
```

On Ubuntu or WSL, install prerequisites first if needed:

```bash
sudo apt update && sudo apt install -y python3 python3-venv make
```

### Native Windows (PowerShell)

```powershell
git clone https://github.com/Neuronautix/murmuration.git
cd murmuration
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -e ".[dev]"
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

### Add API Keys

Copy `.env.example` to `.env` and add your keys:

```env
OPENAI_API_KEY=sk-your-key-here
YOU_API_KEY=your-you-dot-com-key-here   # optional — enables live web search
```

### Build the Knowledge Base and Run

```bash
make ingest    # vectorize KB documents
make info      # inspect the active team
make run PROMPT="Your research question here"
```

See [QUICKSTART.md](QUICKSTART.md) for the full step-by-step setup guide.

---

## Create Your Own Swarm

### Option 1: Interactive Builder

```bash
python -m automation.main init
```

The wizard guides you through naming your swarm, defining specialist personas, configuring tools, and setting reviewer constraints.

### Option 2: Edit the Template Directly

Open `swarm_config.yml` and follow the inline comments:

1. Set the swarm name and domain description.
2. Rename the personas to match your team (e.g., rename `Specialist1` to `EpiScope` or `PolicyAnalyst`).
3. Rename the corresponding folder in `agents/` to match.
4. Edit each `agents/*/persona.md` to define the agent's focus and behavior rules.
5. Drop literature documents into `agents/*/KB/` and run `make ingest`.
6. Customize the reviewer constraints in `swarm_config.yml` for your domain's standards.

### Option 3: Start from a Blueprint

```bash
# List available starter blueprints
python -m automation.main blueprints

# Initialize from a blueprint
python -m automation.main init --no-interactive --template literature-mapping --domain "Climate Science" --name "Climate Science Swarm"
```

Available blueprints: `research-core`, `literature-mapping`, `intervention-lab`, `rapid-brief`

---

## Team Structure

The default template ships with five generic personas. Rename and customize them for your domain:

| Agent | Icon | Default Role |
| :--- | :--- | :--- |
| Orchestrator | 👑 | Coordinates the team, decomposes tasks, synthesizes findings |
| Specialist1 | 🔬 | First domain specialist — configure for your primary expertise area |
| Specialist2 | 📊 | Second domain specialist — configure for your secondary expertise area |
| LitScout | 📚 | Citation chaining, landmark-paper identification, author-trail expansion |
| Journalist | ✍️ | Neutral, structured final output and documentation |

See `agents/*/persona.md` for each agent's instructions, and `agents/how_to_enrich.md` for a guide on populating knowledge bases.

---

## Swarm Builder Commands

```bash
python -m automation.main init                  # create a new swarm interactively
python -m automation.main blueprints            # list available blueprints
python -m automation.main preview               # inspect the active team and tool map
python -m automation.main doctor                # validate config, files, KB, and tool wiring
python -m automation.main doctor --fix          # auto-repair safe filesystem issues
python -m automation.main persona add           # add a persona interactively
python -m automation.main persona edit          # edit an existing persona
python -m automation.main team configure        # set orchestrator, routing, and HITL
python -m automation.main review configure      # set reviewer rules, bans, and required elements
python -m automation.main model configure       # change provider, model name, temperature
python -m automation.main metadata configure    # change swarm name and description
python -m automation.main tools configure       # curate the tool registry
python -m automation.main tools add             # add a tool entry
python -m automation.main blueprint export      # save current swarm as a portable blueprint
python -m automation.main blueprint import FILE # generate a swarm from a blueprint file
```

---

## Report Modes

Use `python -m automation.main report` to get structured output in a specific format:

```bash
python -m automation.main report "Your research question" --mode scoping-review
python -m automation.main report "Your research question" --mode evidence-brief
python -m automation.main report "Your research question" --mode grant-support
python -m automation.main report "Your research question" --mode manuscript-draft
```

Available modes: `scoping-review`, `narrative-review`, `evidence-brief`, `manuscript-draft`, `grant-support`, `journalistic-brief`, `pediatric-screen`, `young-adult-screen`, `general-adult-screen`

---

## Reviewer-2 Adversarial Checkpoint

Every swarm run passes through a Reviewer-2 checkpoint before final output. The reviewer checks for banned words, required structural elements, and domain-specific rejection patterns. Configure it in `swarm_config.yml` under `reviewer:`.

For maximum adversarial diversity, set the reviewer to a different model provider than the research agents — a Claude reviewer challenging GPT-4o output (or vice versa) introduces genuinely different training biases. See the commented-out examples in `swarm_config.yml`.

---

## Journalist Brief

The instructions the Journalist receives with the collected findings come from `journalist.brief` in `swarm_config.yml`. It is a Jinja2 template that can read any top-level config key (for example a custom `output_sections` list) plus `required_elements`, `tool_names` and `journalist_name`. When omitted, the default brief lists `reviewer.required_elements` as mandatory bullets and asks the Journalist to save its output with its writing tool. Put domain-specific output requirements here, never in Python; `examples/toxicology/` and `examples/hcm/` show briefs that enforce a fixed section list.

---

## Human-in-the-Loop

Enable HITL checkpoints in `swarm_config.yml` to pause the swarm at key decision points:

```yaml
hitl:
  enabled: true
  checkpoints:
    - pre_flight       # confirm scope before the graph starts
    - post_plan        # approve the orchestrator's routing decision
    - pre_journalist   # add framing instructions before the Journalist writes
    - on_rejection     # choose REVISE / OVERRIDE when Reviewer-2 rejects
```

At `post_plan`, type `APPROVE`, or exact persona names separated by commas to replace the
plan (for example `LitScout, Journalist`). Anything else you type is passed to the agents
as a `USER REDIRECT` note without changing the plan. Framing typed at `pre_journalist` is
shown to the Journalist on every draft, revisions included.

Each checkpoint is its own graph node and makes no model calls, so answering a checkpoint
never re-runs the orchestrator or the reviewer: what you approve is what runs.

---

## Orchestration Options

All of these keys are optional. Leaving one out keeps the behaviour described in its
"Default" column, so an older `swarm_config.yml` runs exactly as before. `swarm doctor`
and `load_config()` reject invalid values and name the offending key.

| Key | Default | Effect |
| --- | --- | --- |
| `swarm.run_log` | `false` | Path of a JSON Lines file. Each node update adds one line with the node, timestamp, `next_agents`, routing reasoning, assignments, reviewer verdict and feedback, HITL answer, token delta and `final_status`. Run start and end are logged too, including crashes and Ctrl-C. Every line carries a `run_id` (the checkpoint thread id when there is one), so several runs can share a file. Relative paths resolve against the project root. |
| `swarm.persist_findings` | `false` | After each specialist update, write its findings to `<output_dir>/findings/<persona-slug>.md`, so a crash before the Journalist does not lose them. Each file names the run that wrote it; files from earlier runs are not deleted. |
| `orchestrator.allow_tools` | `false` | When `true`, the orchestrator persona runs a bounded tool loop with its own `tools:` (at most `max_tool_rounds_per_agent` rounds, plus one summary call if it is still calling tools) before each routing decision except the safety-valve pass, and its short report is added to the routing prompt. Its tokens count towards the run's usage. When `false`, its tools are not loaded and `swarm doctor` warns that they are ignored. The toxicology, FAIR-NAMs and PIU examples set it to `true`. |
| `orchestrator.max_parallel_agents` | `null` | Maximum number of specialists running their model and tool calls at the same time when the orchestrator dispatches several in parallel. The others wait; none is skipped, and `max_agent_calls` counts them as before. Use it to stay under provider rate limits. |
| `orchestrator.early_stop` | `{enabled: false, min_rounds: 1}` | When enabled, the orchestrator is asked to set `sufficient_evidence` and describe `coverage` once the findings answer the task. After at least `min_rounds` specialist rounds, that sends the run straight to the Journalist, which receives the coverage note. |
| `orchestrator.max_findings_chars` / `max_peer_chars` | `1500` / `600` | How much of each specialist's findings the orchestrator and the other specialists see. The view ends at the last paragraph break, sentence end or space within the limit (never less than half of it), not mid-word. The Journalist always reads the full text. |
| `personas[].model` | none | A `{provider, name, temperature, env_key}` block on any persona, orchestrator and Journalist included, merged over `model` the way `reviewer.model` is. Personas with the same merged spec share one client. `execute` checks that each persona's key is set. `env_key` only names the variable to check (the client reads its provider's standard variable); switching `provider` without `env_key` checks that provider's standard variable. The cost estimate still prices every token with `model.pricing`. |
| `checkpointer` | `{type: none}`, or `{type: memory}` when HITL is on | Where the run state is saved after every step. `sqlite` (with optional `path`, default `./.murmuration/checkpoints.sqlite`, kept out of `output_dir` because `git_commit_snapshot` commits that folder) keeps it in a file so a run can be resumed from a new process; it needs `pip install 'murmuration[persist]'`. `none` cannot be combined with HITL. |
| `reviewer.on_rejection` | `journalist` | Where a rejected draft goes. `journalist`: the Journalist revises alone. `orchestrator`: the orchestrator first gathers more evidence (its routing prompt gets a `REVIEWER REQUESTS` block listing each rejected claim and the suggested specialist), then the Journalist revises. `auto`: the orchestrator only when the reviewer reports missing evidence. With `orchestrator` or `auto` the reviewer answers in a structured form (`verdict`, `style_issues`, `missing_evidence`); an unparseable reply falls back to the `APPROVED` / `REJECTED:` text protocol, so `reviewer.custom_prompt` keeps working. |
| `reviewer.max_evidence_loops` | `1` | How many reviewer-triggered orchestrator rounds a run may have. Specialist work stays bounded by `max_agent_calls`. |
| `reviewer.on_max_revisions` | `flag` | After `max_revision_loops` rejections: `flag` force-approves the next draft and `execute` warns (previous behaviour); `fail` reviews it once more and, if it is still rejected, ends the run with `final_status: failed` and exit code 1. |
| `reviewer.context` | `[draft]` | What the reviewer reads: add `task` (the research question) and/or `matrix` (this run's traceability-matrix rows, to check that cited claims were logged). |
| `reviewer.matrix_excerpt_rows` | `50` | How many of this run's matrix rows the reviewer sees with `context: matrix` (the most recent ones). |
| `collaboration.structured_findings` | `false` | After its research each specialist makes one more call that restates its report as claims (`text`, `epistemic_tag` from `epistemic_tags`, `sources`, `confidence`, `contradicts`). Claims get ids such as `litscout.2.3`, are written to the traceability matrix under the persona's name, and replace the text slice in the orchestrator's and colleagues' views (within the same `max_findings_chars` / `max_peer_chars` budgets). The Journalist reads the full reports and is asked to cite claim ids; with `reviewer.context` including `matrix`, the reviewer gets a check of the cited ids. An unparseable reply keeps the text report. |
| `collaboration.surface_disagreements` | `false` | Lists claim pairs linked by `contradicts` in the orchestrator's prompt and asks the Journalist for a "Points of disagreement" section. Needs `structured_findings`. |
| `collaboration.critique_round` | `false` | Before the first hand-over to the Journalist, each specialist with claims gets one pass that asks only for objections to its colleagues' claims. It runs once, counts towards `max_agent_calls` (keeping one call back so the orchestrator can read the objections), and is how specialists dispatched in parallel get to dispute each other's claims. Needs `structured_findings`. |

If a run log or findings file cannot be written (locked file, full disk), the run continues
and that output is switched off with a warning.

### Resuming a run

With `checkpointer: {type: sqlite}`, `execute` and `report` print a thread id when the run
starts. If the run crashes, is stopped with Ctrl-C, or is left waiting at a HITL question,
continue it with:

```bash
python -m automation.main execute --resume <thread_id>
```

Steps that finished before the crash are not re-run; the step that failed runs again. A
pending HITL question is asked again. The saved task is used, so no prompt is needed.

Every row the `append_traceability` tool writes to the traceability matrix names the
persona that logged it in the Author/Agent column.

---

## Repository Layout

```
murmuration/
├── swarm_config.yml          # Active swarm definition — edit this to customize
├── agents/                   # Personas and local knowledge bases
│   ├── Orchestrator/
│   │   ├── persona.md        # Orchestrator instructions
│   │   └── KB/               # Drop shared bibliographies and scope notes here
│   ├── Specialist1/          # Rename to match your domain
│   ├── Specialist2/          # Rename to match your domain
│   ├── LitScout/             # Literature mapping specialist
│   ├── Journalist/           # Output writer
│   └── how_to_enrich.md      # Guide for populating KB folders
├── automation/               # Framework code — no edits needed for customization
│   ├── graph.py              # LangGraph multi-agent state machine
│   ├── config.py             # YAML config loader and validator
│   ├── tools.py              # All built-in tool implementations
│   ├── ingest.py             # KB vectorization (ChromaDB)
│   ├── main.py               # CLI entry point (Typer)
│   └── builder/              # Interactive swarm builder
├── examples/                 # Reference implementations for specific domains
│   ├── piu_psychiatry/       # Psychiatric research on problematic internet use
│   ├── toxicology/           # TOCSAS preclinical toxicology metadata squad
│   └── fair_nams/            # FAIR data, 3Rs and NAMs regulatory squad
├── Drafts/                   # Swarm outputs and generated documents
├── Knowledge_Traceability_Matrix.md  # Running evidence audit trail (auto-generated)
├── pyproject.toml
├── Makefile
├── docker-compose.yml
└── .env.example
```

---

## Examples

See the `examples/` folder for complete, ready-to-use swarm configurations:

- **`examples/piu_psychiatry/`** — A complete six-specialist psychiatric research team for problematic internet use (PIU), with pre-populated knowledge bases, curated prompt sets, and domain-specific reviewer constraints. See its [README](examples/piu_psychiatry/README.md).
- **`examples/toxicology/`** — TOCSAS, a nine-persona preclinical toxicology team for MNMS metadata mapping, conservative adversity interpretation and knowledge-graph design, with a draft toxicology extension schema. See its [README](examples/toxicology/README.md).
- **`examples/fair_nams/`** — FAIR-NAMs-Squad, a five-persona team for 3Rs/NAMs regulatory context, FAIR semantics and implementation architecture. KB documents are listed as sources, not shipped. See its [README](examples/fair_nams/README.md).
- `examples/climate_science/` — *(configuration stub — full agents/ and README coming soon)*
- `examples/financial_modeling/` — *(configuration stub — full agents/ and README coming soon)*
- `examples/legal_tech/` — *(configuration stub — full agents/ and README coming soon)*

To deploy the PIU psychiatry example:

```bash
cp examples/piu_psychiatry/swarm_config.yml .
cp -r examples/piu_psychiatry/agents .
make ingest
make run PROMPT="Your first question"
```

To contribute a complete example for another domain, see [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Tips for Effective Swarms

See [TIPS.md](TIPS.md) for a detailed guide on:
- Designing a well-scoped specialist team
- Pre-loading knowledge bases with the right documents
- Seeding literature searches with key authors and landmark papers
- Configuring the reviewer for your domain's epistemological standards
- Getting the most out of the literature mapping tools

---

## Documentation Index

| Document | Purpose |
| :--- | :--- |
| [QUICKSTART.md](QUICKSTART.md) | Step-by-step setup guide including Windows PowerShell |
| [FAQ.md](FAQ.md) | Common questions — cost, output quality, citations, API keys |
| [TIPS.md](TIPS.md) | Best practices for team design, KB seeding, and reviewer configuration |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to report bugs, share domain examples, or contribute code |
| [CHANGELOG.md](CHANGELOG.md) | Version history |
| [CLAUDE.md](CLAUDE.md) | Conventions for AI-assisted development on this codebase |
| [docs/dev/](docs/dev/) | Internal planning documents (builder architecture, improvement tracker) |

---

## License

MIT — see [LICENSE](LICENSE) for details.

## Contributing

Issues, bug reports, and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.
