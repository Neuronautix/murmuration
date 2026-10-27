# Example: TOCSAS — Toxicology Scientific Agent Squad

A nine-persona team for preclinical toxicology metadata harmonization, MNMS
alignment, provenance-aware evidence synthesis, and knowledge-graph design.

## Origin and licence

Ported from `dhuzard/PIU-Psych-Swarm` PR #10 (branch `claude/tocsas-toxicology-squad-yfNBy`),
commit `2ce8cb89727a30f65758008ce21724cb65d2d0ea`. The repository owner licensed
this content under this repository's MIT licence on 2026-10-26.

The personas, KB notes and schema are AI-assisted drafts (the donor commit was
authored with Claude). They are not scientifically validated or reviewed.
Treat all outputs, MNMS mappings and the extension schema as proposals.

## The team

| Agent | Role |
| :--- | :--- |
| TOCSAS Dr. Nexus | Orchestrator: scope, sequencing, conservative integration, traceability |
| TOCSAS LitScout | Literature cartographer: OECD/ICH guidance, INHAND, STP papers |
| TOCSAS Toxicology Lead | Conservative interpretation: adaptive vs. adverse |
| TOCSAS Exposure & Design Specialist | Dose, route, schedule, formulation, vehicle, toxicokinetics |
| TOCSAS Pathology & Biomarker Analyst | Clinical pathology, organ weights, histopathology, biomarkers |
| TOCSAS Metadata Architect | MNMS core vs. extension vs. ambiguous vs. missing |
| TOCSAS Knowledge Graph Engineer | Graph-ready structures with provenance links |
| TOCSAS Bias & Comparability Auditor | Comparability, missing metadata, unsupported mechanistic language |
| TOCSAS Journalist | Sections A–J with citation fidelity |

`schemas/tocsas_mnms_tox_extension_v1.jsonld` is the donor's draft MNMS
toxicology extension (about 60 fields aligned to ChEBI, UBERON, NCIt, EFO and
PROV-O). Its `tocsas:` namespace now points to this file's location in this
repository. Its `mms:` namespace still points to the MNMS core schema in the
donor repository, which this repository does not ship.

## How to use

Persona paths in `swarm_config.yml` are relative to the repository root.
`make ingest` reads KB files from `agents/<folder>/KB/`. From the repository root:

```bash
cp examples/toxicology/swarm_config.yml .
cp -r examples/toxicology/agents/TOCSAS_* agents/
make doctor
make ingest
make run PROMPT="Map the metadata needed to compare 90-day rat oral toxicity studies of a hepatotoxicant."
```

A live run needs the usual model and search credentials. Offline configuration
checks are in `tests/test_example_packs.py`.

## Adaptations from the donor

- `swarm_config_tocsas.yml` became `swarm_config.yml`, and persona paths now
  point to `./examples/toxicology/agents/`.
- The traceability matrix path is `./Knowledge_Traceability_Matrix_TOCSAS.md`.
- In-text references to `swarm_config_tocsas.yml` in the Dr. Nexus and
  Journalist personas now name `examples/toxicology/swarm_config.yml`.
- `output_sections` is read by the `journalist.brief` template in
  `swarm_config.yml`, which renders the section list into the Journalist's task
  message; the personas and the reviewer rules reinforce it.
- Each `KB/README.md` is kept as written. Some of them list planned KB files
  that the donor never created. Only the files present here exist.

## Not ported

- `TOCSAS_Knowledge_Traceability_Matrix.md` is a seeded run artefact whose
  entries were generated, not reviewed. The runtime creates a fresh matrix
  header on the first run.
- `schemas/mms_schema_v1.jsonld` (MNMS core) was not part of PR #10. The
  template judged it vestigial (PIU-Psych-Swarm PR #11). The Metadata
  Architect KB still describes it.
