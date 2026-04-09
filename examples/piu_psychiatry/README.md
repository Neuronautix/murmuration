# Example: PIU Psychiatric Research Swarm

This folder contains a complete, ready-to-use swarm configuration for **psychiatric research on problematic internet use (PIU)**, including internet gaming disorder (IGD), social media disorder, and smartphone overuse.

It serves as a reference implementation of the Research Swarm framework for a domain that requires high diagnostic caution, careful construct disambiguation, and multi-disciplinary synthesis.

## How to Use This Example

Copy the contents of this folder into the root of a fresh clone of the repository, then run:

```bash
make setup
make ingest
make run PROMPT="Review the psychiatric literature on problematic internet use in adolescents, including prevalence, comorbidity, mechanisms, and interventions."
```

Or, use the builder to import this team's structure as a starting point:

```bash
python -m automation.main blueprint import ./examples/piu_psychiatry/blueprints/PIUPsychTeam.swarm-blueprint.yml
```

## The Team

| Agent | Icon | Role |
| :--- | :--- | :--- |
| Dr. Nexus | 👑 | Orchestrator — coordinates, synthesizes, resolves conflicts |
| ClinicalPsych | 🧠 | Diagnosis, impairment criteria, and comorbidity |
| EpiScope | 📊 | Prevalence, psychometrics, risk and protective factors |
| LitScout | 📚 | Citation chaining, reference mapping, author-trail expansion |
| NeuroCogs | 🧪 | Mechanisms — reward, executive control, neuroimaging |
| CarePath | 🛟 | Interventions, prevention, and care pathways |
| Journalist | ✍️ | Structured output, neutral documentation |

## Key Design Decisions

**Diagnostic caution built into reviewer constraints** — the Reviewer-2 checkpoint explicitly requires distinguishing screening risk from formal disorder, naming instruments and cut-offs for all prevalence figures, and clarifying the ICD-11 vs. DSM-5-TR status of gaming disorder. These constraints prevent the common error of treating heavy use as pathology.

**Separate specialist for mechanisms** — NeuroCogs is kept independent from ClinicalPsych to prevent conflation of neurobiological correlates with diagnostic criteria.

**Cross-provider Reviewer-2** — the reviewer model is set to a different temperature than the research agents; switching it to a different provider (e.g., Claude reviewing GPT-4o output) adds adversarial diversity. See the commented-out examples in `swarm_config.yml`.

## Ready-to-Use Prompts

```bash
# Scoping review
make run PROMPT="Create a scoping review outline for problematic internet use in university students."

# Evidence brief
python -m automation.main report "PIU and depression in adolescents" --mode evidence-brief

# Grant background
python -m automation.main report "Internet gaming disorder — prevalence and interventions" --mode grant-support

# Diagnostic caution focus
make run PROMPT="Compare problematic internet use and internet gaming disorder as psychiatric constructs, including diagnostic cautions."
```

See `Drafts/piu_prompt_set.md` for a curated set of eight ready-to-run prompts covering scoping reviews, evidence briefs, policy questions, and population-specific analyses.

## Knowledge Base

Each specialist has a pre-populated KB folder under `agents/*/KB/`. Drop additional PDFs, Markdown files, or plain-text notes into the relevant folder, then run `make ingest` to vectorize them.

See `agents/how_to_enrich.md` for a guide on which documents belong to which specialist.
