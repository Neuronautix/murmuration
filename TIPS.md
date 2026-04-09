# Tips for Building an Effective Research Swarm

This guide covers the practical decisions that most affect swarm output quality: team design, knowledge base seeding, reviewer configuration, and literature search strategy.

---

## 1. Team Design

### Start small and focused

A 3-agent swarm (Orchestrator + one Specialist + Journalist) often outperforms a 7-agent swarm where roles overlap. Each additional specialist adds cost and coordination overhead. Add specialists only when you have a genuine knowledge boundary that one agent cannot straddle.

**Minimum viable team:**
```
Orchestrator → Specialist → Journalist
```

**Good 4-agent team for a focused domain:**
```
Orchestrator → EvidenceSpecialist + LitScout → Journalist
```

**Full team for a multi-disciplinary review (e.g., clinical + epidemiology + mechanisms + interventions):**
```
Orchestrator → Specialist1 + Specialist2 + Specialist3 + LitScout → Journalist
```

### Write sharp persona files

The most impactful single change you can make is writing a precise `persona.md`. The three elements that matter most:

1. **Core Mission** — one or two sentences that define what this agent is for and what it is *not* for. Explicit exclusions matter: "You do NOT assess intervention efficacy — that is CarePath's role."
2. **Search Triggers** — explicit rules for *when* to call which tool. Agents that always search everything are noisy; agents with clear triggers are efficient.
3. **Reporting Rules** — domain-specific standards for how to qualify claims (e.g., "always state the instrument name and cut-off score when reporting a prevalence figure").

### Assign tools deliberately

Give each agent only the tools it actually needs. An orchestrator rarely needs `scrape_page`; a literature mapper rarely needs `you_research`. Over-tooled agents make poor tool choices and waste tokens.

### Use the Journalist as a non-negotiable quality gate

The Journalist should receive *all* agent findings before writing. Its persona.md should include explicit rules for what to carry forward unchanged (caveats, confidence qualifications) and what to push back on (missing citations, unsupported causal claims).

---

## 2. Knowledge Base Seeding

Pre-loading the right documents into `agents/*/KB/` is the single best way to improve output quality without changing the prompt. Agents with a strong local KB make fewer speculative claims and produce more accurate citations.

### What to put in the KB

| Document type | Effect |
| :--- | :--- |
| Landmark review or meta-analysis | Gives the agent a reliable anchor for evidence quality and prevalence estimates |
| Consensus statement or clinical guideline | Sets the authoritative standard against which the agent evaluates new claims |
| Construct disambiguation note | Prevents agents from collapsing related-but-distinct concepts (critical in fields with contested terminology) |
| Measurement instrument manual or psychometric validation paper | Allows the agent to evaluate whether instrument use in a study is appropriate |
| Scope note (written by you) | Defines the project boundaries — what is in scope, what is out, and known controversies to address |

### How to write a good KB scope note

Drop a `00_scope_and_goals.md` file into `agents/Orchestrator/KB/` that answers:
- What is the primary research question?
- What is the target population or domain boundary?
- What are the known construct controversies the team must address?
- What output format does the project need?

This file gets vectorized and retrieved when the orchestrator plans its decomposition, steering the whole run.

### KB file hygiene

- Keep files under 20 pages — very long documents dilute retrieval precision.
- Use descriptive filenames: `cbt_meta_analysis_march_2024.md` beats `notes.md`.
- Start each file with a one-paragraph summary of why it is in this agent's KB.
- After adding files, always re-run `make ingest`.

---

## 3. Literature Search Strategy

### Pre-load seed authors and papers for deep search

The `trace_literature_network` tool is most powerful when given a strong seed paper. Prepare a short list of 3–5 landmark authors and their most cited papers before running a major synthesis task. Drop this list into `agents/LitScout/KB/` as a Markdown file.

**Example KB seed file format (`landmark_authors_and_papers.md`):**

```markdown
# Landmark Authors and Papers for [Your Domain]

## Foundational Reviews
- Smith et al. (2020) — "Title of the landmark review" — DOI: 10.xxxx/xxxxx
- Jones & Lee (2019) — "Another foundational paper" — DOI: 10.xxxx/xxxxx

## Key Authors to Trace
- Prof. Jane Smith (University of X) — senior author on most prevalence meta-analyses
- Dr. Carlos Müller — key contributor to measurement instrument validation
- Dr. Aiko Tanaka — leading researcher on mechanisms

## Search Keywords
- "[Primary construct]" AND "systematic review"
- "[Construct]" AND "meta-analysis" AND "prevalence"
- "[Construct]" AND "[population]" AND "intervention"
```

### Suggested seeding strategy by domain type

**Clinical / psychiatric domains:**
- Start with the most recent large-scale systematic review or meta-analysis (preferably published in a high-impact journal within the last 5 years).
- Trace the reference lists of 2–3 consensus or guideline papers.
- Identify the 3–5 authors whose names appear most frequently in the methods literature for your key measurement instruments.

**Basic science / mechanistic domains:**
- Start with review papers on the dominant theoretical model (e.g., the leading mechanistic framework in your field).
- Trace citation networks from foundational empirical papers (often older, highly cited papers from the period when the field crystallized).
- Include 1–2 critical or replication failure papers — agents need to know what hasn't replicated.

**Policy / intervention domains:**
- Start with the most recent systematic review of intervention efficacy.
- Include any clinical guidelines or national/international policy documents.
- Add papers on implementation fidelity and real-world effectiveness, not just RCT efficacy.

**Epidemiological / prevalence domains:**
- Start with the most comprehensive global or multi-country meta-analysis.
- Include papers that compare measurement instruments — agents frequently encounter instrument heterogeneity and need to flag it.
- Add papers on risk and protective factors so the swarm can contextualize prevalence figures.

### High-value PubMed search strategies to give your agents

Paste these types of queries directly into a research prompt or drop them as KB notes:

```
"[your construct]"[tiab] AND "systematic review"[pt] AND ("2018"[dp]:"2025"[dp])
"[your construct]"[tiab] AND "meta-analysis"[pt]
"[your construct]"[tiab] AND "prevalence"[tiab] AND "adolescent"[tiab]
```

For grey literature and preprints, the `you_research` tool is more effective than PubMed — use it for very recent work (last 12–18 months) and for practice guidelines that are not indexed on PubMed.

---

## 4. Reviewer Configuration

The Reviewer-2 checkpoint is an adversarial agent that reads the Journalist's output and rejects it if quality thresholds are not met. Configuring it well prevents the most common failure modes for your domain.

### Generic required elements that work well across most domains

```yaml
required_elements:
  - "a short limitations section"
  - "in-text citations (e.g., [1], [2])"
  - "a formal 'References' section at the bottom"
  - "at least one caveat about generalizability or sample characteristics"
```

### Domain-specific required elements to consider

| Domain | Example required element |
| :--- | :--- |
| Clinical / health | "when citing a prevalence figure, the screening instrument name and cut-off must be stated" |
| Behavioral science | "distinguish between self-reported behavioral frequency and clinically validated impairment" |
| Epidemiology | "state the study design (RCT, cohort, cross-sectional) for every evidence claim" |
| Policy | "distinguish between evidence from high-income settings and LMICs when results may not generalize" |
| Mechanistic science | "distinguish between correlational evidence and evidence for a causal mechanism" |

### Rejection patterns — catch common failures automatically

```yaml
rejection_patterns:
  - "any causal claim not supported by experimental or longitudinal evidence"
  - "any prevalence figure cited without naming the measurement instrument"
  - "use of hyperbolic language about findings ('proves', 'definitively shows', 'conclusively demonstrates')"
```

### Cross-provider reviewer for stronger adversarial critique

Set the Reviewer-2 model to a different provider than your research agents. GPT-4o agents reviewed by Claude (or vice versa) produce noticeably more independent critique than same-model review. See the commented-out examples in `swarm_config.yml`.

```yaml
reviewer:
  model:
    provider: "anthropic"
    name: "claude-opus-4-6"
    temperature: 0.8
    env_key: "ANTHROPIC_API_KEY"
```

---

## 5. Prompt Engineering for Swarm Tasks

### Write prompts as decomposable research briefs, not single questions

Bad prompt:
```
"Tell me about anxiety in adolescents."
```

Good prompt:
```
"Review the epidemiological and clinical literature on anxiety disorders in adolescents (12–18). Cover: 
(1) prevalence estimates from the last 10 years, noting instruments used; 
(2) key risk and protective factors; 
(3) comorbidity patterns (depression, ADHD, substance use); 
(4) evidence-based interventions (CBT, pharmacotherapy, digital self-help); 
(5) gaps in the evidence base and methodological limitations. 
Target output: a narrative review suitable for a clinical journal submission."
```

The orchestrator will route each numbered sub-question to the appropriate specialist. Clear decomposability is the highest-leverage prompt design choice.

### Use HITL checkpoints for high-stakes synthesis tasks

Enable `post_plan` and `pre_journalist` checkpoints when working on grant applications, policy briefs, or peer-reviewed manuscripts. The `post_plan` checkpoint lets you redirect the orchestrator's routing before any specialist work begins — catching scope drift early saves significant token cost.

### Prefer `report` mode for structured outputs

```bash
python -m automation.main report "Your question" --mode evidence-brief
```

Report modes prepend a structured format instruction to the prompt, which consistently improves Journalist output quality and reduces reviewer rejection loops.

---

## 6. Cost and Token Management

- `gpt-4o` at temperature 0.2 is the recommended default — it is accurate and relatively economical.
- Use `max_agent_calls: 4–6` for focused questions; `8–12` for broad multi-topic synthesis.
- Use `max_tool_rounds_per_agent: 3–4` for standard runs; increase to 6–8 only for deep literature mapping tasks.
- Run `make doctor` before a long autonomous run to catch config errors that would cause a failed run at cost.
- Check `Drafts/run_metrics.json` after each run to track token usage and cost trends.

---

## Quick Reference Checklist

Before starting a new domain, work through this list:

- [ ] Defined the domain scope and added a scope note to `agents/Orchestrator/KB/`
- [ ] Renamed personas in `swarm_config.yml` and matching folders in `agents/`
- [ ] Written focused `persona.md` for each specialist with explicit search triggers
- [ ] Added 3–5 landmark documents to each specialist's KB folder
- [ ] Added a seed author and paper list to `agents/LitScout/KB/`
- [ ] Run `make ingest` after adding KB documents
- [ ] Configured domain-specific `required_elements` and `rejection_patterns` in the reviewer
- [ ] Run `make doctor` to validate the configuration
- [ ] Tested with a focused prompt before running a broad synthesis task
