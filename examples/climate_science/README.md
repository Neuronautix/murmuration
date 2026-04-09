# Climate Science Example

A ready-to-run Research Swarm configuration for climate science research and
policy analysis.

## Team

| Agent | Role |
|-------|------|
| 🌍 Principal Investigator | Orchestrator — plans research, synthesises findings |
| 📊 Data Analyst | Statistical modelling, dataset retrieval, uncertainty quantification |
| 📜 Policy Advisor | IPCC frameworks, COP outcomes, national climate legislation |
| ✍️ Technical Writer | Journalist — assembles the final report |

## Quick Start

```bash
# From the repo root:
cp examples/climate_science/swarm_config.yml swarm_config.yml
cp -r examples/climate_science/agents ./agents

# Install and run
make setup
make ingest   # optional: load KB documents first
make run PROMPT="What are the latest findings on Arctic sea-ice decline and
  its policy implications for the Paris Agreement targets?"
```

## Customising

1. **Persona prompts** — edit `agents/*/persona.md` to sharpen each agent's
   focus (e.g. target a specific IPCC working group or a national context).
2. **Knowledge Base** — drop PDFs or text files into `agents/*/KB/` and run
   `make ingest` to vectorise them.
3. **Reviewer constraints** — adjust `reviewer.banned_words` and
   `reviewer.required_elements` in `swarm_config.yml`.
4. **Epistemic tags** — the defaults (`[OBSERVED]`, `[MODELED]`, `[PROJECTED]`,
   `[INFERENCE]`) match IPCC vocabulary; customise as needed.

## Example Prompts

- *"Summarise the IPCC AR6 synthesis report findings on 1.5°C vs 2°C pathways."*
- *"What carbon pricing mechanisms have been most effective, and what does
  the literature say about their optimal design?"*
- *"Review the scientific evidence on solar geoengineering risks and governance
  frameworks proposed since 2020."*
