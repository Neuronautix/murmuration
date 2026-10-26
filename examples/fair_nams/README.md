# Example: FAIR-NAMs-Squad

A small team for regulatory toxicology and New Approach Methodologies (NAMs):
ethics and 3Rs, FAIR data and ontologies, and implementation architecture.

## Origin and licence

Ported from `dhuzard/FAIR-NAMs-Squad-fns-` at commit
`940a574a8b1c6a6d197cc6425c0ee91f958d62e9`. The persona files and
configuration were last changed in `23606169b2aa4110575fab0f0cdaad32a4eb85d7`.
The repository owner licensed this content under this repository's MIT licence
on 2026-10-26.

Persona text is not scientifically validated. Treat outputs as drafts for
human review.

## The team

| Agent | Role |
| :--- | :--- |
| Dr. Nexus | Orchestrator & Synthesizer |
| BioEthos | Ethics & Regulatory Officer: 3Rs, Directive 2010/63/EU, virtual control groups |
| Semantica | Ontologist & FAIR Data Expert: RDF/SHACL, SSN, EFO, PROV-O |
| TechLead | Architect & Implementation Lead: Symfony, API Platform, time-series infrastructure |
| Journalist | Observer & Scribe |

## Knowledge bases

The donor's KB folders held PDF/DOCX files and verbatim copies of agency web
pages (FDA, NIH, European Commission). None of them are redistributed here.
Each `agents/*/KB/SOURCES.md` lists the donor files and, where the donor
recorded one, the original URL. Obtain the documents from their sources and
add them locally before running `make ingest`.

## How to use

Persona paths in `swarm_config.yml` are relative to the repository root.
`make ingest` reads KB files from `agents/<folder>/KB/`, so copy the
KB-bearing folders. Do not copy `Journalist`, which would overwrite the root
template's `agents/Journalist`. From the repository root:

```bash
cp examples/fair_nams/swarm_config.yml .
cp -r examples/fair_nams/agents/{DrNexus,BioEthos,Semantica,TechLead} agents/
make doctor
make ingest
make run PROMPT="Summarize the evidence requirements for qualifying virtual control groups in rat dose-range-finding studies."
```

Offline configuration checks are in `tests/test_example_packs.py`.

## Adaptations from the donor

- Added the `orchestrator:` and `hitl:` blocks that the current schema
  requires or expects. The donor's root config predated them.
- Dropped `system_prompt_template`. The current runtime builds per-agent prompts
  from `persona.md` and does not read that key.
- Persona paths now point to `./examples/fair_nams/agents/`. The output
  directory is `./Drafts/FAIR_NAMs` and the matrix is
  `./Knowledge_Traceability_Matrix_FAIR_NAMs.md`.
- BioEthos: corrected "3Rs (Recall, …)" to "3Rs (Replacement, …)", which
  matches the persona's own definition further down.
- Dr. Nexus and Journalist: section headings were restructured into the
  `Core Mission` / `Knowledge Base (KB) Focus` / `Behavior` sections that
  `doctor` checks. The Journalist's directives are unchanged under `Behavior`.
- Reviewer rules are unchanged. The donor had no `rejection_patterns`, so
  `doctor` warns about this. Adding patterns is a domain decision for the owner.

## Not ported

- All KB binaries: 10 PDF and 1 DOCX. Their reuse rights are third-party or
  not recorded.
- `BioEthos/KB/*.md`: verbatim copies of FDA, NIH and European Commission web
  pages. They are third-party content and not covered by the owner's grant.
  They are listed in `SOURCES.md`.
- `DrNexus/KB/chatGPT-plan.md`: an AI-generated article plan, not reviewed knowledge.
- `Article_Draft.md`, `Drafts/fda_alternative_methods_nams_news_summary.md`,
  `Phase_1_Blueprint.md`, `Drafting_Tasks.md` and
  `Knowledge_Traceability_Matrix.md`: unreviewed run outputs and planning notes.
- `schemas/mms_schema_v1.jsonld`: an older MNMS core draft that the template
  judged vestigial.
- The donor's code, Docker, Makefile and root docs: superseded by this repository.
